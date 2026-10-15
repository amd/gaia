# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Step-budget notices, the plan cap and the step-cap answer under pressure."""

import json
import os
from unittest import mock

import pytest

from gaia.agents.base.agent import _BUDGET_NOTICES, default_max_steps
from tests.unit.agents.test_step_cap_final_answer import (
    _FINDINGS,
    _TOOL,
    _answer,
    _make_agent,
    _model_calls,
    _stub_chat,
    _tool_call,
)

pytestmark = pytest.mark.usefixtures("clean_registry")


@pytest.fixture
def clean_registry():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_MAX_STEPS", raising=False)
    monkeypatch.delenv("GAIA_AGENT_BUDGET_NOTICES", raising=False)


def _notices(messages):
    return [
        m["content"]
        for m in messages
        if isinstance(m.get("content"), str) and m["content"].startswith("Step budget")
    ]


# ------------------------------------------------- notices never outlive a turn


def test_notices_never_reach_a_later_turn_across_three_turns():
    agent = _make_agent(streaming=False)
    agent.tool_result = {"status": "success", "body": "datasheet text"}
    for turn in range(3):
        chat = _stub_chat(agent, *[_tool_call(n) for n in range(1, 9)], _answer("ok"))
        # The loop keeps mutating one list, so copy what each call really sent.
        seen, reply = [], chat.send_messages.side_effect

        def _send(*a, _reply=reply, _seen=seen, **kw):
            _seen.append([dict(m) for m in kw["messages"]])
            return _reply(*a, **kw)

        chat.send_messages.side_effect = _send
        result = agent.process_query(f"summarize issue {turn}", max_steps=8)
        # A fresh set every turn: the levels re-arm, and nothing piles up.
        assert len(_notices(seen[-1])) == len(_BUDGET_NOTICES)
        assert _notices(seen[0]) == []
        assert _notices(result["model_messages"]) == []
        # What a session saves is what the next turn starts from.
        agent.conversation_history = result["model_messages"]
    assert _notices(agent.conversation_history) == []


# --------------------------------------------- each level fires once, no matter


@pytest.fixture
def host():
    return _make_agent(streaming=False)


def test_a_retried_step_never_repeats_a_notice(host):
    messages, conversation = [], []
    sent = []
    # Retries re-announce the same step; a step count can also step back.
    for step in (38, 40, 40, 40, 39, 41, 40, 50, 50, 49, 55, 55, 56, 60, 60):
        notice = host._announce_step_budget(messages, conversation, step, 60)
        if notice:
            sent.append(step)
    assert sent == [40, 50, 55]
    assert len(messages) == len(conversation) == 3


def test_a_limit_raised_mid_turn_without_a_reset_does_not_refire(host):
    messages, conversation = [], []
    assert host._announce_step_budget(messages, conversation, 55, 60)
    # More room, but the turn already heard "5 left": nothing less urgent now.
    for step in range(56, 110):
        assert host._announce_step_budget(messages, conversation, step, 110) is None


def test_turning_notices_off_between_turns_takes_effect(monkeypatch):
    agent = _make_agent(streaming=False)
    agent.tool_result = {"status": "success", "body": "x"}
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", "0")
    chat = _stub_chat(agent, *[_tool_call(n) for n in range(1, 9)], _answer("ok"))
    agent.process_query("summarize issue 1", max_steps=8)
    assert _notices(_model_calls(agent, chat)[-1].kwargs["messages"]) == []


def test_a_typo_in_the_notice_switch_fails_the_turn_loudly(monkeypatch):
    agent = _make_agent(streaming=False)
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", "maybe")
    _stub_chat(agent, _answer("ok"))
    with pytest.raises(ValueError, match="GAIA_AGENT_BUDGET_NOTICES"):
        agent.process_query("hi", max_steps=8)


# ---------------------------------------------------------- the step limit


@pytest.mark.parametrize("bad", ["abc", "-1", "-5", "1.5", " 7 x"])
def test_an_invalid_step_limit_env_raises_at_construction(monkeypatch, bad):
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", bad)
    with pytest.raises(ValueError, match="GAIA_AGENT_MAX_STEPS"):
        _make_agent(streaming=False)
    with pytest.raises(ValueError, match=r"default \(150\)"):
        default_max_steps(150)


