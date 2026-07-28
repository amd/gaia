# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guided Outlook mailbox onboarding (#2590) — Microsoft-only, end to end.

Three bugs this replaces:

1. Setting up Outlook asked for a client secret Microsoft's public-client PKCE
   route never issues, so the prompt was impossible to answer honestly.
2. The setup flow could only fail as one opaque "connect your mailbox" prompt
   — no walkthrough, no verification, no browserless sign-in.
3. A user with a working mailbox could still be dragged into setup — #2469's
   whole point was to stop that.

``_ScriptedConsole`` / ``_FakeAgent`` are shared with
``test_mailbox_onboarding_2469.py`` via ``conftest.py`` so the real
``question.ask()`` resolves options, enforces strict rejection, and
suppresses sensitive echoes exactly as it does in production — a hand-rolled
fake ``ask`` would not reproduce that, and a second drifted copy of the fake
would silently stop testing it.
"""

from __future__ import annotations

import json

import pytest
from onboarding_fakes import FakeAgent as _FakeAgent
from onboarding_fakes import ScriptedConsole as _ScriptedConsole
from gaia_agent_email.tools import onboarding_tools as ob

OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
]
VALID_GUID = "11112222-bbbb-3333-cccc-4444dddd5555"

# The device-code route's nav steps, in order — Done answers walk straight
# through; see gaia.connectors.setup_routes.MS_PERSONAL /
# steps_for(sign_in="device_code").
_WALKTHROUGH_DONE_ANSWERS = ["done", "done", "done", "done"]


def _connection(scopes=None, email="kalin@outlook.com", error=None):
    entry = {
        "provider": "microsoft",
        "account_email": email,
        "scopes": list(OUTLOOK_SCOPES if scopes is None else scopes),
        "connected_at": 1,
    }
    if error:
        entry["error"] = error
    return entry


@pytest.fixture()
def ms_connectors(monkeypatch):
    """Drive every connector call the flow makes for microsoft, no keyring/network."""

    state = {
        "connection": None,
        "granted": False,
        "token_error": None,
        "grants": [],
        "configured": [],
        "device_started": [],
        "device_polled": [],
        "client_id": VALID_GUID,
        "timeouts": [],
        "poll_result": None,
    }

    def get_connection(provider):
        return state["connection"] if provider == "microsoft" else None

    def check_agent_grant(provider, agent_id, scopes):
        return state["granted"]

    def get_access_token_sync(**kwargs):
        if state["token_error"] is not None:
            raise state["token_error"]
        return "token"

    def grant_agent(provider, agent_id, scopes):
        state["grants"].append((provider, agent_id, tuple(scopes)))

    class _Provider:
        provider_id = "microsoft"

        @property
        def client_id(self):
            return state["client_id"]

        @property
        def client_secret(self):
            return ""  # Microsoft public-client PKCE — never a secret.

    def get_provider(provider_id):
        from gaia.connectors.errors import ConfigurationError

        if not state["client_id"]:
            raise ConfigurationError("GAIA_MICROSOFT_CLIENT_ID is not set")
        return _Provider()

    async def configure(connector_id, config):
        state["configured"].append((connector_id, config))
        if config.get("client_id"):
            state["client_id"] = config["client_id"]
        return {"status": "saved", "connector_id": connector_id}

    async def start_device_flow(provider, scopes):
        state["device_started"].append((provider, list(scopes)))
        return {
            "provider_id": provider,
            "scopes": list(scopes),
            "device_code": "DEV-CODE",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            "message": "Go to https://microsoft.com/devicelogin and enter ABCD-EFGH",
        }

    async def poll_device_flow(
        provider, device_code, *, scopes, interval, expires_in, grant_agents=None
    ):
        state["device_polled"].append(
            {
                "provider": provider,
                "device_code": device_code,
                "scopes": list(scopes),
                "grant_agents": grant_agents,
            }
        )
        result = state["poll_result"] or {
            "provider": provider,
            "account_email": "kalin@outlook.com",
            "scopes": list(scopes),
            "connected_at": 1,
        }
        # Mirror what a real successful device-code connect does: the
        # mailbox is now connected and granted, so the tail-end
        # inspect_provider(probe=True) call reports it usable.
        state["connection"] = _connection(scopes=list(scopes))
        state["granted"] = True
        return result

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
    monkeypatch.setattr("gaia.connectors.flow.start_device_flow", start_device_flow)
    monkeypatch.setattr("gaia.connectors.flow.poll_device_flow", poll_device_flow)
    monkeypatch.setattr("gaia.connectors._loop.run_sync", run_sync)
    return state


def _run(agent, provider="microsoft"):
    return json.loads(ob._setup_mailbox_access(agent, provider))


def _questions(agent):
    return [a["message"] for a in agent.console.asked]


# ---------------------------------------------------------------------------
# Never asked for a client secret — the whole walk, every state.
# ---------------------------------------------------------------------------


def test_reauth_with_client_already_configured_never_mentions_secret(ms_connectors):
    """``gap is None`` case: the client is already on disk (reconnect path).

    Declining here never reaches the walkthrough or device sign-in at all —
    still worth asserting nothing about a secret is ever said.
    """
    from gaia.connectors.errors import ConnectionRevokedError

    ms_connectors["connection"] = _connection()
    ms_connectors["granted"] = True
    ms_connectors["token_error"] = ConnectionRevokedError("microsoft")
    agent = _FakeAgent(answers=["no"])  # decline the reconnect offer

    _run(agent)

    assert not any("secret" in a["message"].lower() for a in agent.console.asked)


def test_declining_client_setup_with_no_client_configured_never_mentions_secret(
    ms_connectors,
):
    """``gap == "client_id"`` case: nothing configured yet — say [I'm stuck]
    right away rather than complete the walkthrough."""
    ms_connectors["connection"] = None
    ms_connectors["client_id"] = ""
    # "yes" confirms the repair; "stuck" ends the walkthrough on step one.
    agent = _FakeAgent(answers=["yes", "stuck"])

    out = _run(agent, provider="microsoft")

    assert out["ok"] is True
    assert out["data"]["stuck"] is True
    assert not any("secret" in a["message"].lower() for a in agent.console.asked)
    # Nor via any option description offered along the way (truthfully saying
    # "no secret needed" is fine — the bug was ASKING for one).
    for call in agent.console.asked:
        for opt in call.get("options") or []:
            desc = opt.get("description", "").lower()
            assert "paste" not in desc or "secret" not in desc
    assert ms_connectors["configured"] == [], "getting stuck must change nothing"


def test_full_walkthrough_and_device_connect_never_mentions_secret_either(
    ms_connectors,
):
    """AC1, asserted end to end: a COMPLETE first-time Outlook connect —
    walkthrough + device-code sign-in — never once asks for a secret."""
    ms_connectors["connection"] = None
    ms_connectors["client_id"] = ""
    agent = _FakeAgent(
        answers=["yes", *_WALKTHROUGH_DONE_ANSWERS, VALID_GUID]
    )

    out = _run(agent, provider="microsoft")

    assert out["ok"] is True, out
    assert out["data"]["changed"] is True
    assert out["data"]["account_email"] == "kalin@outlook.com"
    assert not any("secret" in a["message"].lower() for a in agent.console.asked)
    for call in agent.console.asked:
        for opt in call.get("options") or []:
            desc = opt.get("description", "").lower()
            assert "paste" not in desc or "secret" not in desc
    # The collected client id was actually used to configure the provider.
    _, config = ms_connectors["configured"][0]
    assert config["client_id"] == VALID_GUID
    assert "client_secret" not in config
    assert ms_connectors["device_started"], "device-code sign-in never ran"


# ---------------------------------------------------------------------------
# A working mailbox is never re-interviewed (#2469's core guarantee).
# ---------------------------------------------------------------------------


def test_already_usable_microsoft_mailbox_is_never_walked_through_setup(
    ms_connectors,
):
    ms_connectors["connection"] = _connection()
    ms_connectors["granted"] = True
    agent = _FakeAgent()

    out = _run(agent, provider="microsoft")

    assert out["ok"] is True
    assert out["data"]["changed"] is False
    assert agent.console.asked == [], "a working mailbox must not be interrupted"


def test_a_working_google_mailbox_is_never_dragged_into_microsoft_setup(
    ms_connectors, monkeypatch
):
    """#2590's the-walkthrough-hooks-at-_choose_provider requirement: a user
    with ANY working mailbox is never shown a walkthrough question, even if
    they have an abandoned Microsoft attempt sitting around."""
    from gaia_agent_email import mailbox_state as ms

    ms_connectors["connection"] = None  # microsoft: not connected
    google_scopes = ms.required_scopes("google")

    def get_connection(provider):
        if provider == "google":
            return {
                "provider": "google",
                "account_email": "kalin@gmail.com",
                "scopes": list(google_scopes),
                "connected_at": 1,
            }
        return None

    def check_agent_grant(provider, agent_id, scopes):
        return provider == "google"

    monkeypatch.setattr("gaia.connectors.api.get_connection", get_connection)
    monkeypatch.setattr("gaia.connectors.grants.check_agent_grant", check_agent_grant)
    monkeypatch.setattr(
        "gaia.connectors.api.get_access_token_sync", lambda **kw: "token"
    )

    agent = _FakeAgent()

    out = _run(agent, provider="")  # no target named — the "pick the best" path

    assert out["ok"] is True
    assert out["data"]["provider"] == "google"
    assert agent.console.asked == [], "a working Gmail mailbox must stay quiet"
