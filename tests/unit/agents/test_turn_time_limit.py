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


def _marks(chat) -> list:
    seen = []
    for sent in chat.sent:
        for m in sent:
            text = str(m.get("content"))
            if m.get("role") == "user" and text.startswith("Time check:"):
                if text not in seen:
                    seen.append(text)
    return seen


def test_the_model_is_told_the_time_used_at_a_quarter_and_at_half(agent, clock):
    """With no clock it guessed, and one turn quit a third of the way in."""
    agent.time_limit_s = 100
    replies = [_lookup(n) for n in range(1, 8)] + [_answer("Done.")]
    chat = _stub_chat(agent, clock, replies, 10)

    agent.process_query(_REQUEST)

    assert _marks(chat) == [
        "Time check: 30 of 100 seconds used.",
        "Time check: 50 of 100 seconds used.",
    ]


def test_one_slow_step_past_both_marks_sends_one_time_check(agent, clock):
    agent.time_limit_s = 1000
    replies = [_lookup(n) for n in range(1, 6)] + [_answer("Done.")]
    chat = _stub_timed_chat(agent, clock, replies, [5, 5, 5, 540, 5])

    agent.process_query(_REQUEST)

    assert _marks(chat) == ["Time check: 555 of 1000 seconds used."]


def test_notices_off_sends_no_time_check(agent, clock, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", "0")
    agent.time_limit_s = 100
    chat = _stub_chat(agent, clock, [_lookup(1), _lookup(2), _answer("Done.")], 30)

    agent.process_query(_REQUEST)

    assert _marks(chat) == []


def test_the_notice_keeps_caveats_out_of_the_file_and_a_right_file_as_it_is(
    agent, clock
):
    agent.time_limit_s = 100
    replies = [_lookup(n) for n in range(1, 8)] + [_answer("Done.")]
    chat = _stub_chat(agent, clock, replies, 10)

    agent.process_query(_REQUEST)

    notice = _notices(chat)[0]
    assert "in your answer, not inside the file" in notice
    assert "already written and right as it is" in notice
    assert "refine afterwards" not in notice


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
    agent.time_limit_s = 200
    replies = [_lookup(n) for n in range(1, 18)] + [_write(), _answer("Wrote it.")]
    chat = _stub_chat(agent, clock, replies, 10)

    result = agent.process_query(_REQUEST)

    # A file is still missing, so the stop keeps three typical steps: 200 - 30.
    assert chat.send_messages.call_count == 19
    assert "time limit" in chat.sent[17][-1]["content"]
    assert "time limit" in chat.sent[18][-1]["content"]
    assert (tmp_path / _TARGET).read_text(encoding="utf-8") == "# Changes\n"
    assert result["time_limit_reached"] is True
    assert result["max_steps_reached"] is False
    assert result["status"] == "incomplete"
    assert result["result"].startswith("Wrote it.")


def test_slow_steps_move_the_stop_earlier(agent, clock):
    """Boundary: a twentieth of the limit can't hold two slow model calls."""
    agent.time_limit_s = 200
    replies = [_lookup(n) for n in range(1, 9)] + [_answer("Done.")]
    chat = _stub_chat(agent, clock, replies, 20)

    result = agent.process_query("Is the loader in src/ right?")

    # 20s steps: stop once 200 - 40 = 160s are used, after the eighth step.
    assert chat.send_messages.call_count == 9
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
    chat = _stub_chat(agent, clock, [_lookup(1), _answer("Stopped early.")], 80)

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


def _stub_timed_chat(agent, clock, replies, ticks, stats=None):
    """Model call *i* takes ``ticks[i]`` seconds; the last tick repeats."""
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.sent = []
    chat.kwargs = []
    chat.sent_at = []

    def _send(*_, **kwargs):
        chat.sent.append([dict(m) for m in kwargs.get("messages") or []])
        chat.kwargs.append(kwargs)
        chat.sent_at.append(clock.now - 1000.0)
        clock.now += ticks[min(len(chat.sent) - 1, len(ticks) - 1)]
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        return SimpleNamespace(text=queue.pop(0), stats=dict(stats or _STATS))

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return chat


def test_one_slow_step_does_not_pull_the_stop_forward(agent, clock):
    """A 150s step among 13s ones used to keep 300s back and stop with minutes left."""
    agent.time_limit_s = 600
    replies = [_lookup(n) for n in range(1, 60)] + [_answer("Done.")]
    chat = _stub_timed_chat(agent, clock, replies, [150, 13])

    result = agent.process_query("Is the loader in src/ right?")

    # Stop once 2 x 13s (or 5% of 600s, 30s) is all that is left.
    closing = next(
        i
        for i, sent in enumerate(chat.sent)
        if "time limit" in str(sent[-1].get("content"))
    )
    assert result["time_limit_reached"] is True
    assert 560 <= chat.sent_at[closing] < 600
    assert closing > 30


def _closing_index(chat) -> int:
    return next(
        i
        for i, sent in enumerate(chat.sent)
        if "time limit" in str(sent[-1].get("content"))
    )


def test_a_long_second_step_does_not_stop_the_turn_at_half_its_limit(agent, clock):
    """Two steps in, one long step was half the "typical" step: the turn
    stopped with over 40% of its limit unspent."""
    agent.time_limit_s = 2100
    replies = [_lookup(n) for n in range(1, 40)]
    chat = _stub_timed_chat(agent, clock, replies, [400, 721, 60])

    result = agent.process_query("Is the loader in src/ right?")

    assert result["time_limit_reached"] is True
    assert chat.sent_at[_closing_index(chat)] >= 2100 * 0.9


def test_slow_steps_never_keep_back_more_than_a_quarter_of_the_limit(agent, clock):
    agent.time_limit_s = 1000
    replies = [_lookup(n) for n in range(1, 10)]
    chat = _stub_timed_chat(agent, clock, replies, [200])

    agent.process_query("Is the loader in src/ right?")

    # Two 200s steps would keep 400s back; the ceiling keeps 250s.
    assert chat.sent_at[_closing_index(chat)] == 800


def test_the_stop_never_comes_before_the_write_now_notice(agent, clock):
    agent.time_limit_s = 1000
    chat = _stub_timed_chat(agent, clock, [_lookup(n) for n in range(1, 10)], [330])

    agent.process_query("Is the loader in src/ right?")

    closing = _closing_index(chat)
    assert chat.sent_at[closing] >= 1000 * agent_module._TIME_NOTICE_AT
    assert 1 - agent_module._TIME_RESERVE_MAX >= agent_module._TIME_NOTICE_AT


def test_a_long_step_across_the_notice_threshold_still_warns(agent, clock):
    """The stop check used to run first, so a step from 50% to 95% skipped the notice."""
    agent.time_limit_s = 100
    replies = [_lookup(1), _lookup(2), _lookup(3), _answer("Done.")]
    chat = _stub_timed_chat(agent, clock, replies, [10, 10, 75])

    result = agent.process_query("Is the loader in src/ right?")

    assert result["time_limit_reached"] is True
    # The step from 20s to 95s crossed both lines; the closing request is warned.
    closing = chat.sent[3]
    assert _notices_in(closing)
    assert "time limit" in closing[-1]["content"]


def _write_markup(path: str) -> str:
    return (
        "<tool_call>\n<function=write_file>\n"
        f"<parameter=file_path>\n{path}\n</parameter>\n"
        "<parameter=content>\n# Verdict\n</parameter>\n</function>\n</tool_call>"
    )


def _native_writes(*paths: str) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"w{i}",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"file_path": path, "content": "# Verdict\n"}
                        ),
                    },
                }
                for i, path in enumerate(paths)
            ],
            "finish_reason": "tool_calls",
        }
    )


