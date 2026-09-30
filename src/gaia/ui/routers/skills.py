# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The skills the GAIA agent can load, for the Agent UI's Skills settings.

Discovered with the same roots the flagship agent uses (its bundled skills,
``~/.gaia/skills``, Claude Code skill folders), so the list matches what
``list_skills`` shows the model.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["skills"])


@router.get("/api/skills")
def list_skills() -> Dict[str, Any]:
    """Installed skills, and any folder that failed to parse."""
    from gaia.skills import SkillManager
    from gaia.skills.errors import SkillError

    try:
        from gaia_agent.agent import ENGINEERING_SKILL, GaiaAgent
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                f"The GAIA agent package is not installed ({e}), so its skills "
                "cannot be listed. Run `gaia init` to install it."
            ),
        ) from e

    manager = SkillManager(
        agent_skill_dirs=list(GaiaAgent.SKILL_DIRS),
        excluded_names=(ENGINEERING_SKILL,),
    )
    try:
        discovered = manager.reload()
    except SkillError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
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
