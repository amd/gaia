# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The learned overlay: an authored skill, adapted by what the agent learned.

The authored ``SKILL.md`` on disk is **immutable**. What an agent learns about a
skill is stored as typed rows in agent memory and composed over the base at
render time, so the effective skill can change without the shipped file ever
being written.

Why the deltas are *substitutive*, not additive
-----------------------------------------------
The motivating case was a skill that did not fit: ``github-triage`` is written
around a repository backlog, and the user wanted their own inbox. Closing that
gap by hand grew the file 4,103 → 6,078 bytes — a 48% permanent prompt-tax on
every turn, for a skill that was no better matched than before, just longer.

Appending learned prose reproduces that failure (measured: +36%). Replacing the
section that does not fit does not (measured: −7%; −19% once a section the user
never exercises is dropped). So the grammar here can **replace and remove**, and
the only thing it can do additively is bounded by the budget below.

That also makes repeated learning safe. Corrections to the same section
supersede one another instead of stacking, so N corrections cost what one costs:
the resolved size is a function of the *base*, not of how much has been learned.

What a delta can never do
-------------------------
It carries no permissions and touches no frontmatter — resolution rewrites
``Skill.body`` and nothing else, so ``security_tier`` and ``permissions`` are
structurally out of reach. It cannot add a tool: ``register_skill_tools`` drops
names absent from the live registry, and prose reaching the model is only ever
text. And it cannot change *when* a skill activates, because
``SkillLoader._doc_text`` embeds a skill's name and description, never its body.

Off-states are conservative everywhere: any condition this module cannot resolve
cleanly leaves the **authored** text in place and records a note. Nothing here
may raise into :meth:`Agent.get_skills_system_prompt` — a fragment that raises is
dropped from the composed prompt with only a warning, which would silently take
every loaded skill's instructions with it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from gaia.skills.sections import Section, parse_sections, render_sections

logger = logging.getLogger(__name__)

# --- The v1 grammar -------------------------------------------------------

#: Swap a whole named section's text. The shape-adaptation kind.
KIND_REPLACE_SECTION = "replace_section"
#: Swap an exact substring inside a named section. The surgical kind — a wrong
#: command string costs a ~20-token payload instead of a whole section.
KIND_REPLACE_SNIPPET = "replace_snippet"
#: Remove a named section outright. The only kind with negative token cost.
KIND_DROP_SECTION = "drop_section"

KNOWN_KINDS = frozenset({KIND_REPLACE_SECTION, KIND_REPLACE_SNIPPET, KIND_DROP_SECTION})

#: Only a delta the user actually approved participates in resolution.
STATUS_STAGED = "staged"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_ORPHANED = "orphaned"

#: Ceiling on how much bigger a resolved skill may be than its authored base,
#: in estimated tokens. Deliberately small: substitutive learning should trend
#: *down*, so needing headroom at all is the exception. Enforced when a delta is
#: approved — never by truncating at render, which would make the prompt
#: non-deterministic and hide the loss.
MAX_OVERLAY_TOKENS = 120

#: Cap on one delta's stored payload, in characters. Bounds a single pathological
#: write; the real bound on *resident* cost is MAX_OVERLAY_TOKENS above.
MAX_PAYLOAD_CHARS = 2000

#: Matches ``skill_library_tools.estimate_prompt_tokens`` (4 chars ≈ 1 token).
#: Verified against tiktoken cl100k on the shipped skill corpus: 3.97 vs 4.00
#: chars/token, i.e. within 1%. Kept local so this module stays importable
#: without pulling in the agent tool stack.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate prompt tokens for *text*."""
    return len(text) // _CHARS_PER_TOKEN


class DeltaRefused(ValueError):
    """A proposed delta was refused at write time. Never stored, never staged."""


@dataclass(frozen=True)
class SkillDelta:
    """One learned adjustment attached to a section of an authored skill."""

    id: str
    base_name: str
    scope: str
    kind: str
    anchor_section: str
    anchor_digest: str
    payload: Dict[str, Any]
    provenance: Dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_STAGED
    superseded_by: Optional[str] = None
    created_at: str = ""
    approved_at: Optional[str] = None

    @property
    def is_resolvable(self) -> bool:
        """Approved, not retired, not superseded."""
        return self.status == STATUS_ACTIVE and not self.superseded_by


@dataclass
class ResolutionNote:
    """Why one delta did or did not apply. The material for `gaia skill deltas`."""

    delta_id: str
    section: str
    # applied | orphaned | stale | snippet_not_found | unknown_kind | superseded
    outcome: str
    detail: str = ""


@dataclass
class ResolvedSkill:
    """An authored body with its approved overlay composed in."""

    body: str
    base_body: str
    notes: List[ResolutionNote] = field(default_factory=list)
    applied: List[str] = field(default_factory=list)

    @property
    def base_tokens(self) -> int:
        return estimate_tokens(self.base_body)

    @property
    def resolved_tokens(self) -> int:
        return estimate_tokens(self.body)

    @property
    def token_delta(self) -> int:
        """Resident cost of the overlay. Negative when learning made it cheaper."""
        return self.resolved_tokens - self.base_tokens

    @property
    def is_overlaid(self) -> bool:
        return bool(self.applied)