def test_the_closing_steps_call_may_use_the_time_kept_back_for_it(agent, clock):
    """Past the stop point every run was refused, the closing step's included."""
    agent.time_limit_s = 3420
    caps = []
    run = agent._execute_tool_timed

    def _recording(name, args):
        caps.append((agent._time_to_stop_s() > 0, agent.time_capped_run_seconds(300)))
        return run(name, args)

    agent._execute_tool_timed = _recording
    _stub_timed_chat(
        agent,
        clock,
        [
            _lookup(1),
            _lookup(2),
            _native_writes("verdict.md"),
            _answer("Wrote verdict.md."),
        ],
        [100, 3157, 3],
    )

    agent.process_query("Is the loader in src/ right?")

    # 160 s left at the closing call: past the stop point, yet half is its to use.
    assert caps == [(True, 300), (False, 0), (False, 80)]
    assert agent.time_capped_run_seconds(300) == 0, "only the closing call gets it"


@pytest.mark.parametrize("form", ["text", "native"])
def test_the_closing_step_runs_its_last_write(agent, clock, tmp_path, form):
    """The closing call used to withhold tools, so a last write never ran."""
    agent.time_limit_s = 100
    closing = (
        _write_markup("verdict.md") if form == "text" else _native_writes("verdict.md")
    )
    chat = _stub_timed_chat(
        agent, clock, [_lookup(1), closing, _answer("Wrote verdict.md.")], [80, 1]
    )

    result = agent.process_query("Is the loader in src/ right?")

    written = (tmp_path / "verdict.md").read_text(encoding="utf-8")
    assert written.startswith("# Verdict")
    assert result["time_limit_reached"] is True
    assert result["result"].startswith("Wrote verdict.md.")
    # Tools offered on the closing call, then withheld for the answer.
    assert "tool_choice" not in chat.kwargs[1]
    assert chat.kwargs[1]["tools"]
    assert chat.kwargs[2]["tools"] is None


