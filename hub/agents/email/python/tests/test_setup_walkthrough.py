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
from gaia.connectors import setup_routes as sr
from gaia_agent_email.tools import setup_walkthrough as sw

PROVIDER = "microsoft"
OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
]

_VALID_GUID = "11112222-bbbb-3333-cccc-4444dddd5555"


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
    """Mirrors connect_scopes — without identity scopes the account shows as
    "default" instead of the real address, the same #2469 divergence bug."""
    agent = _FakeAgent()

    sw.run_device_oauth(agent, PROVIDER)

    started_scopes = device_flow["started"][1]
    for scope in OUTLOOK_SCOPES:
        assert scope in started_scopes


# ---------------------------------------------------------------------------
# The step driver — walks route.steps, narrates, traces, shape-checks.
# ---------------------------------------------------------------------------


def _device_code_steps():
    return sr.steps_for(sr.MS_PERSONAL, sign_in=sr.SIGN_IN_DEVICE_CODE)


def test_walks_every_device_code_step_in_order_and_skips_the_redirect_uri():
    steps = _device_code_steps()
    assert [s.id for s in steps] == [
        "register",
        "account_type",
        "public_client_flows",
        "permissions",
        "client_id",
    ]
    agent = _FakeAgent(answers=["done", "done", "done", "done", _VALID_GUID])

    collected, trace = sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    for step in steps:
        assert any(step.title in m for m in agent.console.info)
    # The redirect-URI step is loopback-only — never narrated on this route.
    redirect_step = next(s for s in sr.MS_PERSONAL.steps if s.loopback_only)
    assert not any(redirect_step.title in m for m in agent.console.info)
    assert collected["client_id"] == _VALID_GUID


def test_every_step_appends_a_trace_entry_never_claiming_unearned_verification():
    agent = _FakeAgent(answers=["done", "done", "done", "done", _VALID_GUID])

    _, trace = sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    steps = _device_code_steps()
    assert [t["step_id"] for t in trace] == [s.id for s in steps]
    for step, entry in zip(steps, trace):
        if not step.verifiable:
            assert entry["verified"] is False, entry
    # The one verifiable step (client_id) IS traced verified once its shape
    # check passes.
    assert trace[-1] == {"step_id": "client_id", "verified": True}


def test_cannot_see_portal_notice_is_said_exactly_once_at_the_first_step():
    agent = _FakeAgent(answers=["done", "done", "done", "done", _VALID_GUID])

    sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    mentions = [m for m in agent.console.info if "can't see your screen" in m]
    assert len(mentions) == 1


def test_client_id_shape_check_rejects_a_non_guid_without_echoing_it():
    bogus = "not-even-close-to-a-guid"
    agent = _FakeAgent(answers=["done", "done", "done", "done", bogus, _VALID_GUID])

    collected, trace = sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    assert collected["client_id"] == _VALID_GUID
    # The shape-check failure message is an AUTHORED CONSTANT — it must never
    # echo back the value the user pasted (that is how a credential re-enters
    # the transcript).
    assert not any(bogus in m for m in agent.console.info)
    assert any("doesn't look like an Application" in m for m in agent.console.info)


def test_credential_prompt_never_offers_done_stuck_options():
    """AC8 half: a credential ask is a plain free-text prompt, never the
    navigation lane's Done/I'm-stuck options."""
    agent = _FakeAgent(answers=["done", "done", "done", "done", _VALID_GUID])

    sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    credential_call = agent.console.asked[-1]
    assert credential_call["options"] == []
    assert credential_call["allow_free_text"] is True


def test_im_stuck_ends_the_walkthrough_honestly():
    agent = _FakeAgent(answers=["done", "stuck"])

    with pytest.raises(sw.WalkthroughStuck) as exc:
        sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    assert exc.value.step.id == "account_type"
    # No further steps were narrated past the one the user got stuck on.
    later_titles = [s.title for s in _device_code_steps()[2:]]
    assert not any(title in m for title in later_titles for m in agent.console.info)
    # The exception's own message is what gets shown to the user (matching
    # the existing _Declined pattern) — it must name the doc link and never
    # improvise a substitute response.
    assert "amd-gaia.ai" in str(exc.value)
    assert "account_type" not in str(exc.value)  # the step's id, not its title


# ---------------------------------------------------------------------------
# FAQ lane — selection, never composition. Navigation prompts only.
# ---------------------------------------------------------------------------


