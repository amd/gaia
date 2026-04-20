# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""System-prompt composer for ``gaia-coder`` (§3.2, §4.6, §6.5).

Every LLM call ``gaia-coder`` makes ships the same three-document identity
prefix: ``GAIA.md`` (who she is), ``ARCHITECTURE.md`` (how she is composed),
and ``PROJECT_MAP.md`` (what she is building). These three files are:

1. **In a fixed order.** §3.2's component diagram places ``GAIA.md`` first
   (stable kernel), ``ARCHITECTURE.md`` second (her composition), then
   ``PROJECT_MAP.md`` (the project map). Downstream prompt templates in
   §15.8 inject individual sections out of these files in that same order.

2. **Cacheable.** §3.1 / §6.6 mandate Anthropic prompt caching; identity
   docs are the ideal cache-prefix material because they mutate rarely
   (``GAIA.md``: prompt-class self-fix only), re-read every turn, and fit
   comfortably inside the 4-block cache ceiling.

3. **Followed by matched skills** (§4.7). Skills are context-loaded rules
   that only apply on some turns; they live *after* the identity prefix
   and have their own per-skill cache_control so the identity block's
   cache hit is preserved when the skill set changes.

This module exposes:

* :class:`LoopContext` — the minimal bag of information the composer
  needs from the loop (the content roots, the current turn's skill-match
  context). Kept deliberately small; the composer does not need the full
  loop state.
* :class:`MessageBlock` — a typed Anthropic-format content block
  (``{"type":"text","text":...,"cache_control":{"type":"ephemeral"}}``).
* :func:`compose_system_prompt` — returns a list of :class:`MessageBlock`
  items ready to hand to ``anthropic.Anthropic().messages.create()``.

The composer is deliberately offline: it reads files, builds dicts, no
Anthropic SDK required. Tests and callers pass in the file paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, TypedDict

# ---------------------------------------------------------------------------
# Default identity-doc locations (relative to this file)
# ---------------------------------------------------------------------------

#: Directory containing ``GAIA.md`` / ``ARCHITECTURE.md`` / ``PROJECT_MAP.md``.
#: Resolved relative to this module so the composer works regardless of the
#: caller's CWD. Tests override this by passing explicit paths into
#: :func:`compose_system_prompt`.
_DEFAULT_IDENTITY_ROOT: Path = Path(__file__).parent

