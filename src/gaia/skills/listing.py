# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The skill list the Agent UI's Skills settings shows.

Shared by the Agent UI (when the agent package is installed beside it) and the
flagship sidecar's ``GET /v1/gaia/skills`` (when the agent is the Hub binary,
whose bundled skills live inside the executable), so both answer the same shape.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable


def skills_listing(
    agent_skill_dirs: Iterable[str], excluded_names: Iterable[str] = ()
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
    skills = [
        {
            "name": s.name,
            "description": s.description or "",
            "version": s.version or None,
            "tier": s.security_tier,
            "tools": list(s.tool_names),
            "path": s.root or "",
        }
        for s in sorted(discovered.values(), key=lambda s: s.name)
    ]
    return {"skills": skills, "invalid": manager.discovery_errors}