def test_the_closing_step_runs_only_one_call(agent, clock, tmp_path):
    agent.time_limit_s = 100
    _stub_timed_chat(
        agent,
        clock,
        [_lookup(1), _native_writes("a.md", "b.md"), _answer("Wrote a.md.")],
        [80, 1],
    )

    agent.process_query("Is the loader in src/ right?")

    assert (tmp_path / "a.md").exists()
    assert not (tmp_path / "b.md").exists()


def test_the_step_cap_closing_call_still_runs_no_tool(agent, clock, tmp_path):
    """Only the time-limit stop gains the one call."""
    chat = _stub_timed_chat(
        agent,
        clock,
        [_lookup(1), _native_writes("a.md"), _answer("Out of steps.")],
        [1],
    )

    agent.process_query("Is the loader in src/ right?", max_steps=1)

    assert not (tmp_path / "a.md").exists()
    assert chat.kwargs[1]["tool_choice"] == "none"


_RATED = {
    "input_tokens": 100,
    "output_tokens": 10,
    "completion_tokens": 10,
    "tokens_per_second": 40.0,
    "time_to_first_token": 2.0,
}


def test_model_calls_are_capped_by_the_time_left(agent, clock):
    """An unbounded call ran to the 32K cap; now each one fits the time left."""
    agent.time_limit_s = 100
    chat = _stub_timed_chat(
        agent,
        clock,
        [_lookup(1), _lookup(2), _answer("Done.")],
        [10],
        stats=_RATED,
    )
    agent.process_query("Is the loader in src/ right?")

    first, second = chat.kwargs[0]["max_tokens"], chat.kwargs[1]["max_tokens"]
    # No rate measured before the first call: the usual cap.
    assert first == agent._output_token_cap()
    # 90s left, less 2s to first token, at 40 tok/s with the margin.
    assert second == int((90 - 2) * 40 * agent_module._TIME_TOKEN_MARGIN)


