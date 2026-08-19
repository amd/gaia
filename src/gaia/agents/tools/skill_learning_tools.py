# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Let the agent improve a loaded skill from what it learned (#2674).

One model-facing tool, ``remember_skill_lesson``. It writes to the **learned
overlay** in agent memory; the authored ``SKILL.md`` on disk is never touched.

Three deliberate limits, each closing a way this could go wrong:

* **Replace only, never remove.** The model may correct a command or rewrite a
  procedure that does not fit; it may not delete a section. Removal is a human
  action through ``gaia skill deltas`` — otherwise a confidently wrong model
  could quietly drop the rules that constrain it.
* **User consent, not model judgement.** The tool is in the flagship's
  ``CONFIRMATION_REQUIRED_TOOLS``, so the TUI shows the change and blocks until
  the user answers. Reaching this tool's body means the user already said yes.
* **Bounded.** A lesson that would make the resolved skill materially larger
  than the authored one is refused at write time with the reason, rather than
  silently trimmed later. Learning is not allowed to become a prompt tax — the
  case that motivated this feature was a skill hand-patched 48% larger.

Kept out of :mod:`gaia.agents.tools.skill_library_tools` on purpose: that mixin
is composed by every agent that wants skills, and one more always-registered
tool schema costs prompt tokens for agents that will never learn. This one is
opt-in as ``KNOWN_TOOLS["skill_learning"]``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

#: Canonical names, so callers configure the set without importing the mixin.
SKILL_LEARNING_TOOL_NAMES = ("remember_skill_lesson",)


def _failure(message: str, **extra: Any) -> Dict[str, Any]:
    """A refusal the model can act on, message intact."""
    out: Dict[str, Any] = {"status": "error", "message": message}
    out.update(extra)
    return out


