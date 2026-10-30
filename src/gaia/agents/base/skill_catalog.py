# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The installed-skill catalogue the model reads, and which skill grants which CLI.

Every installed skill's name and description sit in the system prompt, and the
model calls ``load_skill`` for each one that fits the work — at the start of a
task or halfway through it. Skills combine: a deck built from a folder of
documents needs both the documents skill and the deck skill. The model has read
the whole request, so it is a better judge of fit than a matcher scoring the
user's words. The per-turn lexical matcher this
replaces loaded a skill on 0 of 24 benchmark tasks: the repo names, issue numbers
and output formats that make a request precise counted against every match (#3764).

The block depends only on what is installed, so it is identical from turn to turn
and stays in the cached head of the prompt.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict, List, Optional

from gaia.logger import get_logger
from gaia.skills.manager import ROOT_CLAUDE_IMPORT

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.skills.format import Skill

log = get_logger(__name__)

#: Env override for the whole feature. Unset = on for agents that opt in.
CATALOG_ENV = "GAIA_SKILL_DISCOVERY"


def catalog_env_override() -> Optional[bool]:
    """Parse :data:`CATALOG_ENV`, or ``None`` when it is unset."""
    raw = os.getenv(CATALOG_ENV)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


#: Deliberately about sourcing, not skills: answering "what's in my github inbox?"
#: from memory happens exactly when no skill is loaded.
GROUNDING_RULE = (
    "==== SOURCING ====\n"
    "Facts about live external state — a repository, an inbox, a calendar, a web "
    "page, a file on disk — must come from a tool call in THIS turn. Memory, "
    "training, and earlier turns are not sources for them. If no registered tool "
    "can fetch what was asked, say you cannot and name what is missing. Never "
    "answer from recollection, and never present an example as real data."
)

CATALOG_HEADER = (
    "==== SKILLS ====\n"
    "Installed skills. Each carries instructions for a kind of work; some unlock "
    "a CLI (such as gh) that is refused until the skill is loaded. Skills combine: "
    "call load_skill for EVERY skill whose description matches any part of the "
    "request — what it reads (files, data, logs, notes, the web) and what it "
    "produces (a deck, workbook, document, report). When several match, load "
    "each one before starting, even if they overlap — also when you realise it "
    "partway through. A skill under LOADED SKILLS is loaded; do not load it again."
)

#: Above every starter-pack description (longest 443), so the cap only bites on
#: an outlier; the 1,024-char format limit would otherwise set the block's size.
CATALOG_DESCRIPTION_CHARS = 480

#: Companions named on a catalogue line. The note has its own budget so a
#: popular skill's description is never cut to make room; load_skill names all.
CATALOG_PAIRS_SHOWN = 3


def _catalog_description(description: str) -> str:
    text = " ".join((description or "").split())
    if len(text) <= CATALOG_DESCRIPTION_CHARS:
        return text
    return text[:CATALOG_DESCRIPTION_CHARS].rstrip() + "…"


def _listed(skill: "Skill") -> bool:
    return skill.root != ROOT_CLAUDE_IMPORT


def companions(skills: Dict[str, "Skill"], name: str) -> List[str]:
    """Listed skills that pair with *name*: its own declared order, then the rest.

    Pairing is mutual: a skill pairs with every name in its own
    ``metadata.gaia.pairs_with`` and with every skill that names it, so one side
    declaring the pair is enough. The skill's own list comes first so its author
    picks what the catalogue line shows; skills that name it follow, sorted.
    Names that are not installed are skipped — a skill may name a companion the
    user has not installed.
    """
    skill = skills.get(name)
    if skill is None:
        return []
    found: List[str] = []
    for other in skill.gaia.pairs_with:
        target = skills.get(other)
        if target is None or not _listed(target):
            log.debug(
                "Skill '%s' pairs with '%s', which is not installed; ignoring.",
                name,
                other,
            )
            continue
        if other != name and other not in found:
            found.append(other)
    found.extend(
        sorted(
            other.name
            for other in skills.values()
            if _listed(other)
            and name in other.gaia.pairs_with
            and other.name != name
            and other.name not in found
        )
    )
    return found


def _pairs_note(paired: List[str]) -> str:
    if not paired:
        return ""
    shown = ", ".join(paired[:CATALOG_PAIRS_SHOWN])
    hidden = len(paired) - CATALOG_PAIRS_SHOWN
    more = f", +{hidden} more" if hidden > 0 else ""
    return f" (pairs with: {shown}{more})"


def _catalog_line(skills: Dict[str, "Skill"], skill: "Skill") -> str:
    """``- name: description (pairs with: a, b, c, +N more)``."""
    note = _pairs_note(companions(skills, skill.name))
    return f"- {skill.name}: {_catalog_description(skill.description)}{note}"


def render_catalog(skills: Dict[str, "Skill"]) -> str:
    """One line per listed skill, sorted by name; ``""`` when there are none.

    The project's own ``.claude/skills`` are listed, as Claude Code lists them:
    they say how to work on the repo the agent was started in. Other
    ``.claude/skills`` imports (``~/.claude/skills``, or a working directory
    that is not a project) stay loadable by name but are not listed.
    """
    lines = [
        _catalog_line(skills, skill)
        for skill in sorted(skills.values(), key=lambda s: s.name)
        if _listed(skill)
    ]
    if not lines:
        return ""
    return CATALOG_HEADER + "\n" + "\n".join(lines)


def skills_granting(skills: Dict[str, "Skill"], binary: str) -> List[str]:
    """Names of the skills that declare ``shell:execute:<binary>``, sorted."""
    from gaia.skills.binaries import normalize_binary

    return sorted(
        skill.name
        for skill in skills.values()
        if any(
            grant.is_binary_bridged and normalize_binary(grant.scope) == binary
            for grant in skill.parsed_permissions()
        )
    )


__all__ = [
    "CATALOG_DESCRIPTION_CHARS",
    "CATALOG_ENV",
    "CATALOG_HEADER",
    "CATALOG_PAIRS_SHOWN",
    "GROUNDING_RULE",
    "catalog_env_override",
    "companions",
    "render_catalog",
    "skills_granting",
]
