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
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skills"])


@router.get("/api/skills")
def list_skills() -> Dict[str, Any]:
    """Installed skills, and any folder that failed to parse."""
    from gaia.skills.errors import SkillError
    from gaia.skills.listing import skills_listing_for_agent

    try:
        from gaia_agent.agent import ENGINEERING_SKILL, GaiaAgent
    except ImportError:
        return _skills_from_agent()
    try:
        return skills_listing_for_agent(GaiaAgent, (ENGINEERING_SKILL,))
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
