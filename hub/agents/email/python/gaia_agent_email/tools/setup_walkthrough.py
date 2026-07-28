# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guided Outlook mailbox setup — device-code sign-in and step walkthrough (#2590).

Extends, rather than rewrites, ``onboarding_tools`` (#2469): the scripted
repair conversation stays there, this module holds the walkthrough that
conversation hands off to for a first-time Microsoft connect. Kept separate
because ``onboarding_tools.py`` is already ~600 lines scoped to *repair*, and
the guided walkthrough is a different concern.

**Outlook only.** No ``google_personal`` route, no account-kind interview, no
resumability — see ``gaia.connectors.setup_routes`` and the #2590 plan for
why those are out of scope here.
"""

from __future__ import annotations

from typing import Any, Dict

from gaia_agent_email import mailbox_state as ms
from gaia_agent_email.tools.onboarding_tools import _connect_scopes, _status

from gaia.logger import get_logger

log = get_logger(__name__)

#: How far past the device code's own advertised lifetime to hold the wait
#: open. NOT the loopback flow's 150s constant — that exists specifically to
#: sit just above the LOOPBACK flow's 120s bound, and reusing it here would
#: cancel a 900s-lived device code at 150s: a user who approves at T+300s
#: (well within the code's real life) gets nothing, and the single-use code
#: is burnt. This is slack on top of poll_device_flow's OWN expires_in-based
#: deadline, not a substitute for deriving it.
_DEVICE_POLL_GRACE_SECONDS = 30

#: Fallback if a provider response is missing (or zeroes) expires_in.
#: Matches poll_device_flow's own default so the two never disagree.
_DEFAULT_EXPIRES_IN_SECONDS = 900


def run_device_oauth(agent: Any, provider: str) -> Dict[str, Any]:
    """Run the device-code flow and grant the result to this agent.

    Mirrors ``onboarding_tools._run_oauth`` but for the browserless
    device-code path: no OAuth client secret, no loopback port, works on a
    machine with no browser at all. Returns the same state-dict shape
    ``_run_oauth`` does, so callers (``onboarding_tools._setup_flow``) treat
    the two interchangeably.
    """
    from gaia.connectors._loop import run_sync
    from gaia.connectors.flow import poll_device_flow, start_device_flow
    from gaia.connectors.grants import grant_agent

    label = ms.provider_label(provider)
    scopes = ms.required_scopes(provider)
    connect_scopes = _connect_scopes(provider, scopes)

    started = run_sync(start_device_flow(provider, connect_scopes))
    user_code = started.get("user_code", "")
    verification_uri = started.get("verification_uri", "")

    _status(
        agent,
        started.get("message")
        or (
            f"To sign in to {label}, go to {verification_uri} and enter the "
            f"code {user_code}. It's valid for a few minutes — no browser "
            "needed on this machine."
        ),
    )

    expires_in = int(started.get("expires_in") or _DEFAULT_EXPIRES_IN_SECONDS)
    poll_timeout = expires_in + _DEVICE_POLL_GRACE_SECONDS

    state = run_sync(
        poll_device_flow(
            provider,
            started["device_code"],
            scopes=connect_scopes,
            interval=int(started.get("interval") or 5),
            expires_in=expires_in,
            # Committing the grant inside the same flow is what stops the
            # connected-but-unusable dead end this feature exists to remove —
            # mirrors _run_oauth's grant_agents on the loopback path.
            grant_agents={ms.AGENT_ID: scopes},
        ),
        timeout=poll_timeout,
    )
    granted = list(state.get("scopes") or scopes)
    # poll_device_flow already committed the grant via grant_agents; this is
    # idempotent and covers a provider path that ignores it — same
    # belt-and-suspenders pattern as _run_oauth.
    grant_agent(provider, ms.AGENT_ID, granted)
    return state


__all__ = ["run_device_oauth"]
