# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn with no step limit: what still stops it, and what must not.

With no budget, the loop is bounded by the repeated-call guard, Stop, the stall
guard (steps in a row that make no progress), and on a hosted model a check-in
every few hundred steps. None of the budget's machinery — notices, the plan
cap, the step-cap answer — may act on a budget that does not exist.
"""

import argparse
import json

import pytest

from gaia.agents.base import agent as agent_module
from gaia.agents.base.agent import (
    _STALL_STEPS,
    UNLIMITED_STEPS,
    default_max_steps,
    parse_max_steps,
)
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tool_grants import STEP_CHECK_IN_PROMPT_TOOL
from gaia.agents.base.turn_progress import TurnProgress
from gaia.config import GaiaConfig, GaiaConfigError
from tests.unit.agents.test_step_cap_final_answer import (
    _TOOL,
    _answer,
    _make_agent,
    _model_calls,
    _stub_chat,
    _tool_call,
)

pytestmark = pytest.mark.usefixtures("clean_registry")

_OK = {"status": "success", "body": "page text"}
_FAILED = {"status": "error", "error": "404 not found"}
_BUDGET_WORDS = ("Step budget", "steps left", "of the 0 ", "None steps")


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


def _unlimited_agent(tool_result=_OK):
    agent = _make_agent(streaming=False)
    # AgentConsole's spinner sleeps on every step; hundreds of steps need quiet.
    agent.console = SilentConsole()
    agent.max_steps = UNLIMITED_STEPS
    agent.tool_result = tool_result
    return agent


def _sent_texts(agent, chat):
    """Every string the model was sent, across every call of the turn."""
    texts = []
    for call in _model_calls(agent, chat):
        for message in call.kwargs.get("messages") or []:
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
    return texts


# ------------------------------------------------- what still stops the turn


def test_an_unlimited_turn_looping_on_one_call_is_stopped_by_the_loop_guard():
    agent = _unlimited_agent(_FAILED)
    _stub_chat(agent, *[_tool_call(1)] * 50)

    result = agent.process_query("read issue 1", max_steps=0)

    # Stopped within a handful of steps, not after 50, and it says why.
    assert result["steps_taken"] < 10
    assert _TOOL in result["result"]
    assert "kept failing" in result["result"]


def test_a_cycle_the_loop_guard_cannot_see_is_stopped_by_the_stall_guard():
    """Period 4 is past the loop guard's reach; every call after the first lap
    repeats one already made, so nothing is progress."""
    agent = _unlimited_agent()
    cycle = [_tool_call(n) for n in (1, 2, 3, 4)]
    closing = "I kept re-reading the same four issues and got nowhere."
    replies = (cycle * 20)[: 4 + _STALL_STEPS] + [closing]
    chat = _stub_chat(agent, *replies)

    result = agent.process_query("compare issues 1-4")

    assert result["result"].startswith(closing)
    assert result["status"] == "incomplete"
    texts = _sent_texts(agent, chat)
    # Told once, half way, before being stopped.
    assert sum("made no progress" in t and "Change approach" in t for t in texts) >= 1
    last_request = _model_calls(agent, chat)[-1].kwargs
    assert last_request.get("tool_choice") == "none"
    assert any(
        f"last {_STALL_STEPS} steps made no progress" in m.get("content", "")
        for m in last_request["messages"]
        if isinstance(m.get("content"), str)
    )


def test_a_tool_failing_with_new_arguments_each_time_is_still_stopped():
    """The turn-scope guard owns this case, limit or not."""
    failures = iter(range(1, 1000))
    # A different error each time, so the same-error loop guard never matches.
    agent = _unlimited_agent(
        lambda: {"status": "error", "error": f"page {next(failures)} missing"}
    )
    _stub_chat(agent, *[_tool_call(n) for n in range(1, 60)])

    result = agent.process_query("find the missing page")

    assert result["steps_taken"] < 10
    assert "kept failing" in result["result"]


def test_when_the_stall_summary_fails_the_note_names_the_stall_not_a_budget():
    agent = _unlimited_agent()
    cycle = [_tool_call(n) for n in (1, 2, 3, 4)]
    replies = (cycle * 20)[: 4 + _STALL_STEPS]
    _stub_chat(agent, *replies, ConnectionError("gateway down"))

    result = agent.process_query("compare issues 1-4")

    assert "made no progress" in result["result"]
    assert "gateway down" in result["result"]
    assert "ran out of steps" not in result["result"]
    assert "--max-steps" not in result["result"]


def test_stop_still_works_at_step_1000():
    import threading

    agent = _unlimited_agent()
    agent._cancel_event = threading.Event()
    calls = {"n": 0}

    def _result():
        calls["n"] += 1
        if calls["n"] == 1000:
            agent._cancel_event.set()
        return _OK

    agent.tool_result = _result
    _stub_chat(agent, *[_tool_call(n) for n in range(1, 1001)])

    result = agent.process_query("index every issue")

    assert result["steps_taken"] == 1000
    assert "stopped before it finished" in result["result"]


# ------------------------------------------------------- what must not stop it


def test_a_long_turn_making_progress_is_not_stopped_and_hears_no_budget():
    agent = _unlimited_agent()
    replies = [_tool_call(n) for n in range(1, 401)] + [_answer("All 400 read.")]
    chat = _stub_chat(agent, *replies)

    result = agent.process_query("read issues 1-400")

    assert result["result"].startswith("All 400 read.")
    assert result["steps_taken"] == 401
    assert result["max_steps_reached"] is False
    texts = _sent_texts(agent, chat)
    assert not [t for t in texts if any(w in t for w in _BUDGET_WORDS)]
    assert not [t for t in texts if "made no progress" in t]


def test_pacing_calls_are_never_idle_and_progress_resets_the_count():
    progress = TurnProgress({"sleep"})
    for _ in range(500):
        progress.record("sleep", {"seconds": 60}, True)
        assert progress.end_step() == 0
    progress.record("read", {"n": 1}, True)
    assert progress.end_step() == 0
    for _ in range(_STALL_STEPS - 1):
        progress.record("read", {"n": 1}, True)  # a repeat
        progress.end_step()
    assert progress.idle_steps == _STALL_STEPS - 1
    progress.record("read", {"n": 2}, True)
    assert progress.end_step() == 0


def test_a_failed_call_retried_successfully_is_progress():
    progress = TurnProgress()
    progress.record("fetch", {"url": "x"}, False)
    assert progress.end_step() == 1
    progress.record("fetch", {"url": "x"}, True)
    # Seen before, so not new: a retry that finally works is not a new fact
    # unless it is the first success. Repeats are never progress.
    assert progress.end_step() == 2
    progress.record("fetch", {"url": "y"}, True)
    assert progress.end_step() == 0


def test_unhashable_and_unserialisable_args_do_not_crash_the_tracker():
    progress = TurnProgress()
    progress.record("t", {"a": {1, 2}, "b": object()}, True)
    assert progress.end_step() == 0
    progress.record("t", None, True)
    assert progress.end_step() == 0


# ------------------------------------------------- the budget's own machinery


@pytest.mark.parametrize("limit", [None, 0])
def test_the_plan_cap_never_fires_on_a_phantom_budget(limit):
    agent = _make_agent(streaming=False)
    agent.plan_iterations = agent.max_plan_iterations = 3
    assert not any(agent._plan_cap_reached(t, limit) for t in range(0, 5001, 7))


@pytest.mark.parametrize("limit", [None, 0])
def test_no_budget_notice_is_sent_without_a_budget(limit):
    agent = _make_agent(streaming=False)
    messages, conversation = [], []
    for step in range(0, 2000, 3):
        assert agent._announce_step_budget(messages, conversation, step, limit) is None
    assert messages == conversation == []


def test_an_explicit_limit_still_stops_and_its_notices_still_fire():
    agent = _unlimited_agent()
    replies = [_tool_call(n) for n in range(1, 9)] + ["Got through 8 of them."]
    chat = _stub_chat(agent, *replies)

    result = agent.process_query("read issues 1-20", max_steps=8)

    assert result["max_steps_reached"] is True
    assert result["result"].startswith("Got through 8 of them.")
    notices = [t for t in _sent_texts(agent, chat) if t.startswith("Step budget")]
    assert notices
    assert "of 8" in notices[-1]


def test_an_env_limit_caps_an_agent_whose_default_is_unlimited(monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", "3")
    assert default_max_steps(UNLIMITED_STEPS) == 3
    agent = _make_agent(streaming=False)
    assert agent.max_steps == 3


def test_a_huge_explicit_limit_behaves_like_a_real_one():
    agent = _unlimited_agent()
    replies = [_tool_call(1), _tool_call(2), _answer("done")]
    chat = _stub_chat(agent, *replies)

    result = agent.process_query("read two issues", max_steps=10**12)

    assert result["result"].startswith("done")
    assert not [t for t in _sent_texts(agent, chat) if t.startswith("Step budget")]


# ----------------------------------------------------- the hosted check-in


def _hosted(agent, monkeypatch, every=5):
    agent.chat.config.use_claude = True
    agent.chat.effective_model = "claude-sonnet-5"
    monkeypatch.setattr(agent_module, "_HOSTED_CHECK_IN_STEPS", every)
    asked = []

    def _answer_with(answers):
        def _confirm(tool, args):
            asked.append((tool, dict(args)))
            return answers.pop(0)

        agent.console.confirm_tool_execution = _confirm

    return asked, _answer_with


def test_a_hosted_turn_checks_in_and_stops_with_a_summary_when_declined(
    monkeypatch,
):
    agent = _unlimited_agent()
    replies = [_tool_call(n) for n in range(1, 11)] + ["Here is what I have."]
    chat = _stub_chat(agent, *replies)
    asked, answer_with = _hosted(agent, monkeypatch)
    answer_with([True, False])

    result = agent.process_query("read every issue")

    assert asked == [
        (STEP_CHECK_IN_PROMPT_TOOL, {"steps": 5, "model": "claude-sonnet-5"}),
        (STEP_CHECK_IN_PROMPT_TOOL, {"steps": 10, "model": "claude-sonnet-5"}),
    ]
    assert result["result"].startswith("Here is what I have.")
    last = _model_calls(agent, chat)[-1].kwargs
    assert last.get("tool_choice") == "none"
    assert any(
        "Continuing past 10 steps was not approved" in m.get("content", "")
        for m in last["messages"]
        if isinstance(m.get("content"), str)
    )


def test_stop_pressed_at_the_check_in_cancels_without_a_summary(monkeypatch):
    import threading

    agent = _unlimited_agent()
    agent.console.cancelled = threading.Event()
    chat = _stub_chat(agent, *[_tool_call(n) for n in range(1, 6)], "unused")
    _hosted(agent, monkeypatch)

    def _stop_while_asking(tool, args):
        agent.console.cancelled.set()
        return False

    agent.console.confirm_tool_execution = _stop_while_asking

    result = agent.process_query("read every issue")

    assert result["status"] == "cancelled"
    assert result["result"] == ""
    assert len(_model_calls(agent, chat)) == 5  # no paid closing call


def test_a_dropped_client_at_the_check_in_makes_no_closing_call(monkeypatch):
    import threading

    agent = _unlimited_agent()
    agent._cancel_event = threading.Event()
    chat = _stub_chat(agent, *[_tool_call(n) for n in range(1, 6)], "unused")
    _hosted(agent, monkeypatch)

    def _client_gone(tool, args):
        agent._cancel_event.set()
        return False

    agent.console.confirm_tool_execution = _client_gone

    result = agent.process_query("read every issue")

    assert "stopped before it finished" in result["result"]
    assert len(_model_calls(agent, chat)) == 5


def test_full_access_approves_the_check_in_and_the_turn_runs_on(monkeypatch):
    agent = _unlimited_agent()
    agent.console = SilentConsole(auto_approve_gated_tools=True)
    replies = [_tool_call(n) for n in range(1, 13)] + [_answer("All read.")]
    _stub_chat(agent, *replies)
    _hosted(agent, monkeypatch)

    result = agent.process_query("read every issue")

    assert result["result"].startswith("All read.")
    assert result["steps_taken"] == 13


def test_a_local_turn_is_never_asked(monkeypatch):
    agent = _unlimited_agent()
    _stub_chat(agent, *[_tool_call(n) for n in range(1, 13)], _answer("ok"))
    monkeypatch.setattr(agent_module, "_HOSTED_CHECK_IN_STEPS", 5)
    asked = []
    agent.console.confirm_tool_execution = lambda *a: asked.append(a) or False

    result = agent.process_query("read issues")

    assert asked == []
    assert result["result"].startswith("ok")


def test_a_hosted_turn_with_a_limit_is_never_asked(monkeypatch):
    agent = _unlimited_agent()
    _stub_chat(agent, *[_tool_call(n) for n in range(1, 7)], _answer("ok"))
    asked, answer_with = _hosted(agent, monkeypatch)
    answer_with([False, False])

    result = agent.process_query("read issues", max_steps=50)

    assert asked == []
    assert result["result"].startswith("ok")


# ------------------------------------------------------- parsing the limit


@pytest.mark.parametrize(
    "raw, expected",
    [("0", 0), ("7", 7), (" 12 ", 12), ("10000000000", 10_000_000_000)],
)
def test_env_values_that_parse(monkeypatch, raw, expected):
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", raw)
    assert default_max_steps(50) == expected


@pytest.mark.parametrize("raw", ["", "   "])
def test_an_empty_env_is_unset(monkeypatch, raw):
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", raw)
    assert default_max_steps(50) == 50
    assert default_max_steps(UNLIMITED_STEPS) == UNLIMITED_STEPS


@pytest.mark.parametrize("raw", ["-1", "-500", "abc", "1.5", "1e3", "inf", "0x10"])
def test_env_values_that_fail_loudly(monkeypatch, raw):
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", raw)
    with pytest.raises(ValueError, match="GAIA_AGENT_MAX_STEPS"):
        default_max_steps(UNLIMITED_STEPS)


@pytest.mark.parametrize("bad", [-1, "abc", True, 2.5, None])
def test_explicit_limits_that_fail_loudly(bad):
    with pytest.raises(ValueError, match="max_steps"):
        parse_max_steps(bad, "max_steps")


def test_a_negative_per_query_limit_fails_the_turn_loudly():
    agent = _unlimited_agent()
    _stub_chat(agent, _answer("ok"))
    with pytest.raises(ValueError, match="max_steps"):
        agent.process_query("hi", max_steps=-2)


@pytest.mark.parametrize(
    "raw, expected",
    [("0", 0), ("150", 150), ("", None), ("  ", None), ("10000000000", 10**10)],
)
def test_config_set_parses(raw, expected):
    config = GaiaConfig()
    config.set("max_steps", raw)
    assert config.max_steps == expected


@pytest.mark.parametrize("raw", ["-1", "abc", "1.5", "true"])
def test_config_set_rejects(raw):
    with pytest.raises(GaiaConfigError, match="max_steps"):
        GaiaConfig().set("max_steps", raw)


@pytest.mark.parametrize("value", [-1, "5", 1.5, True])
def test_a_bad_config_file_value_fails_loudly(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"max_steps": value}), encoding="utf-8")
    with pytest.raises(GaiaConfigError, match="max_steps"):
        GaiaConfig.load(path)


def test_config_applies_when_env_is_unset_and_env_wins(monkeypatch):
    GaiaConfig.save_key("max_steps", "40")
    assert default_max_steps(UNLIMITED_STEPS) == 40
    assert default_max_steps(50) == 40
    monkeypatch.setenv("GAIA_AGENT_MAX_STEPS", "0")
    assert default_max_steps(50) == UNLIMITED_STEPS
    monkeypatch.delenv("GAIA_AGENT_MAX_STEPS")
    GaiaConfig.save_key("max_steps", "")
    assert default_max_steps(UNLIMITED_STEPS) == UNLIMITED_STEPS
    assert default_max_steps(50) == 50


@pytest.mark.parametrize("raw, expected", [("0", 0), ("25", 25)])
def test_the_cli_flag_accepts(raw, expected):
    from gaia.cli import _step_limit_arg

    assert _step_limit_arg(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "lots", "2.5", ""])
def test_the_cli_flag_rejects(raw):
    from gaia.cli import _step_limit_arg

    with pytest.raises(argparse.ArgumentTypeError, match="0 for no limit"):
        _step_limit_arg(raw)


# ------------------------------------------------------ what front-ends see


@pytest.mark.parametrize(
    "total, message", [(None, "Step 3"), (0, "Step 3"), (50, "Step 3/50")]
)
def test_the_canonical_status_never_shows_a_phantom_total(total, message):
    from gaia.ui.sse_translation import CanonicalTranslator

    translator = CanonicalTranslator(
        "00000000-0000-4000-8000-000000000001", agent_id="gaia", debug=True
    )
    (status,) = translator.translate({"type": "step", "step": 3, "total": total})
    assert status["message"] == message


def test_the_step_event_says_no_limit_with_null():
    from gaia.ui.sse_handler import SSEOutputHandler

    handler = SSEOutputHandler()
    events = []
    handler._emit = events.append
    handler.print_step_header(3, None)
    handler.print_step_header(4, 50)
    assert events[0]["total"] is None
    assert json.loads(json.dumps(events[0]))["total"] is None
    assert events[1]["total"] == 50
