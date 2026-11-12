# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agents GAIA used to ship and no longer does.

A leaf module, standard library only, so the installer, the daemon and its
clock can all name the same list.
"""

from typing import Optional

#: Nothing can install, start or update these any more.
RETIRED_AGENT_IDS = ("email",)

EMAIL_DOCS_URL = "https://amd-gaia.ai/docs/guides/email"

RETIRED_AGENT_JOB_MESSAGE = (
    "The email agent was removed from GAIA, so this scheduled task did not "
    "run. A scheduled email was not sent. Ask GAIA in a chat to draft or send "
    f"it instead. See {EMAIL_DOCS_URL}"
)


def retired_install_message(agent_id: str) -> str:
    return (
        f"The '{agent_id}' agent was removed from GAIA and can no longer be "
        "installed. The GAIA agent does its job now: run `gaia chat`, or open "
        f"the Agent UI. See {EMAIL_DOCS_URL}"
    )


def is_retired_agent_source(source: Optional[str]) -> bool:
    """Whether a daemon-clock job came from a retired agent's own clock."""
    name = (source or "").split(":", 1)[0]
    return name in RETIRED_AGENT_IDS