def _sort_key(delta: SkillDelta) -> tuple:
    """Deterministic order: two sessions with the same deltas render identically."""
    return (delta.created_at, delta.id)


def resolve_skill_body(
    base_body: str,
    deltas: Sequence[SkillDelta],
    *,
    enabled: bool = True,
) -> ResolvedSkill:
    """Compose approved *deltas* over *base_body*.

    With ``enabled=False`` — the ``--no-learned-skills`` off-switch — this
    short-circuits before touching a single delta and returns the base
    byte-for-byte, so the composed prompt is identical to a no-overlay build.

    Never raises. Any delta that cannot be applied cleanly is skipped with a
    note and the authored text stands.
    """
    if not enabled or not deltas:
        return ResolvedSkill(body=base_body, base_body=base_body)

    notes: List[ResolutionNote] = []
    applied: List[str] = []

    resolvable = sorted((d for d in deltas if d.is_resolvable), key=_sort_key)
    if not resolvable:
        return ResolvedSkill(body=base_body, base_body=base_body)

    sections = parse_sections(base_body)
    by_slug = {s.slug: s for s in sections}

    # Bucket per section so precedence is decided within a section, not globally.
    buckets: Dict[str, List[SkillDelta]] = {}
    for delta in resolvable:
        if delta.kind not in KNOWN_KINDS:
            # Never guess at an unknown payload shape — skip it loudly.
            notes.append(
                ResolutionNote(
                    delta.id,
                    delta.anchor_section,
                    "unknown_kind",
                    f"unknown delta kind {delta.kind!r}; skipped",
                )
            )
            logger.warning(
                "[SkillDeltas] delta %s has unknown kind %r — skipped",
                delta.id,
                delta.kind,
            )
            continue
        if delta.anchor_section not in by_slug:
            notes.append(
                ResolutionNote(
                    delta.id,
                    delta.anchor_section,
                    "orphaned",
                    "the section it was attached to is not in the current base",
                )
            )
            continue
        buckets.setdefault(delta.anchor_section, []).append(delta)

    out: List[Section] = []
    for section in sections:
        bucket = buckets.get(section.slug, [])
        if not bucket:
            out.append(section)
            continue
        text, dropped = _apply_bucket(section, bucket, notes, applied)
        if not dropped:
            out.append(Section(section.slug, section.level, section.heading, text))

    return ResolvedSkill(
        body=render_sections(out),
        base_body=base_body,
        notes=notes,
        applied=applied,
    )


def _apply_bucket(
    section: Section,
    bucket: List[SkillDelta],
    notes: List[ResolutionNote],
    applied: List[str],
) -> tuple[str, bool]:
    """Apply one section's deltas. Returns ``(text, dropped)``."""
    live_digest = section.digest

    # A drop wins over everything else attached to the same section.
    drops = [d for d in bucket if d.kind == KIND_DROP_SECTION]
    if drops:
        drop = drops[-1]
        if drop.anchor_digest != live_digest:
            # Removing text that changed since the user approved the removal is
            # exactly the silent-wrong-answer case. Keep the authored section.
            notes.append(
                ResolutionNote(
                    drop.id,
                    section.slug,
                    "stale",
                    "the section changed since this removal was approved; "
                    "authored text kept — re-approve to drop it again",
                )
            )
            return section.text, False
        notes.append(
            ResolutionNote(drop.id, section.slug, "applied", "section removed")
        )
        applied.append(drop.id)
        return "", True

    text = section.text

    # Whole-section replacement: latest wins, earlier ones are dead weight the
    # store should have superseded, so say so rather than applying them in turn.
    replacements = [d for d in bucket if d.kind == KIND_REPLACE_SECTION]
    if replacements:
        winner = replacements[-1]
        for loser in replacements[:-1]:
            notes.append(
                ResolutionNote(
                    loser.id,
                    section.slug,
                    "superseded",
                    f"a later replacement ({winner.id}) covers this section",
                )
            )
        if winner.anchor_digest != live_digest:
            notes.append(
                ResolutionNote(
                    winner.id,
                    section.slug,
                    "stale",
                    "the authored section changed since this was approved; "
                    "authored text kept — re-approve to apply it to the new text",
                )
            )
        else:
            text = str(winner.payload.get("body", ""))
            notes.append(
                ResolutionNote(winner.id, section.slug, "applied", "section replaced")
            )
            applied.append(winner.id)

    # Snippet replacements apply on top, oldest first. A verbatim match is its
    # own anchor, so these survive a digest change that whole-section edits do
    # not — but a miss is reported, never approximated.
    for delta in [d for d in bucket if d.kind == KIND_REPLACE_SNIPPET]:
        old = str(delta.payload.get("old", ""))
        new = str(delta.payload.get("new", ""))
        if not old or old not in text:
            notes.append(
                ResolutionNote(
                    delta.id,
                    section.slug,
                    "snippet_not_found",
                    "the text this replaced is no longer present; nothing changed",
                )
            )
            continue
        text = text.replace(old, new)
        notes.append(
            ResolutionNote(delta.id, section.slug, "applied", "snippet replaced")
        )
        applied.append(delta.id)

    return text, False