def test_the_closing_call_is_capped_by_half_the_time_left(agent, clock):
    """Its one tool call and the answer after it need the other half."""
    agent.time_limit_s = 200
    chat = _stub_timed_chat(
        agent, clock, [_lookup(1), _answer("Done.")], [160, 1], stats=_RATED
    )
    agent.process_query("Is the loader in src/ right?")

    assert chat.kwargs[1]["max_tokens"] == int(
        (40 / 2 - 2) * 40 * agent_module._TIME_TOKEN_MARGIN
    )


def test_the_answer_after_a_time_limit_stop_has_a_small_fixed_cap(agent, clock):
    """With minutes left the forced answer could run for as long as they
    lasted, re-typing work as prose; it ran longer than the step refused."""
    agent.time_limit_s = 30000
    chat = _stub_timed_chat(
        agent,
        clock,
        [_lookup(1), _native_writes("verdict.md"), _answer("Wrote verdict.md.")],
        [29000, 1],
        stats=_RATED,
    )

    agent.process_query("Is the loader in src/ right?")

    margin = agent_module._TIME_TOKEN_MARGIN
    assert chat.kwargs[1]["max_tokens"] == min(
        int((1000 / 2 - 2) * 40 * margin), agent._output_token_cap()
    )
    assert int((999 - 2) * 40 * margin) > agent_module._CLOSING_ANSWER_MAX_TOKENS
    assert chat.kwargs[2]["max_tokens"] == agent_module._CLOSING_ANSWER_MAX_TOKENS


def test_a_step_cap_answer_with_no_time_limit_keeps_the_usual_cap(agent, clock):
    chat = _stub_timed_chat(
        agent, clock, [_lookup(1), _answer("Out of steps.")], [1], stats=_RATED
    )

    agent.process_query("Is the loader in src/ right?", max_steps=1)

    assert chat.kwargs[1]["max_tokens"] == agent._output_token_cap()


def test_the_forced_answer_is_told_to_summarise_not_to_paste_files(agent, clock):
    agent.time_limit_s = 100
    chat = _stub_timed_chat(
        agent,
        clock,
        [_lookup(1), _native_writes("verdict.md"), _answer("Wrote verdict.md.")],
        [80, 1],
    )

    agent.process_query("Is the loader in src/ right?")

    assert len(chat.sent) == 3
    for sent in chat.sent[1:]:
        asked = sent[-1]["content"]
        assert "where the work stands and the next steps" in asked
        assert "never paste code or a file's contents" in asked


def test_the_cap_never_drops_below_the_floor(agent, clock):
    agent.time_limit_s = 30
    chat = _stub_timed_chat(
        agent, clock, [_lookup(1), _answer("Done.")], [29.9], stats=_RATED
    )
    agent.process_query("Is the loader in src/ right?")

    assert chat.kwargs[1]["max_tokens"] == agent_module._TIME_MIN_OUTPUT_TOKENS


def test_an_abandoned_turn_reports_what_it_had(agent, clock):
    """`gaia -p` builds this from another thread when a call outlives the limit."""
    agent.time_limit_s = 100
    chat = _stub_timed_chat(
        agent,
        clock,
        [_lookup_with_prose(1, "The loader skips symlinks."), _answer("Done.")],
        [10],
        stats=_RATED,
    )
    send = chat.send_messages.side_effect
    taken = []

    def _abandoned_during_second_call(*args, **kwargs):
        if chat.sent:
            taken.append(
                agent.time_limit_abandoned_result(
                    "Stopped at the 100-second time limit."
                )
            )
        return send(*args, **kwargs)

    chat.send_messages.side_effect = _abandoned_during_second_call
    agent.process_query("Is the loader in src/ right?")

    result = taken[0]

    assert result["status"] == "incomplete"
    assert result["time_limit_reached"] is True
    assert result["result"].startswith("Stopped at the 100-second time limit.")
    assert "The loader skips symlinks." in result["result"]
    assert result["output_tokens"] > 0


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


