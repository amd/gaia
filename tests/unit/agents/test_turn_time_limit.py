# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn with a known time limit warns once, then wraps up before it.

Without this a run killed at its wall-clock limit ended with nothing written:
the model never knew it had a deadline, and the kill left no answer behind.
Only a limit the caller set counts; with none, nothing changes.
"""

import json
import time as real_time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base import agent as agent_module
from gaia.agents.base.agent import Agent, TurnCancel
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_LOOKUP = "lookup_for_time_limit_test"
_TARGET = "out/OUTPUT.md"
_REQUEST = f"Fix the loader and write `{_TARGET}` with what you changed."
_STATS = {"input_tokens": 100, "output_tokens": 10}


class _Clock:
    """``time`` for the agent module, with a monotonic clock the test moves."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(real_time, name)


class _Agent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def lookup_for_time_limit_test(path: str) -> dict:
            """Look something up."""
            return {"status": "success", "path": path}

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            target = Path(file_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"status": "success", "file_path": str(target)}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(agent_module, "time", fake)
    return fake


@pytest.fixture
def agent(tmp_path, monkeypatch, clock):  # pylint: disable=unused-argument
    snapshot = dict(_TOOL_REGISTRY)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GAIA_AGENT_BUDGET_NOTICES", raising=False)
    with patch("gaia.agents.base.agent.AgentSDK"):
        made = _Agent(
            silent_mode=True,
            skip_lemonade=True,
            model_id=DEFAULT_MODEL_NAME,
            max_steps=50,
        )
    made.streaming = False
    made.console.auto_approve_gated_tools = True
    yield made
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _stub_chat(agent, clock, replies, tick):
    """Each model call takes *tick* seconds of the turn's time."""
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.sent = []

    def _send(*_, **kwargs):
        chat.sent.append([dict(m) for m in kwargs.get("messages") or []])
        clock.now += tick
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(text=item, stats=dict(_STATS))

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return chat