IDENTITY_DOCS: tuple[str, ...] = (
    "GAIA.md",
    "ARCHITECTURE.md",
    "PROJECT_MAP.md",
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class CacheControl(TypedDict):
    """Anthropic prompt-cache control block."""

    type: str  # "ephemeral" is the only v1 value


class MessageBlock(TypedDict, total=False):
    """Anthropic content block shape.

    We emit ``type`` and ``text`` on every block. ``cache_control`` is set
    on blocks we want cached; omitted otherwise so the v1 default (not
    cached) applies. This matches the shape
    ``anthropic.Anthropic().messages.create()`` expects for
    ``system=[...blocks]`` and ``messages=[{role, content:[...blocks]}]``.
    """

    type: str
    text: str
    cache_control: CacheControl


@dataclass
class LoopContext:
    """The slice of loop state the prompt composer needs.

    Kept tiny on purpose: the composer should not have a reason to peek at
    the full tool registry, audit log, or memory. If it needs more state
    than what's here, that's a signal to surface that state through this
    dataclass rather than have the composer reach into the loop directly.

    Attributes:
        identity_root: Directory containing the three identity docs.
            Defaults to :data:`_DEFAULT_IDENTITY_ROOT` (i.e. this package's
            directory) so a running agent picks up the docs bundled with
            her source tree.
        skill_paths: Ordered iterable of ``Path`` objects pointing at
            skill markdown files that matched the current turn's context
            (§4.7 loading algorithm step 3). The composer loads and
            cache-keys each skill independently so the identity prefix's
            cache hit is preserved when the skill set changes between
            turns.
        extra_suffix: Free-form extra system-prompt text the caller wants
            appended after skills but before the per-turn user/assistant
            messages. Useful for dynamic facts ("current tier: 3", "dev
            mode: ON") that would otherwise require mutating the identity
            docs on every turn.
    """

    identity_root: Path = field(default_factory=lambda: _DEFAULT_IDENTITY_ROOT)
    skill_paths: list[Path] = field(default_factory=list)
    extra_suffix: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_or_raise(path: Path) -> str:
    """Read *path* or raise a descriptive ``FileNotFoundError``.

    The three identity docs are load-bearing — the agent should never start
    a turn without them — so a missing file is a surfacing-worthy error,
    not a reason to silently degrade. Follows the repo's fail-loudly rule.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"gaia-coder identity document missing: {path}. "
            "These files ship with the package (src/gaia/coder/*.md); a "
            "missing file indicates a broken install or an accidental delete."
        )
    return path.read_text(encoding="utf-8")


def _identity_block(label: str, body: str) -> MessageBlock:
    """Wrap an identity doc in XML tags the downstream prompts reference.

    §15.8 templates refer to ``<gaia_md_…>``, ``<architecture_md>``,
    ``<project_map_md>`` as cacheable prefix segments. Using XML tags
    (rather than plain concatenation) gives the model a clean delimiter
    between documents and makes prompt-replay debugging easy.
    """
    tag = label.lower().replace(".md", "").replace("_", "_")
    # Prefer the §15.8 tag names exactly — downstream prompts assume them.
    tag_map = {
        "gaia.md": "gaia_md",
        "architecture.md": "architecture_md",
        "project_map.md": "project_map_md",
    }
    xml_tag = tag_map.get(label.lower(), tag)
    wrapped = f"<{xml_tag}>\n{body.rstrip()}\n</{xml_tag}>"
    return {
        "type": "text",
        "text": wrapped,
        "cache_control": {"type": "ephemeral"},
    }


def _skill_block(path: Path) -> MessageBlock:
    """Load a skill file and render it as a cacheable content block.

    Each skill is cached independently (§4.7 step 4: "Skills are
    cache-friendly **per skill** — the Anthropic cache can key on each
    skill's content independently"). The XML tag embeds the skill's stem
    so downstream prompts can reference it by name if needed.
    """
    body = _read_or_raise(path)
    return {
        "type": "text",
        "text": f'<skill name="{path.stem}">\n{body.rstrip()}\n</skill>',
        "cache_control": {"type": "ephemeral"},
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compose_system_prompt(
    context: LoopContext,
    matched_skills: Optional[Iterable[str]] = None,
) -> list[MessageBlock]:
    """Build the system-prompt block list for one LLM call.

    Args:
        context: :class:`LoopContext` providing the identity-doc root and
            (optionally) pre-resolved skill paths. When
            ``context.skill_paths`` is non-empty it is used directly; when
            it is empty and *matched_skills* is provided, the composer
            resolves each name to ``<identity_root>/skills/<name>.md``.
        matched_skills: Optional iterable of skill *names* (without ``.md``)
            to load. Ignored when ``context.skill_paths`` already lists the
            paths. Kept as a convenience so callers can pass names from
            :mod:`gaia.coder.skills.catalog` without pre-resolving paths.

    Returns:
        Ordered list of :class:`MessageBlock` items, suitable for the
        ``system`` parameter of ``anthropic.messages.create()``. Order:

        1. ``GAIA.md`` (cacheable)
        2. ``ARCHITECTURE.md`` (cacheable)
        3. ``PROJECT_MAP.md`` (cacheable)
        4. One block per matched skill, each cacheable
        5. Optional ``extra_suffix`` as a non-cacheable tail block

    Raises:
        FileNotFoundError: if any identity doc or referenced skill path is
            missing. Identity docs are non-negotiable; a missing skill
            file is always an authoring bug (catalog references a
            non-existent file).
    """
    blocks: list[MessageBlock] = []

    for filename in IDENTITY_DOCS:
        path = context.identity_root / filename
        body = _read_or_raise(path)
        blocks.append(_identity_block(filename, body))

    # Resolve skill paths: explicit paths win; otherwise resolve names.
    skill_paths: list[Path] = list(context.skill_paths or [])
    if not skill_paths and matched_skills:
        skills_dir = context.identity_root / "skills"
        skill_paths = [skills_dir / f"{name}.md" for name in matched_skills]

    for path in skill_paths:
        blocks.append(_skill_block(path))

    if context.extra_suffix:
        # Per-turn facts go un-cached — they change every turn, so caching
        # them would thrash the cache without benefit. Keeping them in a
        # separate block (rather than concatenating onto the last identity
        # doc) preserves the identity block's cache key.
        blocks.append({"type": "text", "text": context.extra_suffix})

    return blocks


def introspect_system_prompt(
    context: Optional[LoopContext] = None,
    matched_skills: Optional[Iterable[str]] = None,
) -> str:
    """Return the composed prompt as a single plain-text string (for debugging).

    Not used in LLM calls — for ``gaia-coder introspect system_prompt`` and
    for test assertions that care about the textual content, not the block
    boundaries.
    """
    ctx = context or LoopContext()
    blocks = compose_system_prompt(ctx, matched_skills)
    return "\n\n".join(block["text"] for block in blocks)
