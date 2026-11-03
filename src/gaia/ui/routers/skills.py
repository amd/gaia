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


# ---------------------------------------------------------------------------
# Per-skill preferences (gaia.skills.preferences) — local to this machine.
# ---------------------------------------------------------------------------


def _preference_store():
    from gaia.skills.preferences import SkillPreferenceStore

    return SkillPreferenceStore()


def _preference_schema(name: str) -> Any:
    """The skill's ``preferences.schema.json``, or None when it ships none.

    Writes are validated against it, so the skill has to be found here: the
    UI does not save a preference it cannot check.
    """
    from gaia.skills.errors import SkillError
    from gaia.skills.manager import SkillManager
    from gaia.skills.preferences import load_schema

    try:
        from gaia_agent.agent import GaiaAgent
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "The GAIA agent package is not installed beside the UI, so the "
                f"preference schema of '{name}' cannot be read to check the change."
            ),
        ) from e
    try:
        skill = SkillManager(agent_skill_dirs=GaiaAgent.SKILL_DIRS).load(name)
        return load_schema(skill.directory)
    except SkillError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/api/skills/preferences")
def list_skill_preferences() -> Dict[str, Any]:
    """Every skill with saved preferences."""
    from gaia.skills.errors import SkillError

    try:
        return {"skills": _preference_store().list()}
    except SkillError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/skills/{name}/preferences")
def get_skill_preferences(name: str) -> Dict[str, Any]:
    """One skill's saved preferences (``{}`` when none)."""
    from gaia.skills.errors import SkillError

    try:
        return {"skill": name, "preferences": _preference_store().get(name)}
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/api/skills/{name}/preferences")
def update_skill_preferences(name: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *changes* into a skill's preferences; a ``null`` value removes a key."""
    from gaia.skills.errors import SkillError

    schema = _preference_schema(name)
    try:
        saved = _preference_store().update(name, changes, schema=schema)
    except SkillError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"skill": name, "preferences": saved}


@router.delete("/api/skills/{name}/preferences")
def reset_skill_preferences(name: str) -> Dict[str, Any]:
    """Forget every saved preference of a skill."""
    from gaia.skills.errors import SkillError

    try:
        removed = _preference_store().reset(name)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"skill": name, "reset": removed}
