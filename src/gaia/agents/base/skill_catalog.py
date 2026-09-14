# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The installed-skill catalogue the model reads, and which skill grants which CLI.

Every installed skill's name and description sit in the system prompt, and the
model calls ``load_skill`` when one fits the work — at the start of a task or
halfway through it. The model has read the whole request, so it is a better judge
of fit than a matcher scoring the user's words. The per-turn lexical matcher this
replaces loaded a skill on 0 of 24 benchmark tasks: the repo names, issue numbers
and output formats that make a request precise counted against every match (#3764).

The block depends only on what is installed, so it is identical from turn to turn
and stays in the cached head of the prompt.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict, List, Optional

from gaia.skills.manager import ROOT_CLAUDE_IMPORT

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.skills.format import Skill

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
    "Installed skills. Each one carries instructions for a kind of work, and some "
    "unlock a CLI (such as gh) that is refused until the skill is loaded. When the "
    "work matches a skill, call load_skill with its name before doing that work — "
    "also when you realise it partway through a task."
)


def render_catalog(skills: Dict[str, "Skill"]) -> str:
    """One line per listed skill, sorted by name; ``""`` when there are none.

    ``.claude/skills`` imports stay loadable by name but are not listed: they are
    another host's skills for working on a repo, not answers to a user's request.
    """
    lines = [
        f"- {skill.name}: {' '.join((skill.description or '').split())}"
        for skill in sorted(skills.values(), key=lambda s: s.name)
        if skill.root != ROOT_CLAUDE_IMPORT
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
    "CATALOG_ENV",
    "CATALOG_HEADER",
    "GROUNDING_RULE",
    "catalog_env_override",
    "render_catalog",
    "skills_granting",
]