def test_the_env_limit_is_honoured_and_an_explicit_one_wins(monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", "12")
    assert _make_agent(streaming=False).max_steps == 12
    assert default_max_steps(150) == 12
    with mock.patch("gaia.agents.base.agent.AgentSDK"):
        from tests.unit.agents.test_step_cap_final_answer import _DummyAgent

        explicit = _DummyAgent(silent_mode=True, skip_lemonade=True, max_steps=5)
    assert explicit.max_steps == 5


def test_a_per_query_limit_overrides_the_agents(monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", "40")
    agent = _make_agent(streaming=False)
    agent.tool_result = {"status": "success", "body": "x"}
    chat = _stub_chat(agent, _tool_call(1), _tool_call(2), _answer("Summary."))
    result = agent.process_query("summarize issue 42", max_steps=2)
    assert result["max_steps_reached"] is True
    assert len(_model_calls(agent, chat)) == 3


def test_the_flagship_limit_follows_the_same_rules(monkeypatch):
    gaia_agent = pytest.importorskip("gaia_agent.agent")
    # No limit by default: 0 is the "no limit" value everywhere one is taken.
    assert gaia_agent.flagship_max_steps() == 0
    assert gaia_agent.GaiaAgentConfig().max_steps == 0
    assert gaia_agent.GaiaAgentConfig(max_steps=9).max_steps == 9
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", "33")
    assert gaia_agent.GaiaAgentConfig().max_steps == 33
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", "lots")
    with pytest.raises(ValueError, match=r"default \(no limit\)"):
        gaia_agent.GaiaAgentConfig()


# ------------------------------------------- running out never yields nothing


@pytest.mark.parametrize("closing", ["", "   \n\t", ConnectionError("gateway down")])
def test_running_out_with_no_prose_at_all_still_answers(closing):
    agent = _make_agent(streaming=False)
    agent.tool_result = {"status": "success", "body": "x"}
    _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), closing)
    result = agent.process_query("summarize issue 42", max_steps=3)
    assert result["result"].strip()
    assert "I ran out of steps" in result["result"]
    assert f"{_TOOL}: 3x" in result["result"]


@pytest.mark.parametrize("closing", ["", "  ", ConnectionError("gateway down")])
def test_a_failed_closing_reply_keeps_the_earlier_findings(closing):
    agent = _make_agent(streaming=False, model_id=None)
    calls = [
        json.dumps(
            {
                "thought": f"Step {n}. {_FINDINGS}",
                "tool": _TOOL,
                "tool_args": {"number": n},
            }
        )
        for n in (1, 2, 3)
    ]
    _stub_chat(agent, *calls, closing)
    result = agent.process_query("Save the recommendation as report.md", max_steps=3)
    assert f"Step 3. {_FINDINGS}" in result["result"]
    assert "report.md" in result["result"]


def test_huge_findings_are_cut_not_dropped():
    agent = _make_agent(streaming=False)
    long_text = "Finding: " + "price row; " * 2000
    conversation = [{"role": "assistant", "content": {"thought": long_text}}]
    latest = agent._latest_findings(conversation)
    assert latest.startswith("Finding: price row;")
    assert latest.endswith("…")
    assert len(latest) <= agent._LATEST_FINDINGS_CHARS + 1


def test_a_raw_tool_call_envelope_is_not_mistaken_for_findings():
    agent = _make_agent(streaming=False)
    conversation = [
        {"role": "assistant", "content": "The board costs $95."},
        {"role": "assistant", "content": '{"tool": "x", "tool_args": {}}'},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c"}]},
    ]
    assert agent._latest_findings(conversation) == "The board costs $95."


# ------------------------------------------------------------ the plan cap


@pytest.mark.parametrize("limit", [8, 9, 10, 50, 51, 150])
def test_the_plan_cap_binds_exactly_from_half_the_budget(limit):
    agent = _make_agent(streaming=False)
    agent.plan_iterations = agent.max_plan_iterations = 3
    fired = [t for t in range(limit + 1) if agent._plan_cap_reached(t, limit)]
    assert fired[0] == limit - limit // 2
    assert fired == list(range(fired[0], limit + 1))


# --------------------------------- the kw-02 gate keeps the substance it judges

_MULTI = (
    "## Recommendation\n\nCM5 8GB carrier board.\n\n"
    "| Part | Qty | Unit |\n|---|---|---|\n| CM5 8GB | 40 | $95.00 |\n\n"
    "PSU price unverified. I can't confirm the cooler price either."
)


@pytest.mark.parametrize("streaming", [False, True])
def test_the_step_cap_gate_keeps_every_line_of_the_findings(streaming):
    agent = _make_agent(streaming=streaming)
    agent.tool_result = {"status": "success", "body": "datasheet"}
    _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), _MULTI)
    result = agent.process_query("Save the BOM as bom.xlsx", max_steps=3)
    answer = result["result"]
    assert answer.startswith(_MULTI)
    after = answer.split(_MULTI, 1)[1]
    assert "I can't confirm this is done" in after
    assert "bom.xlsx" in after
    assert result["status"] == "incomplete"


def test_a_json_closing_reply_keeps_its_answer_not_the_envelope():
    agent = _make_agent(streaming=False, model_id=None)
    agent.tool_result = {"status": "success", "body": "datasheet"}
    calls = [
        json.dumps({"thought": "look", "tool": _TOOL, "tool_args": {"number": n}})
        for n in (1, 2, 3)
    ]
    _stub_chat(agent, *calls, _answer(_FINDINGS))
    result = agent.process_query("Save the recommendation as report.md", max_steps=3)
    assert result["result"].startswith(_FINDINGS)
    assert '"answer"' not in result["result"]
    assert "report.md" in result["result"].split(_FINDINGS, 1)[1]


def test_without_a_gap_the_findings_are_not_followed_by_a_report():
    agent = _make_agent(streaming=False)
    agent.tool_result = {"status": "success", "body": "datasheet"}
    _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), _FINDINGS)
    result = agent.process_query("What does the CM5 cost?", max_steps=3)
    assert result["result"].startswith(_FINDINGS)
    assert "I can't confirm this is done" not in result["result"]


def test_an_env_free_run_uses_the_base_default():
    assert "GAIA_AGENT_MAX_STEPS" not in os.environ
    assert default_max_steps() == 50
