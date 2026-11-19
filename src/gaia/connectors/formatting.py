# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
User-facing formatting for ``gaia.connectors`` errors.

The connector exception hierarchy lives in ``gaia.connectors.errors`` and
stays a pure data layer (each error knows what failed and what the user
should do, but does not concern itself with HOW that gets surfaced).
This module is the single presentation seam — every agent that talks to
the connectors framework should funnel its caught exceptions through
``format_connector_error`` so users see a consistent, actionable string
across the CLI, the AgentUI, and tool result envelopes.

Why a separate module (and not in ``errors.py``):
- Keeps the exception hierarchy free of presentation logic.
- One source of truth — if the message wording changes, every consumer
  picks it up automatically.
"""

from __future__ import annotations

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionBusyError,
    ConnectorsError,
)


def format_connector_error(e: BaseException) -> str:
    """Translate a connectors exception into a one-line user-facing string.

    The two states the user can fix by clicking something in
    Settings → Connectors (``AGENT_NOT_GRANTED`` and ``NOT_CONNECTED``)
    are surfaced explicitly so agents can tell the user where to go.

    """
    if isinstance(e, AuthRequiredError):
        if e.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED:
            if e.agent_id is None:
                # The fail-closed branch (#915): a credential request inside an
                # agent turn that carries no identity. "Grant it in Settings"
                # is unfollowable here — there is no agent to grant anything
                # TO. Name the actual cause instead.
                return (
                    f"AGENT_IDENTITY_MISSING: a credential for {e.provider} was "
                    "requested from inside an agent turn that carries no agent "
                    "identity, so no per-agent grant can be checked and none is "
                    "assumed. Either the agent has no AGENT_ID, or the calling "
                    "code left the agent's context (a thread or task it started "
                    "itself) — capture current_agent_id() and pass agent_id= "
                    "explicitly across that boundary."
                )
            scopes = ", ".join(e.missing_scopes) or "(none reported)"
            return (
                f"AGENT_NOT_GRANTED: this agent isn't granted these scopes "
                f"on {e.provider}: {scopes}. Open Settings → Connectors → "
                f"{e.provider} → Per-agent grants and grant them."
            )
        if e.reason in (
            AuthRequiredError.Reason.NOT_CONNECTED,
            AuthRequiredError.Reason.REAUTH_REQUIRED,
        ):
            return (
                f"NOT_CONNECTED: {e.provider} is not currently connected. "
                f"Open Settings → Connectors → {e.provider} and click Connect."
            )
        return f"AUTH_REQUIRED: {e}"
    if isinstance(e, ConnectionBusyError):
        return f"CONNECTION_BUSY: {e}"
    if isinstance(e, ConfigurationError):
        return f"CONFIG_ERROR: {e}"
    if isinstance(e, ConnectorsError):
        return f"CONNECTOR_ERROR: {e}"
    return f"UNEXPECTED_ERROR: {type(e).__name__}: {e}"
