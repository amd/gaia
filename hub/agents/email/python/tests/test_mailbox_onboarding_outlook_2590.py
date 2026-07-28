# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guided Outlook mailbox onboarding (#2590) — Microsoft-only.

Two bugs this replaces:

1. Setting up Outlook asked for a client secret Microsoft's public-client PKCE
   route never issues, so the prompt was impossible to answer honestly.
2. The setup flow could only fail as one opaque "connect your mailbox" prompt
   — no walkthrough, no verification, no browserless sign-in.

``_ScriptedConsole`` / ``_FakeAgent`` are copied verbatim from
``test_mailbox_onboarding_2469.py`` (not imported — this tree has no
``tests/__init__.py``) so the real ``question.ask()`` resolves options,
enforces strict rejection, and suppresses sensitive echoes exactly as it does
in production; a hand-rolled fake ``ask`` would not reproduce that.
"""

from __future__ import annotations

import json

import pytest
from gaia_agent_email import mailbox_state as ms
from gaia_agent_email import question as q
from gaia_agent_email.tools import onboarding_tools as ob

OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
]


# ---------------------------------------------------------------------------
# Fakes — verbatim copy, see module docstring.
# ---------------------------------------------------------------------------


class _ScriptedConsole:
    """Records every question asked and replies from a fixed script."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []
        self.info = []

    def request_user_input_blocking(self, **kwargs):
        self.asked.append(kwargs)
        if not self.answers:
            return q.NO_RESPONSE
        return self.answers.pop(0)

    def print_info(self, message):
        self.info.append(message)


class _FakeAgent:
    def __init__(self, answers=(), can_answer_questions=True):
        self.console = _ScriptedConsole(answers)
        self.can_answer_questions = can_answer_questions


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
        "completed": [],
        "client_id": "11112222-bbbb-3333-cccc-4444dddd5555",
        "timeouts": [],
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
        return {"status": "saved", "connector_id": connector_id}

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
    monkeypatch.setattr("gaia.connectors._loop.run_sync", run_sync)
    return state


def _run(agent, provider="microsoft"):
    return json.loads(ob._setup_mailbox_access(agent, provider))


def _questions(agent):
    return [a["message"] for a in agent.console.asked]


# ---------------------------------------------------------------------------
# Increment 1 — Outlook is never asked for a client secret.
# ---------------------------------------------------------------------------


def test_reauth_with_client_already_configured_never_mentions_secret(ms_connectors):
    """``gap is None`` case: the client is already on disk (reconnect path).

    Reaches ``_collect_oauth_client`` with nothing missing, so it should ask
    nothing at all about credentials — and definitely never mention a secret.
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
    """``gap == "client_id"`` case: nothing configured yet, still no secret talk."""
    ms_connectors["connection"] = None
    ms_connectors["client_id"] = ""
    # "yes" confirms the repair; "no" declines at the credentials question.
    agent = _FakeAgent(answers=["yes", "no"])

    _run(agent, provider="microsoft")

    assert not any("secret" in a["message"].lower() for a in agent.console.asked)
    # Nor asked to provide one via any option description offered along the way
    # (truthfully saying "no secret needed" is fine and expected — see the
    # plan's lifted seven-day-expiry constraint; the bug was ASKING for one).
    for call in agent.console.asked:
        for opt in call.get("options") or []:
            desc = opt.get("description", "").lower()
            assert "paste" not in desc or "secret" not in desc