def _lookup(n: int) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"c{n}",
                    "type": "function",
                    "function": {
                        "name": _LOOKUP,
                        "arguments": json.dumps({"path": f"src/{n}"}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _write() -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "w1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"file_path": _TARGET, "content": "# Changes\n"}
                        ),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _answer(text: str) -> str:
    return json.dumps({"thought": "", "answer": text})


def _notices(chat) -> list:
    return [
        m["content"]
        for sent in chat.sent
        for m in sent
        if m.get("role") == "user" and "Time budget:" in str(m.get("content"))
    ]


def _notices_in(sent) -> bool:
    return any("Time budget:" in str(m.get("content")) for m in sent)


def test_one_notice_near_the_limit_names_the_missing_file(agent, clock):
    agent.time_limit_s = 100
    replies = [_lookup(n) for n in range(1, 8)] + [_answer("Done.")]
    chat = _stub_chat(agent, clock, replies, 10)

    agent.process_query(_REQUEST)

    # Steps of 10s: the notice lands at 70s, short of the stop at 80s.
    first_with_notice = next(i for i, s in enumerate(chat.sent) if _notices_in(s))
    assert first_with_notice == 7
    notices = set(_notices(chat))
    assert len(notices) == 1
    assert f"`{_TARGET}`" in notices.pop()


def test_the_turn_stops_short_of_the_limit_and_still_writes(agent, clock, tmp_path):
    agent.time_limit_s = 100
    replies = [_lookup(n) for n in range(1, 9)] + [_write(), _answer("Wrote it.")]
    chat = _stub_chat(agent, clock, replies, 10)

    result = agent.process_query(_REQUEST)

    # The stop keeps two of the slowest steps in hand: 100 - 2 x 10 = 80s.
    assert chat.send_messages.call_count == 10
    assert "time limit" in chat.sent[8][-1]["content"]
    assert "time limit" in chat.sent[9][-1]["content"]
    assert (tmp_path / _TARGET).read_text(encoding="utf-8") == "# Changes\n"
    assert result["time_limit_reached"] is True
    assert result["max_steps_reached"] is False
    assert result["status"] == "incomplete"
    assert result["result"].startswith("Wrote it.")


def test_slow_steps_move_the_stop_earlier(agent, clock):
    """Boundary: a tenth of a short limit can't hold two slow model calls."""
    agent.time_limit_s = 60
    chat = _stub_chat(agent, clock, [_lookup(1), _lookup(2), _answer("Done.")], 15)

    result = agent.process_query("Is the loader in src/ right?")

    # 15s steps: stop once 60 - 30 = 30s are used, after the second step.
    assert chat.send_messages.call_count == 3
    assert result["time_limit_reached"] is True


def test_a_failed_closing_call_names_the_time_limit_not_steps(agent, clock):
    agent.time_limit_s = 10
    _stub_chat(agent, clock, [_lookup(1), RuntimeError("gateway 500")], 9.5)

    result = agent.process_query("Is the loader in src/ right?")

    assert "10-second time limit" in result["result"]
    assert "ran out of steps" not in result["result"]


def test_a_hard_stop_at_the_limit_is_reported_incomplete(agent, clock):
    """The caller's timer fired mid-step: never a success, always the limit."""
    agent.time_limit_s = 100
    cancel = TurnCancel()
    agent._cancel_event = cancel
    chat = _stub_chat(agent, clock, [_lookup(1)], 10)

    def _slow_then_stopped(*args, **kwargs):
        reply = chat_send(*args, **kwargs)
        clock.now += 90
        cancel.cancel("Stopped at the 100-second time limit.")
        return reply

    chat_send = chat.send_messages.side_effect
    chat.send_messages.side_effect = _slow_then_stopped

    result = agent.process_query("Is the loader in src/ right?")

    assert result["result"].startswith("Stopped at the 100-second time limit.")
    assert result["time_limit_reached"] is True
    assert result["status"] == "incomplete"


def _lookup_with_prose(n: int, prose: str) -> str:
    return json.dumps(
        {"thought": prose, "tool": _LOOKUP, "tool_args": {"path": f"src/{n}"}}
    )


def _stopped_after_first_call(agent, clock, replies, message, limit=None):
    agent.time_limit_s = limit
    cancel = TurnCancel()
    agent._cancel_event = cancel
    chat = _stub_chat(agent, clock, replies, 10)
    chat_send = chat.send_messages.side_effect

    def _stopped(*args, **kwargs):
        reply = chat_send(*args, **kwargs)
        if limit:
            clock.now += limit
        cancel.cancel(message)
        return reply

    chat.send_messages.side_effect = _stopped
    return agent.process_query("Is the loader in src/ right?")


def test_a_hard_stop_at_the_limit_keeps_what_was_found(agent, clock):
    """No closing call fits past the deadline, so the latest prose is the answer."""
    result = _stopped_after_first_call(
        agent,
        clock,
        [_lookup_with_prose(1, "The loader skips src/ when it is a symlink.")],
        "Stopped at the 100-second time limit.",
        limit=100,
    )

    answer = result["result"]
    assert answer.startswith("Stopped at the 100-second time limit.")
    assert "What I had found so far:" in answer
    assert "The loader skips src/ when it is a symlink." in answer
    assert result["time_limit_reached"] is True


def test_a_hard_stop_with_nothing_found_adds_no_empty_findings(agent, clock):
    result = _stopped_after_first_call(
        agent, clock, [_lookup(1)], "Stopped at the 100-second time limit.", limit=100
    )

    assert "What I had found so far" not in result["result"]


def test_a_plain_stop_does_not_carry_findings(agent, clock):
    """Stop or a disconnect is not a deadline: its wording stays as it was."""
    result = _stopped_after_first_call(
        agent,
        clock,
        [_lookup_with_prose(1, "The loader skips src/ when it is a symlink.")],
        "The chat went idle.",
    )

    assert "What I had found so far" not in result["result"]
    assert result["time_limit_reached"] is False


def test_a_stop_with_a_message_and_no_limit_is_not_a_time_limit(agent, clock):
    cancel = TurnCancel()
    agent._cancel_event = cancel
    chat = _stub_chat(agent, clock, [_lookup(1)], 10)
    chat_send = chat.send_messages.side_effect

    def _stopped(*args, **kwargs):
        reply = chat_send(*args, **kwargs)
        cancel.cancel("The chat went idle.")
        return reply

    chat.send_messages.side_effect = _stopped

    result = agent.process_query("Is the loader in src/ right?")

    assert result["time_limit_reached"] is False


def test_no_limit_means_no_notice_and_no_stop(agent, clock):
    chat = _stub_chat(
        agent, clock, [_lookup(1), _lookup(2), _answer("All good.")], 10_000
    )

    result = agent.process_query(_REQUEST)

    assert _notices(chat) == []
    assert result["time_limit_reached"] is False
    assert result["result"].startswith("All good.")


@pytest.mark.parametrize("limit", [0, -5, None])
def test_a_zero_or_negative_limit_is_no_limit(agent, clock, limit):
    """Boundary: nothing here may invent a limit from a bad value."""
    agent.time_limit_s = limit
    chat = _stub_chat(agent, clock, [_lookup(1), _answer("Fine.")], 10_000)

    result = agent.process_query("Is the loader in src/ right?")

    assert _notices(chat) == []
    assert result["time_limit_reached"] is False


def test_notices_off_still_stops_at_the_limit(agent, clock, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", "0")
    agent.time_limit_s = 100
    chat = _stub_chat(agent, clock, [_lookup(1), _answer("Stopped early.")], 50)

    result = agent.process_query("Is the loader in src/ right?")

    assert _notices(chat) == []
    assert result["time_limit_reached"] is True


def test_the_limit_is_measured_per_turn(agent, clock):
    """A second turn on the same agent gets the whole limit again."""
    agent.time_limit_s = 100
    _stub_chat(agent, clock, [_lookup(1), _answer("First.")], 40)
    agent.process_query("Is the loader in src/ right?")

    clock.now += 500
    chat = _stub_chat(agent, clock, [_lookup(2), _answer("Second.")], 10)
    result = agent.process_query("And the writer?")

    assert result["time_limit_reached"] is False
    assert _notices(chat) == []


_OPENING = "This turn has 100 seconds in all."


def test_the_model_is_told_the_limit_with_its_request(agent, clock):
    """Hearing of the limit only at 70% leaves the deliverable for the end."""
    agent.time_limit_s = 100
    chat = _stub_chat(agent, clock, [_lookup(1), _answer("Fine.")], 10)

    agent.process_query(_REQUEST)

    first = chat.sent[0][-1]
    assert first["role"] == "user"
    assert first["content"].startswith(_REQUEST)
    assert _OPENING in first["content"]
    assert "write a first working version early" in first["content"]
    # One user message, not two in a row: some chat templates refuse that.
    assert [m["role"] for m in chat.sent[0]].count("user") == 1


def test_the_saved_history_keeps_the_users_own_words(agent, clock):
    agent.time_limit_s = 100
    _stub_chat(agent, clock, [_answer("Fine.")], 10)
    result = agent.process_query(_REQUEST)

    asked = [m for m in result["conversation"] if m.get("role") == "user"]
    assert asked[0]["content"] == _REQUEST

    agent.time_limit_s = None
    chat = _stub_chat(agent, clock, [_answer("Second.")], 10)
    agent.process_query("And the writer?")

    assert all(_OPENING not in str(m.get("content")) for m in chat.sent[0])


@pytest.mark.parametrize("limit", [0, -5, None])
def test_no_limit_adds_nothing_to_the_request(agent, clock, limit):
    agent.time_limit_s = limit
    chat = _stub_chat(agent, clock, [_answer("Fine.")], 10)

    agent.process_query(_REQUEST)

    assert chat.sent[0][-1]["content"] == _REQUEST


def test_notices_off_adds_nothing_to_the_request(agent, clock, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", "0")
    agent.time_limit_s = 100
    chat = _stub_chat(agent, clock, [_answer("Fine.")], 10)

    agent.process_query(_REQUEST)

    assert chat.sent[0][-1]["content"] == _REQUEST


class _Timed:
    """The agent attributes the run-time cap reads, with a clock the test sets."""

    time_capped_run_seconds = Agent.time_capped_run_seconds
    _time_to_stop_s = Agent._time_to_stop_s

    def __init__(self, limit, used, slowest=0.0):
        self.time_limit_s = limit
        self._turn_started_at = 1000.0 - used if used is not None else None
        self._slowest_step_s = slowest


@pytest.mark.parametrize(
    "limit, used, asked, expected",
    [
        (None, 0, 600, 600),  # no limit: untouched
        (720, None, 600, 600),  # no turn running: untouched
        (720, 100, 600, 548),  # 720 * 0.9 - 100 left before the stop point
        (720, 100, 30, 30),  # a short run already fits
        (720, 645, 600, 0),  # under 5 s left: start nothing
        (720, 700, 600, 0),  # past the stop point
    ],
)
def test_a_run_is_cut_to_end_before_the_stop_point(limit, used, asked, expected):
    clock = _Clock()
    with patch.object(agent_module, "time", clock):
        assert _Timed(limit, used).time_capped_run_seconds(asked) == expected


def test_the_shell_caps_a_default_timeout_and_refuses_with_no_time_left():
    from gaia.agents.base.verification import OUT_OF_TIME
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    class _Host(ShellToolsMixin):
        time_capped_run_seconds = Agent.time_capped_run_seconds
        _time_to_stop_s = Agent._time_to_stop_s
        _slowest_step_s = 0.0

        def __init__(self, limit, used):
            self.time_limit_s = limit
            self._turn_started_at = 1000.0 - used
            self.applied = None

        def full_access_active(self):
            return True

        def _run_full_access(self, command, cwd, timeout, timeout_class):
            self.applied = timeout
            return {"status": "success", "timeout": timeout}

    def run(host):
        host.register_shell_tools()
        return _TOOL_REGISTRY["run_shell_command"]["function"]("python train.py")

    saved = dict(_TOOL_REGISTRY)
    clock = _Clock()
    try:
        with patch.object(agent_module, "time", clock):
            capped = _Host(720, 100)
            assert run(capped)["timeout"] == 548
            spent = _Host(720, 700)
            refused = run(spent)
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
    assert spent.applied is None
    assert refused == {**OUT_OF_TIME, "command": "python train.py"}
    assert refused["executed"] is False


def test_the_cap_ends_at_the_same_stop_point_the_loop_uses():
    """Slow steps move the stop earlier; a run capped to 90% would overrun it."""
    clock = _Clock()
    with patch.object(agent_module, "time", clock):
        # 600 s limit, slowest step 60 s: the stop is at 600 - 120 = 480 s.
        assert _Timed(600, 300, slowest=60).time_capped_run_seconds(900) == 180
        assert _Timed(600, 300).time_capped_run_seconds(900) == 240


def test_the_loop_stops_exactly_where_the_cap_ends(agent, clock):
    agent.time_limit_s = 600
    agent._turn_started_at = clock.now
    agent._slowest_step_s = 60.0
    clock.now += 479
    assert agent._time_limit_passed() is False
    assert agent.time_capped_run_seconds(900) == 0  # 1 s left: start nothing
    clock.now += 1
    assert agent._time_limit_passed() is True


def test_a_run_the_limit_cut_short_is_not_told_to_rescope_or_ask_for_longer():
    from gaia.agents.base.verification import TIME_LIMIT_CUT_RUN
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    class _Host(ShellToolsMixin, _Timed):
        pass

    clock = _Clock()
    with patch.object(agent_module, "time", clock):
        cut = _Host(720, 648)._timeout_hint("script", ".")
        not_cut = _Host(720, 100)._timeout_hint("script", ".")
        no_limit = _Host(None, 0)._timeout_hint("script", ".")
    assert cut == TIME_LIMIT_CUT_RUN
    assert "time limit" not in not_cut and "It ran as a 'script' command" in not_cut
    assert no_limit == not_cut


def test_sleep_and_wait_for_condition_stop_short_of_the_limit():
    from gaia.agents.base.verification import OUT_OF_TIME
    from gaia.agents.tools.shell_tools import ShellToolsMixin
    from gaia.agents.tools.wait_tools import WaitToolsMixin

    class _Host(ShellToolsMixin, WaitToolsMixin, _Timed):
        slept = None

        def _require_stop_channels(self):
            return None

        def _wait_interrupted(self):
            return False

    saved = dict(_TOOL_REGISTRY)
    clock = _Clock()
    try:
        with patch.object(agent_module, "time", clock):
            spent = _Host(720, 646)
            assert spent._sleep(120, "rate limit") == OUT_OF_TIME
            spent.register_shell_tools()
            waited = _TOOL_REGISTRY["wait_for_condition"]["function"]("true")
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
    assert waited == OUT_OF_TIME
