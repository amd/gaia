# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guided Gmail mailbox onboarding (#2594, "the Google half of #2590") —
personal Google account, end to end.

Unlike Outlook, Google's route genuinely needs a client secret (its token
endpoint rejects a Desktop-app PKCE client without one) and has no
device-code flow for a personal client — sign-in is the ordinary browser
loopback. What #2590 already proved for Microsoft this asserts for Google:
the SAME ``setup_routes``-authored content drives both the console error and
the in-chat walkthrough, the secret is never echoed, and a step this driver
cannot verify is never traced as verified.

``FakeAgent`` / ``ScriptedConsole`` are shared with the other onboarding test
modules via ``onboarding_fakes`` so the real ``question.ask()`` — its
sensitive-echo suppression included — always runs unmodified.
"""

from __future__ import annotations

import json

import pytest
from gaia_agent_email.tools import onboarding_tools as ob
from onboarding_fakes import FakeAgent as _FakeAgent

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
VALID_CLIENT_ID = "12345-abc.apps.googleusercontent.com"
VALID_SECRET = "s3cr3t-value"

# The loopback route's nav steps, in order — Done answers walk straight
# through; see gaia.connectors.setup_routes.GOOGLE_PERSONAL /
# steps_for(sign_in="loopback").
_WALKTHROUGH_DONE_ANSWERS = ["done", "done", "done", "done", "done"]


def _connection(scopes=None, email="kalin@gmail.com", error=None):
    entry = {
        "provider": "google",
        "account_email": email,
        "scopes": list(GMAIL_SCOPES if scopes is None else scopes),
        "connected_at": 1,
    }
    if error:
        entry["error"] = error
    return entry


@pytest.fixture()
def google_connectors(monkeypatch):
    """Drive every connector call the flow makes for google, no keyring/network."""

    state = {
        "connection": None,
        "granted": False,
        "token_error": None,
        "grants": [],
        "configured": [],
        "started_flows": [],
        "completed": [],
        "client_id": VALID_CLIENT_ID,
        "client_secret": VALID_SECRET,
        "timeouts": [],
    }

    def get_connection(provider):
        return state["connection"] if provider == "google" else None

    def check_agent_grant(provider, agent_id, scopes):
        return state["granted"]

    def get_access_token_sync(**kwargs):
        if state["token_error"] is not None:
            raise state["token_error"]
        return "token"

    def grant_agent(provider, agent_id, scopes):
        state["grants"].append((provider, agent_id, tuple(scopes)))

    class _Provider:
        provider_id = "google"

        @property
        def client_id(self):
            return state["client_id"]

        @property
        def client_secret(self):
            return state["client_secret"]

    def get_provider(provider_id):
        from gaia.connectors.errors import ConfigurationError

        if not state["client_id"]:
            raise ConfigurationError("GAIA_GOOGLE_CLIENT_ID is not set")
        return _Provider()

    async def configure(connector_id, config):
        state["configured"].append((connector_id, dict(config)))
        if config.get("client_id"):
            state["client_id"] = config["client_id"]
        if config.get("client_secret"):
            state["client_secret"] = config["client_secret"]
        if config.get("save_only"):
            return {"status": "saved", "connector_id": connector_id}
        # The browser-flow start — mirrors what handler.configure returns when
        # asked to begin a loopback sign-in (scopes + grant_agents present,
        # no save_only).
        flow_id = f"flow-{len(state['started_flows'])}"
        state["started_flows"].append((connector_id, dict(config)))
        return {
            "status": "pending",
            "connector_id": connector_id,
            "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?...",
            "flow_id": flow_id,
        }

    async def complete_authorization(flow_id):
        state["completed"].append(flow_id)
        state["connection"] = _connection()
        state["granted"] = True
        return {
            "provider": "google",
            "account_email": "kalin@gmail.com",
            "scopes": list(GMAIL_SCOPES),
            "connected_at": 1,
        }

    def run_sync(coro, *, timeout=30.0):
        import asyncio

        state["timeouts"].append(timeout)
        return asyncio.run(coro)

    monkeypatch.setattr("gaia.connectors.api.get_connection", get_connection)
    monkeypatch.setattr("gaia.connectors.grants.check_agent_grant", check_agent_grant)
    monkeypatch.setattr(
        "gaia.connectors.api.get_access_token_sync", get_access_token_sync
    )
    monkeypatch.setattr("gaia.connectors.grants.grant_agent", grant_agent)
    monkeypatch.setattr("gaia.connectors.providers.get", get_provider)
    monkeypatch.setattr("gaia.connectors.handler.configure", configure)
    monkeypatch.setattr(
        "gaia.connectors.flow.complete_authorization", complete_authorization
    )
    monkeypatch.setattr("gaia.connectors._loop.run_sync", run_sync)
    return state


def _run(agent, provider="google"):
    return json.loads(ob._setup_mailbox_access(agent, provider))


# ---------------------------------------------------------------------------
# The secret is real here (unlike Microsoft) — assert it is asked for, kept
# out of cleartext, and actually reaches configure().
# ---------------------------------------------------------------------------


def test_full_walkthrough_and_loopback_connect_asks_for_and_saves_the_secret(
    google_connectors,
):
    """AC1, asserted end to end: a first-time Gmail connect — walkthrough +
    loopback sign-in — reaches a working mailbox through chat alone."""
    google_connectors["connection"] = None
    google_connectors["client_id"] = ""
    google_connectors["client_secret"] = ""
    agent = _FakeAgent(
        answers=[
            "yes",
            *_WALKTHROUGH_DONE_ANSWERS,
            VALID_CLIENT_ID,
            VALID_SECRET,
        ]
    )

    out = _run(agent, provider="google")

    assert out["ok"] is True, out
    assert out["data"]["changed"] is True
    assert out["data"]["account_email"] == "kalin@gmail.com"
    # The secret is a real value here, unlike Microsoft — but it must never be
    # echoed into any narrated message or option description.
    assert not any(VALID_SECRET in m for m in agent.console.info)
    for call in agent.console.asked:
        assert VALID_SECRET not in call.get("message", "")
    # The collected id+secret were actually used to save the client, together,
    # before sign-in started.
    save_calls = [c for c in google_connectors["configured"] if c[1].get("save_only")]
    assert len(save_calls) == 1
    _, saved = save_calls[0]
    assert saved["client_id"] == VALID_CLIENT_ID
    assert saved["client_secret"] == VALID_SECRET
    assert google_connectors["started_flows"], "loopback sign-in never started"
    assert google_connectors["completed"], "loopback sign-in never completed"


def test_client_secret_prompt_is_marked_sensitive(google_connectors):
    google_connectors["connection"] = None
    google_connectors["client_id"] = ""
    google_connectors["client_secret"] = ""
    agent = _FakeAgent(
        answers=[
            "yes",
            *_WALKTHROUGH_DONE_ANSWERS,
            VALID_CLIENT_ID,
            VALID_SECRET,
        ]
    )

    _run(agent, provider="google")

    secret_calls = [
        c
        for c in agent.console.asked
        if c["message"].startswith("Paste the value for: Copy the Client Secret")
    ]
    assert len(secret_calls) == 1
    assert secret_calls[0]["sensitive"] is True


def test_reconnect_with_client_already_configured_skips_the_walkthrough(
    google_connectors,
):
    """``gap is None`` case: id and secret are already on disk — go straight
    to sign-in, never re-walked (mirrors the Microsoft reconnect guarantee)."""
    google_connectors["connection"] = None  # not yet connected/granted
    agent = _FakeAgent(answers=["yes"])  # confirm the repair; no walkthrough needed

    out = _run(agent, provider="google")

    assert out["ok"] is True, out
    assert out["data"]["changed"] is True
    assert google_connectors["started_flows"], "loopback sign-in never started"
    save_calls = [c for c in google_connectors["configured"] if c[1].get("save_only")]
    assert save_calls == [], "an already-configured client must not be re-walked"


def test_already_usable_google_mailbox_is_never_walked_through_setup(
    google_connectors,
):
    google_connectors["connection"] = _connection()
    google_connectors["granted"] = True
    agent = _FakeAgent()

    out = _run(agent, provider="google")

    assert out["ok"] is True
    assert out["data"]["changed"] is False
    assert agent.console.asked == [], "a working mailbox must not be interrupted"


def test_declining_client_setup_leaves_nothing_configured(google_connectors):
    google_connectors["connection"] = None
    google_connectors["client_id"] = ""
    google_connectors["client_secret"] = ""
    agent = _FakeAgent(answers=["no"])

    out = _run(agent, provider="google")

    assert out["ok"] is True
    assert out["data"]["declined"] is True
    assert google_connectors["configured"] == []
    assert google_connectors["started_flows"] == []
