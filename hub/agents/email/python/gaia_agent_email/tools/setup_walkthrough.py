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

import re
from typing import Any, Dict, List, Optional, Tuple

from gaia_agent_email import mailbox_state as ms
from gaia_agent_email.question import Option, ask
from gaia_agent_email.tools.onboarding_tools import (
    OAUTH_DOCS_URL,
    connect_scopes,
    narrate,
)

from gaia.connectors.setup_routes import SIGN_IN_DEVICE_CODE, Step, SetupRoute, steps_for
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
    scopes_to_request = connect_scopes(provider, scopes)

    started = run_sync(start_device_flow(provider, scopes_to_request))
    user_code = started.get("user_code", "")
    verification_uri = started.get("verification_uri", "")

    narrate(
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
            scopes=scopes_to_request,
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


# ---------------------------------------------------------------------------
# The step driver — walks route.steps, one at a time, for the device-code
# path only (steps_for(..., sign_in=SIGN_IN_DEVICE_CODE) already drops the
# loopback-only redirect-URI step; see gaia.connectors.setup_routes).
# ---------------------------------------------------------------------------

_DONE = "done"
_STUCK = "stuck"

#: Longer than question.py's DEFAULT_TIMEOUT_SECONDS (240s) — a first-timer
#: finding a specific portal setting routinely exceeds four minutes; timing
#: out mid-walk would abort the whole thing over a slow click, not a real
#: problem.
_STEP_TIMEOUT_SECONDS = 480

#: Said ONCE, at the first non-verifiable step — never repeated per step.
_CANNOT_SEE_PORTAL_NOTICE = (
    "A heads up: I can't see your screen or your provider's portal — I can "
    "only tell you what to click and check what I can. Say \"I'm stuck\" any "
    "time and I'll hand you off to the written guide instead."
)

#: Microsoft's Application (client) ID is always a GUID. Authored constant —
#: never an f-string interpolating what the user pasted, so a shape-check
#: failure can never echo a credential back into the transcript.
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_CLIENT_ID_SHAPE_ERROR = (
    "That doesn't look like an Application (client) ID — it should be a GUID "
    "like 11112222-bbbb-3333-cccc-4444dddd5555. Copy it again from the app's "
    "Overview page and paste just that."
)


class WalkthroughStuck(Exception):
    """The user asked for help partway through the guided walkthrough.

    Not a failure — a defined, honest handoff (design §3: "[I'm stuck] has a
    defined handler ... do not improvise it"). The message IS the user-facing
    text (mirrors onboarding_tools._Declined), so a caller need only str() it.
    """

    def __init__(self, step: Step, route: SetupRoute):
        self.step = step
        self.route = route
        super().__init__(
            f"No problem — here's the written guide instead: {OAUTH_DOCS_URL}. "
            "Nothing you've done so far was undone. Ask me again when you're "
            "ready to pick back up."
        )


def _shape_check_client_id(value: str) -> Optional[str]:
    if _GUID_RE.match((value or "").strip()):
        return None
    return _CLIENT_ID_SHAPE_ERROR


def _collect_credential(agent: Any, step: Step) -> str:
    """Ask for *step*'s credential value — a plain free-text prompt, never
    the navigation lane's Done/I'm-stuck options (design §4: a credential
    prompt must never receive the FAQ lane, and this is how that is
    structural rather than a rule someone has to remember)."""
    prompt = f"Paste the value for: {step.title}."
    value = ask(agent, prompt, allow_free_text=True, sensitive=False)
    if step.id == "client_id":
        error = _shape_check_client_id(value)
        while error is not None:
            narrate(agent, error)
            value = ask(agent, prompt, allow_free_text=True, sensitive=False)
            error = _shape_check_client_id(value)
    return value


def _ask_nav(agent: Any, step: Step) -> str:
    """Ask the Done / I'm-stuck navigation question for a non-credential step.

    ``allow_free_text=False`` with exactly two options, never zero (AC8) —
    the FAQ lane (increment 6) adds free text back on top of this, it never
    replaces the options.
    """
    options = (
        Option(_DONE, "Done", "I've finished this step — move to the next one."),
        Option(
            _STUCK, "I'm stuck", "Show me the written guide instead, and stop here."
        ),
    )
    return ask(
        agent,
        f"Done with: {step.title}?",
        options=options,
        allow_free_text=False,
        timeout_seconds=_STEP_TIMEOUT_SECONDS,
    )


def run_setup_walkthrough(
    agent: Any, route: SetupRoute
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Walk *route*'s device-code steps one at a time.

    Returns ``(collected, trace)``: ``collected`` maps credential step id ->
    the value entered (e.g. ``{"client_id": "..."}``); ``trace`` has one
    ``{"step_id", "verified"}`` entry per step, in order, appended REGARDLESS
    of what got narrated — the testable signal that no step ever claims
    unearned verification (design §3).

    Raises ``WalkthroughStuck`` if the user asks for help; the caller ends
    the flow honestly rather than continuing to improvise.
    """
    trace: List[Dict[str, Any]] = []
    collected: Dict[str, str] = {}
    told_cannot_see = False

    for step in steps_for(route, sign_in=SIGN_IN_DEVICE_CODE):
        narrate(agent, f"{step.title}\n{step.instruction}")
        if not step.verifiable and not told_cannot_see:
            narrate(agent, _CANNOT_SEE_PORTAL_NOTICE)
            told_cannot_see = True

        if step.collects_credential:
            value = _collect_credential(agent, step)
            collected[step.id] = value
            # Reached only once the shape check above has already passed —
            # verified is never claimed for a step this route can't check.
            trace.append({"step_id": step.id, "verified": step.verifiable})
            continue

        answer = _ask_nav(agent, step)
        if answer == _STUCK:
            raise WalkthroughStuck(step, route)
        trace.append({"step_id": step.id, "verified": False})

    return collected, trace


__all__ = [
    "WalkthroughStuck",
    "run_device_oauth",
    "run_setup_walkthrough",
]