_NO_ONE = "Nobody can answer questions during this run."


def test_a_run_nobody_watches_says_so_with_the_request(agent, clock):
    """Expecting a reply, the model ended on an offer instead of deciding."""
    agent.no_one_to_ask = True
    chat = _stub_chat(agent, clock, [_answer("Fine.")], 10)

    result = agent.process_query(_REQUEST)

    first = chat.sent[0][-1]
    assert first["content"].startswith(_REQUEST)
    assert _NO_ONE in first["content"]
    assert [m["role"] for m in chat.sent[0]].count("user") == 1
    asked = [m for m in result["conversation"] if m.get("role") == "user"]
    assert asked[0]["content"] == _REQUEST, "the saved history keeps the user's words"


def test_a_chat_someone_is_in_gets_no_such_note(agent, clock):
    chat = _stub_chat(agent, clock, [_answer("Fine.")], 10)

    agent.process_query(_REQUEST)

    assert chat.sent[0][-1]["content"] == _REQUEST


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
    _time_left_s = Agent._time_left_s
    _time_to_stop_s = Agent._time_to_stop_s
    _typical_step_s = Agent._typical_step_s
    _closing_tool_running = False

    def __init__(self, limit, used, steps=()):
        self.time_limit_s = limit
        self._turn_started_at = 1000.0 - used if used is not None else None
        self._step_durations = list(steps)

    def _pending_deliverables(self):
        return []


@pytest.mark.parametrize(
    "limit, used, asked, expected",
    [
        (None, 0, 600, 600),
        (720, None, 600, 600),
        (720, 100, 600, 584),  # 620 left, less the 36 s reserve
        (720, 100, 30, 30),
        (720, 680, 600, 0),  # 40 left, 36 reserved: under 5 s
        (720, 700, 600, 0),
    ],
)
def test_a_run_is_cut_to_end_before_the_stop_point(limit, used, asked, expected):
    clock = _Clock()
    with patch.object(agent_module, "time", clock):
        assert _Timed(limit, used).time_capped_run_seconds(asked) == expected


def test_the_shell_caps_a_default_timeout_and_refuses_with_no_time_left():
    from gaia.agents.base.verification import OUT_OF_TIME
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    class _Host(ShellToolsMixin, _Timed):
        applied = None

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
            assert run(capped)["timeout"] == 584
            spent = _Host(720, 700)
            refused = run(spent)
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
    assert spent.applied is None
    assert refused == {**OUT_OF_TIME, "command": "python train.py"}
    assert refused["executed"] is False


def test_the_cap_ends_at_the_same_stop_point_the_loop_uses():
    """Slow steps move the stop earlier; the cap must follow it."""
    clock = _Clock()
    with patch.object(agent_module, "time", clock):
        # 600 s limit, typical step 60 s: the stop is at 600 - 120 = 480 s.
        assert _Timed(600, 300, steps=[60, 60]).time_capped_run_seconds(900) == 180
        assert _Timed(600, 300).time_capped_run_seconds(900) == 270


def test_a_run_the_limit_cut_short_is_not_told_to_rescope_or_ask_for_longer():
    from gaia.agents.base.verification import TIME_LIMIT_CUT_RUN
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    class _Host(ShellToolsMixin, _Timed):
        pass

    clock = _Clock()
    with patch.object(agent_module, "time", clock):
        cut = _Host(720, 684)._timeout_hint("script", ".")
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
        def _require_stop_channels(self):
            return None

        def _wait_interrupted(self):
            return False

        def full_access_active(self):
            return True

    saved = dict(_TOOL_REGISTRY)
    clock = _Clock()
    try:
        with patch.object(agent_module, "time", clock):
            spent = _Host(720, 682)
            assert spent._sleep(120, "rate limit") == OUT_OF_TIME
            spent.register_shell_tools()
            waited = _TOOL_REGISTRY["wait_for_condition"]["function"]("true")
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
    assert waited == OUT_OF_TIME
