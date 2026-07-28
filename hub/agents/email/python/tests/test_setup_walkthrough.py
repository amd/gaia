# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Device-code sign-in glue for guided mailbox onboarding (#2590).

The critical detail: the wait must be bound on the device code's OWN
advertised ``expires_in`` (Microsoft defaults to 900s), never the 150s
constant that exists specifically because the LOOPBACK flow's own bound is
120s. Copying that constant here means: poll cancelled at 150s, user
approves at T+300s (well within the code's real 900s life), nothing saves,
and the single-use code is burnt for a user who did everything right.
"""

from __future__ import annotations

import pytest
from conftest import FakeAgent as _FakeAgent
from gaia_agent_email.tools import setup_walkthrough as sw

PROVIDER = "microsoft"
OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
]


@pytest.fixture()
def device_flow(monkeypatch):
    state = {
        "started": None,
        "polled": None,
        "grants": [],
        "timeouts": [],
        "poll_result": {
            "provider": PROVIDER,
            "account_email": "kalin@outlook.com",
            "scopes": OUTLOOK_SCOPES,
            "connected_at": 1,
        },
    }

    async def start_device_flow(provider, scopes):
        state["started"] = (provider, list(scopes))
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

    async def poll_device_flow(provider, device_code, *, scopes, interval, expires_in, grant_agents=None):
        state["polled"] = {
            "provider": provider,
            "device_code": device_code,
            "scopes": list(scopes),
            "interval": interval,
            "expires_in": expires_in,
            "grant_agents": grant_agents,
        }
        return state["poll_result"]

    def grant_agent(provider, agent_id, scopes):
        state["grants"].append((provider, agent_id, tuple(scopes)))

    def run_sync(coro, *, timeout=30.0):
        import asyncio

        state["timeouts"].append(timeout)
        return asyncio.run(coro)

    monkeypatch.setattr("gaia.connectors.flow.start_device_flow", start_device_flow)
    monkeypatch.setattr("gaia.connectors.flow.poll_device_flow", poll_device_flow)
    monkeypatch.setattr("gaia.connectors.grants.grant_agent", grant_agent)
    monkeypatch.setattr("gaia.connectors._loop.run_sync", run_sync)
    return state


def test_narrates_the_user_code_and_verification_url(device_flow):
    agent = _FakeAgent()

    sw.run_device_oauth(agent, PROVIDER)

    assert any("ABCD-EFGH" in m for m in agent.console.info)
    assert any("devicelogin" in m for m in agent.console.info)


def test_poll_timeout_is_derived_from_the_codes_own_expires_in(device_flow):
    """AC2: the run_sync timeout for the POLL call must come from the device
    code's own expires_in — never the 150s loopback-flow constant."""
    agent = _FakeAgent()

    sw.run_device_oauth(agent, PROVIDER)

    # Two run_sync calls: start_device_flow (default timeout), then
    # poll_device_flow (the one under test).
    assert len(device_flow["timeouts"]) == 2
    poll_timeout = device_flow["timeouts"][-1]
    assert poll_timeout != 150
    assert poll_timeout > 900, "must comfortably exceed the code's own 900s life"


def test_poll_timeout_tracks_a_shorter_expires_in_too(device_flow, monkeypatch):
    """Not hardcoded to 900 either — genuinely derived per call."""

    async def short_start_device_flow(provider, scopes):
        return {
            "provider_id": provider,
            "scopes": list(scopes),
            "device_code": "DEV-CODE",
            "user_code": "WXYZ-1234",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 60,
            "interval": 5,
            "message": "",
        }

    monkeypatch.setattr(
        "gaia.connectors.flow.start_device_flow", short_start_device_flow
    )
    agent = _FakeAgent()

    sw.run_device_oauth(agent, PROVIDER)

    poll_timeout = device_flow["timeouts"][-1]
    assert poll_timeout != 150
    assert 60 < poll_timeout < 900


def test_grants_the_agent_after_a_successful_poll(device_flow):
    agent = _FakeAgent()

    state = sw.run_device_oauth(agent, PROVIDER)

    assert state["account_email"] == "kalin@outlook.com"
    assert device_flow["polled"]["grant_agents"] == {
        "installed:email": OUTLOOK_SCOPES
    }
    # Belt-and-suspenders explicit grant, mirroring _run_oauth's own pattern.
    assert device_flow["grants"]
    provider, agent_id, scopes = device_flow["grants"][0]
    assert provider == PROVIDER
    assert agent_id == "installed:email"


def test_scopes_sent_to_the_device_flow_include_identity_scopes(device_flow):
    """Mirrors _connect_scopes — without identity scopes the account shows as
    "default" instead of the real address, the same #2469 divergence bug."""
    agent = _FakeAgent()

    sw.run_device_oauth(agent, PROVIDER)

    started_scopes = device_flow["started"][1]
    for scope in OUTLOOK_SCOPES:
        assert scope in started_scopes
