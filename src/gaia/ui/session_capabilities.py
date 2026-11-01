# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The tools a chat's agent has, for the session panel's Agent info (#440).

Read from the agent the chat actually talks to, never from a stand-in built
for the purpose: an in-process agent is the one cached for the session, and a
relayed one (the Hub binary flagship) is asked over the daemon for its own
session's agent. A chat whose agent is not running gets an error saying so,
never an empty list, which would read as "this agent can do nothing".
"""

from __future__ import annotations

from typing import Any, Dict, List

from gaia.logger import get_logger

logger = get_logger(__name__)

NOT_RUNNING = (
    "This chat's agent isn't running, so its tools can't be listed yet. It "
    "starts with your next message; open Agent info again after that."
)

_FIELDS = ("name", "description", "category")


class CapabilitiesError(RuntimeError):
    """The chat's tools can't be listed; ``status_code`` says why."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def describe(session: Dict[str, Any]) -> Dict[str, Any]:
    """``{"session_id", "agent_type", "tools"}`` for *session*'s agent.

    Blocking (a relayed agent is reached through the daemon); call it off the
    event loop.

    Raises:
        CapabilitiesError: 409 when the agent isn't running, 503 when a relayed
            agent can't be started or is too old to list its tools, 502 when it
            fails to answer or answers with a malformed list.
    """
    from ._chat_helpers import cached_session_agent_of
    from .session_environment import relayed

    session_id = session["id"]
    agent_type = session.get("agent_type") or "chat"
    agent = cached_session_agent_of(session_id, agent_type)
    if agent is not None:
        tools = agent.describe_tools()
    elif relayed(session):
        tools = _from_sidecar(session_id, agent_type)
    else:
        raise CapabilitiesError(409, NOT_RUNNING)
    return {"session_id": session_id, "agent_type": agent_type, "tools": tools}


def _from_sidecar(session_id: str, agent_type: str) -> List[Dict[str, str]]:
    from .email_sidecar import daemon_client
    from .email_sidecar.errors import SidecarError, SidecarHTTPError
    from .email_sidecar.profiles import profile_for, tools_supported

    profile = profile_for(agent_type)
    if profile is None or profile.min_tools_api_version is None:
        raise CapabilitiesError(
            503,
            (
                profile.tools_upgrade_message
                if profile is not None and profile.tools_upgrade_message
                else f"The {agent_type} agent can't list its tools here."
            ),
        )
    try:
        handle = daemon_client.acquire_handle(profile.agent_id)
    except SidecarError as e:
        logger.error("Could not start the %s agent to list tools: %s", agent_type, e)
        raise CapabilitiesError(503, profile.start_failed_text(e.kind)) from e
    if not tools_supported(profile, handle.api_version):
        raise CapabilitiesError(503, profile.tools_upgrade_message)
    try:
        body = handle.proxy().session_tools(session_id)
    except SidecarHTTPError as e:
        if e.status_code == 404:
            raise CapabilitiesError(409, NOT_RUNNING) from e
        raise CapabilitiesError(
            502, f"The {profile.display_name} agent couldn't list its tools: {e}"
        ) from e
    except SidecarError as e:
        raise CapabilitiesError(
            502, f"The {profile.display_name} agent couldn't list its tools: {e}"
        ) from e
    return _checked(body, profile.display_name)


def _checked(body: Any, display_name: str) -> List[Dict[str, str]]:
    """The sidecar's tool list, refused when it is not one the panel can trust."""

    def bad(why: str) -> CapabilitiesError:
        return CapabilitiesError(
            502,
            f"The {display_name} agent sent a tool list GAIA can't read ({why}). "
            "Update GAIA to the latest version, then try again.",
        )

    tools = body.get("tools") if isinstance(body, dict) else None
    if not isinstance(tools, list):
        raise bad("no list of tools")
    seen = set()
    out = []
    for item in tools:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(f), str) for f in _FIELDS
        ):
            raise bad("a tool without a name, description and category")
        if item["name"] in seen:
            raise bad(f"{item['name']!r} listed twice")
        seen.add(item["name"])
        out.append({f: item[f] for f in _FIELDS})
    return out
