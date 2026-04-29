"""Public API surface for the connections subsystem.

This module will expose the functions documented in the design: starting
flows, completing them, listing connections, and `get_access_token`.
Currently this file wires into the minimal implementations in the package.
"""
from .tokens import get_access_token as _get_access_token
from .flow import start_authorization, get_flow, complete_flow
from .grants import grant_agent, revoke_agent_grant, list_agent_grants
from .store import get_refresh_token, save_refresh_token, delete_refresh_token


async def get_access_token(provider: str, scopes: list[str], *, agent_id: str | None = None) -> str:
    return await _get_access_token(provider, scopes, agent_id=agent_id)


__all__ = [
    "get_access_token",
    "start_authorization",
    "get_flow",
    "complete_flow",
    "grant_agent",
    "revoke_agent_grant",
    "list_agent_grants",
    "get_refresh_token",
    "save_refresh_token",
    "delete_refresh_token",
]