class SkillLearningToolsMixin:
    """Adds ``remember_skill_lesson`` — the write half of adaptive skills.

    Compose onto an agent and call :meth:`register_skill_learning_tools` from
    ``_register_tools``, before ``super()._register_tools()`` so the tool is in
    the registry snapshot that reaches the composed prompt.
    """

    def register_skill_learning_tools(self) -> None:
        """Register the skill-learning tool onto this agent."""
        from gaia.agents.base.tools import tool

        agent = self

        @tool
        def remember_skill_lesson(
            skill: str,
            section: str,
            corrected_text: str,
            replaces: str = "",
            reason: str = "",
        ) -> dict:
            """Fix a loaded skill's instructions when the user says they are wrong.

            For a command that fails on this machine, or a procedure written
            around a workflow the user does not follow. Saved locally and applied
            only after they approve; the shipped skill file is never changed.

            Not for facts, notes about this task, or one undiagnosed failure —
            only for something that will still be true next time.

            Args:
                skill: A loaded skill's name.
                section: Heading slug to change, e.g. "procedure". A wrong value
                    is refused with the valid ones.
                corrected_text: The replacement text. Cannot be empty.
                replaces: Exact text to swap out, quoted verbatim. Prefer this —
                    leave empty only to rewrite the whole section.
                reason: One sentence on what was wrong, shown to the user.

            Returns:
                The stored change and the skill's new prompt-token cost.
            """
            from gaia.agents.base.skill_deltas import (
                KIND_REPLACE_SECTION,
                KIND_REPLACE_SNIPPET,
                DeltaRefused,
                SkillDelta,
                resolve_skill_body,
                supersession_key,
                validate_delta,
            )
            from gaia.skills.sections import find_section, parse_sections

            loaded = getattr(agent, "loaded_skills", None) or {}
            target = loaded.get(skill)
            if target is None:
                return _failure(
                    f"No skill named {skill!r} is loaded. "
                    f"Loaded now: {', '.join(sorted(loaded)) or '(none)'}. "
                    "Load it first with load_skill.",
                    loaded=sorted(loaded),
                )

            store = getattr(agent, "_memory_store", None)
            if store is None:
                return _failure(
                    "Memory is unavailable, so a lesson cannot be saved. It "
                    "would be forgotten at the end of this session, so nothing "
                    "was stored."
                )
            if getattr(agent, "_incognito", False):
                return _failure(
                    "This is a private session, so nothing is saved to memory. "
                    "Ask the user to repeat the correction in a normal session "
                    "if they want it to stick."
                )

            # An empty replacement is a deletion wearing a rewrite's clothes:
            # it would blank the section while reporting "corrected". Removal
            # stays a human action, so refuse it here rather than let the model
            # quietly strip the rules that constrain it.
            if not corrected_text.strip():
                return _failure(
                    "corrected_text is empty, which would delete the "
                    f"{section!r} section rather than correct it. Pass the "
                    "replacement text. To remove a section entirely, the user "
                    f"does that themselves with `gaia skill deltas {skill} "
                    "--drop-section <name>`."
                )

            base_body = target.body or ""
            sections = parse_sections(base_body)
            anchored = find_section(sections, section)
            if anchored is None:
                return _failure(
                    f"{skill!r} has no section {section!r}. "
                    f"Valid sections: {', '.join(s.slug for s in sections)}.",
                    valid_sections=[s.slug for s in sections],
                )

            kind = KIND_REPLACE_SNIPPET if replaces else KIND_REPLACE_SECTION
            payload = (
                {"old": replaces, "new": corrected_text}
                if replaces
                else {"body": corrected_text}
            )
            scope = agent.learned_skill_scope()

            existing_rows = store.search_deltas(
                base_name=skill, scope=scope, status="active"
            )
            existing = [_row_to_delta(r) for r in existing_rows]

            candidate = SkillDelta(
                id="candidate",
                base_name=skill,
                scope=scope,
                kind=kind,
                anchor_section=section,
                anchor_digest=anchored.digest,
                payload=payload,
                provenance={
                    "source": "user_instruction",
                    "reason": reason,
                    "skill_version": getattr(target, "version", None),
                },
            )

            # Supersede rather than stack: a revised correction to the same
            # target retires the earlier one, so N corrections cost what one
            # costs. This is what keeps the prompt flat as the agent learns.
            key = supersession_key(candidate)
            superseded = [d for d in existing if supersession_key(d) == key]
            survivors = [d for d in existing if supersession_key(d) != key]

            try:
                validate_delta(base_body, candidate, existing=survivors)
            except DeltaRefused as exc:
                return _failure(str(exc))

            delta_id = store.put_delta(
                base_name=skill,
                scope=scope,
                kind=kind,
                anchor_section=section,
                anchor_digest=anchored.digest,
                payload=payload,
                provenance=candidate.provenance,
                base_root=getattr(target, "root", None),
                base_version=getattr(target, "version", None),
            )
            # Reaching this tool's body means the confirmation gate already
            # passed, so the user's consent is on record — activate it.
            store.approve_delta(delta_id)
            for old in superseded:
                store.supersede_delta(old.id, delta_id)

            # Drop the cached resolution so the correction applies from the next
            # turn. Safe for the KV cache: get_skills_system_prompt is declared
            # volatile, so this fragment already renders after the stable head.
            cache = getattr(agent, "_effective_skill_cache", None)
            if cache is not None:
                cache.clear()
            if hasattr(agent, "rebuild_system_prompt"):
                try:
                    agent.rebuild_system_prompt()
                except Exception:  # noqa: BLE001 - never fail the turn on a rebuild
                    logger.debug("rebuild_system_prompt failed", exc_info=True)

            refreshed = [
                _row_to_delta(r)
                for r in store.search_deltas(
                    base_name=skill, scope=scope, status="active"
                )
            ]
            resolved = resolve_skill_body(base_body, refreshed)

            logger.info(
                "[SkillLearning] %s/%s corrected (%s), delta=%s, %+d tokens",
                skill,
                section,
                kind,
                delta_id,
                resolved.token_delta,
            )
            return {
                "status": "success",
                "skill": skill,
                "section": section,
                "change": kind,
                "delta_id": delta_id,
                "superseded": [d.id for d in superseded],
                "token_delta_vs_authored": resolved.token_delta,
                "learned_changes_on_this_skill": len(resolved.applied),
                "message": (
                    f"Saved. {skill!r} now uses the corrected {section!r} on this "
                    f"machine ({resolved.token_delta:+d} prompt tokens vs the "
                    "shipped skill). The shipped file is unchanged; "
                    f"`gaia skill deltas {skill}` shows or reverts it."
                ),
            }

        self._skill_learning_tools_registered = True


def _row_to_delta(row: dict):
    """Map a ``skill_deltas`` row to the resolver's dataclass."""
    from gaia.agents.base.skill_deltas import SkillDelta

    return SkillDelta(
        id=row["id"],
        base_name=row["base_name"],
        scope=row["scope"],
        kind=row["kind"],
        anchor_section=row["anchor_section"],
        anchor_digest=row["anchor_digest"],
        payload=row["payload"],
        provenance=row["provenance"],
        status=row["status"],
        superseded_by=row["superseded_by"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
    )
