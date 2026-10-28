# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The skill list the Agent UI's Skills settings shows.

Shared by the Agent UI (when the agent package is installed beside it) and the
flagship sidecar's ``GET /v1/gaia/skills`` (when the agent is the Hub binary,
whose bundled skills live inside the executable), so both answer the same shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

from gaia.skills.manager import ROOT_AGENT_BUNDLED, ROOT_CLAUDE_IMPORT, ROOT_USER

#: Where a skill came from: the discovery-root labels the UI groups by.
SKILL_SOURCES = (ROOT_AGENT_BUNDLED, ROOT_USER, ROOT_CLAUDE_IMPORT)


def skills_listing(
    agent_skill_dirs: Iterable[str],
    excluded_names: Iterable[str] = (),
    *,
    always_on_names: Iterable[str] = (),
) -> Dict[str, Any]:
    """Installed skills, and any skill folder that failed to parse.

    Raises:
        gaia.skills.errors.SkillError: a skill root could not be read.
    """
    from gaia.skills import SkillManager

    manager = SkillManager(
        agent_skill_dirs=list(agent_skill_dirs),
        excluded_names=tuple(excluded_names),
    )
    discovered = manager.reload()
    always_on = frozenset(always_on_names)
    skills = [
        {
            "name": s.name,
            "description": s.description or "",
            "version": s.version or None,
            "tier": s.security_tier,
            "tools": list(s.tool_names),
            "path": s.root or "",
            "always_on": s.name in always_on,
            "source": s.root if s.root in SKILL_SOURCES else None,
        }
        for s in sorted(discovered.values(), key=lambda s: s.name)
    ]
    return {"skills": skills, "invalid": manager.discovery_errors}


def skills_listing_for_agent(
    agent_class: type, excluded_names: Iterable[str] = ()
) -> Dict[str, Any]:
    """List skills with the agent manifest's always-on distinction.

    Do not construct an agent merely to inspect its manifest: that would start
    runtime services just to render Settings. Use the same parser that the
    agent itself uses at startup, in both the installed and sidecar paths.
    """
    from gaia.agents.base.agent import parse_skill_manifest

    manifest = getattr(agent_class, "SKILL_MANIFEST", None)
    declarations = parse_skill_manifest(Path(manifest) if manifest else None)
    return skills_listing(
        agent_class.SKILL_DIRS,
        excluded_names,
        always_on_names=(ref.name for ref in declarations.always),
    )