# --- Write-time validation ------------------------------------------------


def validate_delta(
    base_body: str,
    delta: SkillDelta,
    *,
    existing: Iterable[SkillDelta] = (),
) -> None:
    """Refuse a delta that must never be stored.

    Raises :class:`DeltaRefused` naming what failed, what to do, and where — the
    three things an actionable error owes the caller. A refused delta is not
    stored and not staged.
    """
    if delta.kind not in KNOWN_KINDS:
        raise DeltaRefused(
            f"unknown delta kind {delta.kind!r}. "
            f"Use one of: {', '.join(sorted(KNOWN_KINDS))}. "
            "See src/gaia/agents/base/skill_deltas.py for the v1 grammar."
        )

    source = str((delta.provenance or {}).get("source", ""))
    if source != "user_instruction":
        raise DeltaRefused(
            f"provenance {source or '(absent)'!r} is not accepted in v1 — only "
            "'user_instruction'. A lesson inferred from fetched or received "
            "content cannot be persisted until the injection analyzer (#2468) "
            "ships, because a persisted instruction outlives the turn it "
            "arrived in."
        )

    payload_size = sum(len(str(v)) for v in (delta.payload or {}).values())
    if payload_size > MAX_PAYLOAD_CHARS:
        raise DeltaRefused(
            f"delta payload is {payload_size} chars, over the "
            f"{MAX_PAYLOAD_CHARS}-char limit. Replace a smaller section, or use "
            f"{KIND_REPLACE_SNIPPET} to change just the part that is wrong."
        )

    sections = parse_sections(base_body)
    slugs = {s.slug for s in sections}
    if delta.anchor_section not in slugs:
        raise DeltaRefused(
            f"no section {delta.anchor_section!r} in this skill. "
            f"Available anchors: {', '.join(sorted(slugs)) or '(none)'}."
        )

    if delta.kind == KIND_REPLACE_SNIPPET:
        section = next(s for s in sections if s.slug == delta.anchor_section)
        old = str((delta.payload or {}).get("old", ""))
        if not old:
            raise DeltaRefused(
                f"{KIND_REPLACE_SNIPPET} needs a non-empty 'old' value — the "
                "exact text to replace."
            )
        if old not in section.text:
            raise DeltaRefused(
                f"the text to replace was not found verbatim in section "
                f"{delta.anchor_section!r}. Quote it exactly as it appears in "
                "the skill, whitespace included."
            )

    # Budget is a property of the RESOLVED skill, so check the candidate against
    # the deltas already approved rather than in isolation.
    candidate = list(existing) + [delta]
    resolved = resolve_skill_body(base_body, [_as_active(d) for d in candidate])
    if resolved.token_delta > MAX_OVERLAY_TOKENS:
        raise DeltaRefused(
            f"this would make the skill {resolved.token_delta} tokens larger "
            f"than the authored version, over the {MAX_OVERLAY_TOKENS}-token "
            "ceiling that keeps learning from becoming a permanent prompt tax. "
            "Retire an existing learned change, or replace a section instead of "
            "adding to one (`gaia skill deltas <name>` lists what is stored)."
        )


def _as_active(delta: SkillDelta) -> SkillDelta:
    """A copy marked active, for previewing a staged delta's effect."""
    if delta.is_resolvable:
        return delta
    return SkillDelta(
        id=delta.id,
        base_name=delta.base_name,
        scope=delta.scope,
        kind=delta.kind,
        anchor_section=delta.anchor_section,
        anchor_digest=delta.anchor_digest,
        payload=delta.payload,
        provenance=delta.provenance,
        status=STATUS_ACTIVE,
        superseded_by=None,
        created_at=delta.created_at,
        approved_at=delta.approved_at,
    )


def preview_diff(base_body: str, deltas: Sequence[SkillDelta]) -> str:
    """A unified diff of authored vs effective. What the consent gate shows."""
    import difflib

    resolved = resolve_skill_body(base_body, [_as_active(d) for d in deltas])
    lines = difflib.unified_diff(
        base_body.splitlines(),
        resolved.body.splitlines(),
        fromfile="authored SKILL.md",
        tofile="effective (with learned changes)",
        lineterm="",
        n=2,
    )
    return "\n".join(lines)


def supersession_key(delta: SkillDelta) -> tuple:
    """Two deltas with the same key are the same lesson learned twice.

    Keyed on target, not content, so a *revised* correction to a section retires
    the earlier one instead of stacking with it. This is what keeps N
    corrections costing what one costs.
    """
    if delta.kind == KIND_REPLACE_SNIPPET:
        return (
            delta.base_name,
            delta.scope,
            delta.anchor_section,
            delta.kind,
            str((delta.payload or {}).get("old", "")),
        )
    return (delta.base_name, delta.scope, delta.anchor_section, delta.kind)
