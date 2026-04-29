"""Context helpers for resolving the calling agent id.

The agent runtime should set `current_agent_id` when executing agent code so
that `get_access_token` can enforce per-agent grants without requiring an
explicit `agent_id` parameter everywhere.
"""
import contextvars

current_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_agent_id", default=None)


def set_agent_id(agent_id: str) -> None:
    current_agent_id.set(agent_id)


def get_agent_id() -> str | None:
    return current_agent_id.get()


__all__ = ["current_agent_id", "set_agent_id", "get_agent_id"]
