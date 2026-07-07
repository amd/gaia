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
- Lets us extend the formatter with agent-specific overrides (e.g. the
  email agent's grant-migration message) without bloating the data class.
- One source of truth — if the message wording changes, every consumer
  picks it up automatically.
"""

from __future__ import annotations

from typing import Dict

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectorsError,
)

# Per-agent override messages for ``AGENT_NOT_GRANTED``. Keyed by
# ``(namespaced_agent_id, provider)`` (matching the ``agent_id`` and
# ``provider`` fields on ``AuthRequiredError``). Used to surface a more
# specific upgrade-path message when an existing user's grant predates a
# scope addition (e.g. a user who connected Google before #962 has no
# ``gmail.modify`` grant; the first organize/trash/send tool call raises
# ``AGENT_NOT_GRANTED`` and we want them to see exactly how to fix it).
#
# The provider is part of the key so a per-provider override never shadows
# another provider's failure: the email agent now serves both Google and
# Microsoft mailboxes, and a Microsoft grant gap must NOT show Google
# "Reconnect" instructions. Providers without a tailored entry fall through
# to the generic provider-aware branch below.
_AGENT_GRANT_MIGRATION_MESSAGES: Dict[tuple[str, str], str] = {
    ("installed:email", "google"): (
        "Email agent needs additional Google permissions "
        "(gmail.modify, gmail.send, calendar.events). "
        "Open Settings → Connectors → Google → Reconnect to grant the "
        "missing scopes."
    ),
}


def format_connector_error(e: BaseException) -> str:
    """Translate a connectors exception into a one-line user-facing string.

    The two states the user can fix by clicking something in
    Settings → Connections (``AGENT_NOT_GRANTED`` and ``NOT_CONNECTED``)
    are surfaced explicitly so agents can tell the user where to go.

    For ``(agent_id, provider)`` pairs registered in
    ``_AGENT_GRANT_MIGRATION_MESSAGES``, the ``AGENT_NOT_GRANTED`` reason
    returns the agent-specific upgrade message instead of the generic one;
    any provider without a tailored entry falls through to the generic
    provider-aware message.
    """
    if isinstance(e, AuthRequiredError):
        if e.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED:
            override = _AGENT_GRANT_MIGRATION_MESSAGES.get(
                (e.agent_id or "", e.provider or "")
            )
            if override:
                return override
            scopes = ", ".join(e.missing_scopes) or "(none reported)"
            return (
                f"AGENT_NOT_GRANTED: this agent isn't granted these scopes "
                f"on {e.provider}: {scopes}. Open Settings → Connections → "
                f"{e.provider} → Per-agent grants and grant them."
            )
        if e.reason in (
            AuthRequiredError.Reason.NOT_CONNECTED,
            AuthRequiredError.Reason.REAUTH_REQUIRED,
        ):
            return (
                f"NOT_CONNECTED: {e.provider} is not currently connected. "
                f"Open Settings → Connections → {e.provider} and click Connect."
            )
        return f"AUTH_REQUIRED: {e}"
    if isinstance(e, ConfigurationError):
        return f"CONFIG_ERROR: {e}"
    if isinstance(e, ConnectorsError):
        return f"CONNECTOR_ERROR: {e}"
    return f"UNEXPECTED_ERROR: {type(e).__name__}: {e}"