def test_a_step_level_faq_answer_is_emitted_byte_for_byte():
    """The invariant, asserted end-to-end: what got printed IS the authored
    QA.answer — imported from the real route, compared with ==, not `in`. A
    function-level test of the lookup alone would pass even if the driver
    prepended something like "Good question — "."""
    register_step = next(s for s in sr.MS_PERSONAL.steps if s.id == "register")
    qa = register_step.faq[0]
    assert "which account" in qa.question_hints

    agent = _FakeAgent(
        answers=[
            "which account should I use?",
            "done",
            "done",
            "done",
            "done",
            _VALID_GUID,
        ]
    )

    sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    # List membership on a list of strings is Python `==` per element — the
    # exact-match check the plan requires, not a substring `in` check that
    # would also pass if the driver had prepended something to it.
    assert qa.answer in agent.console.info


def test_a_route_level_faq_answer_is_reachable_from_any_step():
    """The client-secret FAQ lives on the ROUTE, not any one step — it must
    still resolve when asked during a step with no matching step-level FAQ."""
    route_qa = next(qa for qa in sr.MS_PERSONAL.faq if "secret" in qa.question_hints)

    agent = _FakeAgent(
        answers=[
            "done",  # register
            "do I need a client secret for this?",  # account_type — no step FAQ
            "done",
            "done",
            "done",
            _VALID_GUID,
        ]
    )

    sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    assert route_qa.answer in agent.console.info


def test_an_unmatched_question_returns_the_authored_no_match_string():
    agent = _FakeAgent(
        answers=[
            "what is the airspeed velocity of an unladen swallow?",
            "done",
            "done",
            "done",
            "done",
            _VALID_GUID,
        ]
    )

    sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    assert sw._FAQ_NO_MATCH in agent.console.info


def test_faq_turn_re_asks_with_the_same_options_and_free_text_still_enabled():
    """A credential prompt is never given the FAQ lane (zero options), and no
    NAVIGATION prompt is ever re-issued with allow_free_text=False and zero
    options — asserted across the whole scripted walk, not one call."""
    agent = _FakeAgent(
        answers=[
            "a question nobody wrote an answer for",
            "done",
            "done",
            "done",
            "done",
            _VALID_GUID,
        ]
    )

    sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    for call in agent.console.asked:
        if call["allow_free_text"] is False:
            assert call["options"], "allow_free_text=False with zero options"
        # Every navigation prompt (has options) keeps allow_free_text True —
        # the FAQ lane is never silently withdrawn mid-walk.
        if call["options"]:
            assert call["allow_free_text"] is True


def test_a_genuine_question_at_the_credential_step_gets_its_authored_answer():
    """Azure's Overview blade shows THREE GUIDs side by side (Application
    client ID, Object ID, Directory/tenant ID) — a shape check can't tell
    them apart, so this is the one place a guided walkthrough answering
    questions genuinely earns its keep. A question-shaped answer must get
    its authored FAQ answer and a re-ask, not the generic shape error."""
    client_id_step = next(s for s in sr.MS_PERSONAL.steps if s.id == "client_id")
    qa = next(
        qa
        for qa in client_id_step.faq
        if any(h in qa.question_hints for h in ("which id", "tenant"))
    )
    agent = _FakeAgent(
        answers=[
            "done",
            "done",
            "done",
            "done",
            "which id is it? there are three on this page",
            _VALID_GUID,
        ]
    )

    collected, _trace = sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    assert collected["client_id"] == _VALID_GUID
    assert qa.answer in agent.console.info
    # The generic shape error was never shown for a question-shaped answer.
    assert sw._CLIENT_ID_SHAPE_ERROR not in agent.console.info


def test_a_malformed_credential_value_still_gets_the_shape_error():
    """A value that ISN'T a question (doesn't match any FAQ hint) is still
    treated as a malformed literal, not silently swallowed by the FAQ lane."""
    bogus = "xxxxxxxx-not-a-guid-at-all"
    agent = _FakeAgent(
        answers=["done", "done", "done", "done", bogus, _VALID_GUID]
    )

    collected, _trace = sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    assert collected["client_id"] == _VALID_GUID
    assert sw._CLIENT_ID_SHAPE_ERROR in agent.console.info
    assert not any(bogus in m for m in agent.console.info)


def test_credential_prompt_still_has_zero_options_with_the_faq_lane_added():
    """The FAQ lookup on the credential step is a lookup, not a lane change —
    the prompt itself stays a plain free-text ask, never gains Done/Stuck
    options (which would let a real client ID collide with an option value)."""
    agent = _FakeAgent(
        answers=["done", "done", "done", "done", "what's a tenant id?", _VALID_GUID]
    )

    sw.run_setup_walkthrough(agent, sr.MS_PERSONAL)

    credential_calls = [
        c for c in agent.console.asked if c["message"].startswith("Paste the value")
    ]
    assert len(credential_calls) == 2  # the question turn, then the real value
    for call in credential_calls:
        assert call["options"] == []
        assert call["allow_free_text"] is True
