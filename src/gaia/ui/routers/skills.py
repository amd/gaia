# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The skills the GAIA agent can load, for the Agent UI's Skills settings.

Discovered with the same roots the flagship agent uses (its bundled skills,
``~/.gaia/skills``, Claude Code skill folders), so the list matches what
``list_skills`` shows the model. When the agent is the Hub binary rather than a
package installed beside the UI, its bundled skills are inside that binary, so
the running agent is asked instead.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skills"])


@router.get("/api/skills")
def list_skills() -> Dict[str, Any]:
    """Installed skills, and any folder that failed to parse."""
    from gaia.skills.errors import SkillError
    from gaia.skills.listing import skills_listing

    try:
        from gaia_agent.agent import ENGINEERING_SKILL, GaiaAgent
    except ImportError:
        return _skills_from_agent()
    try:
        return skills_listing(GaiaAgent.SKILL_DIRS, (ENGINEERING_SKILL,))
    except SkillError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _skills_from_agent() -> Dict[str, Any]:
    """Ask the installed GAIA agent, through the daemon, which skills it has."""
    from gaia.ui.email_sidecar import daemon_client
    from gaia.ui.email_sidecar.errors import SidecarError
    from gaia.ui.email_sidecar.profiles import GAIA_PROFILE, skills_supported

    try:
        handle = daemon_client.acquire_handle(GAIA_PROFILE.agent_id)
    except SidecarError as e:
        raise HTTPException(
            status_code=503,
            detail=f"The GAIA agent could not be started to list its skills: {e}",
        ) from e
    if not skills_supported(GAIA_PROFILE, handle.api_version):
        logger.warning(
            "GAIA agent at contract %s can't list skills (needs %s)",
            handle.api_version,
            GAIA_PROFILE.min_skills_api_version,
        )
        raise HTTPException(status_code=503, detail=GAIA_PROFILE.skills_upgrade_message)
    try:
        return handle.proxy().skills()
    except SidecarError as e:
        raise HTTPException(
            status_code=502,
            detail=f"The GAIA agent could not list its skills: {e}",
        ) from e


class SkillLinkRequest(BaseModel):
    url: str
    #: Skill names to add; omitted adds every addable skill behind the link.
    names: Optional[List[str]] = None
    #: Add skills that ship code (tools.py or scripts/).
    allow_code: bool = False
    force: bool = False
    #: The preview's pin, so the add installs exactly what was reviewed.
    pin: Optional[str] = None


@router.get("/api/skills/featured")
def featured_skill_libraries() -> Dict[str, Any]:
    """Libraries the Skills panel offers as one-click adds."""
    from gaia.skills.link import FEATURED_LIBRARIES

    return {"libraries": FEATURED_LIBRARIES}


@router.post("/api/skills/link/preview")
def preview_skill_link(body: SkillLinkRequest) -> Dict[str, Any]:
    """List and audit the skills behind a pasted link. Writes nothing."""
    from gaia.skills.errors import SkillError
    from gaia.skills.link import SkillLinkError, preview_link

    try:
        return preview_link(body.url).to_dict()
    except SkillLinkError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except SkillError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/skills/link/add")
def add_skill_link(body: SkillLinkRequest) -> Dict[str, Any]:
    """Add the chosen skills behind a pasted link to ``~/.gaia/skills``."""
    from gaia.skills.errors import SkillError
    from gaia.skills.link import SkillLinkError, add_from_link

    try:
        return add_from_link(
            body.url,
            body.names,
            allow_code=body.allow_code,
            force=body.force,
            pin=body.pin,
        ).to_dict()
    except SkillLinkError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except SkillError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/api/skills/{name}")
def remove_skill(name: str) -> Dict[str, Any]:
    """Remove a skill from ``~/.gaia/skills``; bundled skills are refused."""
    from gaia.skills.errors import SkillError, SkillNotFoundError
    from gaia.skills.install import remove_skill as remove

    try:
        removed = remove(name)
    except SkillNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"removed": removed.name}
