# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A forced stop must not lose the file the request asked for, or lie about it.

When the loop guard ends a turn and the requested output file was never
written, the model gets one bounded step with only the file-writing tools to
write it. Its closing answer then passes the same completion gate as any other
early exit, so "OUTPUT.md is written" with no write behind it is reported as
unfinished instead of sealed as done.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_LOOKUP = "lookup_for_forced_stop_test"
_REPEATS = 3
_CALLS = _REPEATS + 1  # the guard corrects once, then stops on the next repeat
_REQUEST = "Audit the loader and write `out/OUTPUT.md` with one section per defect."
_TARGET = "out/OUTPUT.md"
_UNWRITTEN = f"No successful write to `{_TARGET}`"
_STATS = {"input_tokens": 100, "output_tokens": 10}


class _Agent(Agent):
    """One read-only lookup and a real ``write_file``."""

    lookups = 0

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def lookup_for_forced_stop_test(path: str) -> dict:
            """Look something up."""
            agent.lookups += 1
            return {"status": "success", "path": path, "found": "same as before"}

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            target = Path(file_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {
                "status": "success",
                "file_path": str(target),
                "bytes_written": len(content.encode("utf-8")),
            }

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        made = _Agent(
            silent_mode=True,
            skip_lemonade=True,
            model_id=DEFAULT_MODEL_NAME,
            max_consecutive_repeats=_REPEATS,
        )
    made.streaming = False
    # Headless and pre-approved, as `gaia -p --dangerously-skip-permissions`.
    made.console.auto_approve_gated_tools = True
    yield made
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _stub_chat(agent, *replies):
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    # The loop keeps appending to the list it sent, so record what each call saw.
    chat.sent = []

    def _send(*_, **kwargs):
        chat.sent.append([dict(m) for m in kwargs.get("messages") or []])
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(text=item, stats=dict(_STATS))

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return chat


def _native(*calls) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for call_id, name, args in calls
            ],
            "finish_reason": "tool_calls",
        }
    )


def _repeats():
    return [_native((f"call_{n}", _LOOKUP, {"path": "src/"})) for n in range(_CALLS)]


def _write(path=_TARGET, call_id="w1", content="# Findings\n\nOne defect.\n"):
    return (call_id, "write_file", {"file_path": path, "content": content})


def _answer(text: str) -> str:
    return json.dumps({"thought": "", "answer": text})


def _tool_names(call) -> list:
    return [t["function"]["name"] for t in call.kwargs.get("tools") or []]


def test_a_loop_break_gets_one_write_only_step_for_the_missing_file(agent, tmp_path):
    chat = _stub_chat(
        agent,
        *_repeats(),
        _native(_write()),
        _answer(f"`{_TARGET}` is written with one finding."),
    )

    result = agent.process_query(_REQUEST)

    assert (tmp_path / _TARGET).read_text(encoding="utf-8").startswith("# Findings")
    write_step = chat.send_messages.call_args_list[_CALLS]
    assert _tool_names(write_step) == ["write_file"]
    assert _TARGET in chat.sent[_CALLS][-1]["content"]
    assert _UNWRITTEN not in result["result"]
    assert result["completion_gaps"] == []


def test_a_closing_claim_with_no_write_is_reported_unfinished(agent, tmp_path):
    """The write step is declined, and the answer still says the file exists."""
    _stub_chat(
        agent,
        *_repeats(),
        _answer("I have what I need."),
        _answer(f"`{_TARGET}` is written. Summary of what I found follows."),
    )

    result = agent.process_query(_REQUEST)

    assert not (tmp_path / _TARGET).exists()
    assert result["status"] == "incomplete"
    assert any(_TARGET in gap for gap in result["completion_gaps"])
    # The model's text survives; the gate's report follows it.
    assert result["result"].startswith(f"`{_TARGET}` is written.")
    assert "can't confirm this is done" in result["result"]


def test_an_admitted_skip_is_still_incomplete(agent):
    """Saying "I am not writing it" is honest, but the turn did not deliver."""
    _stub_chat(
        agent,
        *_repeats(),
        _answer("Not writing it."),
        _answer("I could not confirm the finding, so I am not writing the file."),
    )

    result = agent.process_query(_REQUEST)

    assert result["status"] == "incomplete"
    assert _UNWRITTEN in result["result"]


def test_the_closing_prompt_names_the_still_missing_file(agent):
    chat = _stub_chat(agent, *_repeats(), _answer("no"), _answer("Stopped."))

    agent.process_query(_REQUEST)

    closing = chat.sent[-1][-1]["content"]
    assert f"`{_TARGET}`" in closing
    assert "do not say they are written" in closing


def test_the_write_step_cannot_run_any_other_tool(agent):
    """Bypass attempt: the model calls the looping tool again in the write step."""
    _stub_chat(
        agent,
        *_repeats(),
        _native(("x1", _LOOKUP, {"path": "elsewhere/"})),
        _answer("Stopped."),
    )

    result = agent.process_query(_REQUEST)

    # Only the calls before the guard ran; the write step's lookup did not.
    assert agent.lookups == _REPEATS - 1
    tool_results = [m for m in result["conversation"] if m.get("role") == "tool"]
    assert "only the requested files" in json.dumps(tool_results[-1])


def test_the_write_step_is_bounded_to_one_write_per_missing_file(agent, tmp_path):
    """Bypass attempt: one reply fans out many writes."""
    _stub_chat(
        agent,
        *_repeats(),
        _native(_write(), _write("out/extra.md", "w2"), _write("out/more.md", "w3")),
        _answer("Done."),
    )

    agent.process_query(_REQUEST)

    assert (tmp_path / _TARGET).exists()
    assert not (tmp_path / "out/extra.md").exists()
    assert not (tmp_path / "out/more.md").exists()


def test_writing_some_other_file_does_not_satisfy_the_request(agent, tmp_path):
    _stub_chat(
        agent,
        *_repeats(),
        _native(_write("out/notes.md")),
        _answer(f"`{_TARGET}` is written."),
    )

    result = agent.process_query(_REQUEST)

    assert (tmp_path / "out/notes.md").exists()
    assert result["status"] == "incomplete"
    assert any(_TARGET in gap for gap in result["completion_gaps"])


def test_no_write_step_when_nothing_was_asked_for(agent):
    """False positive guard: an ordinary loop break costs no extra call."""
    chat = _stub_chat(agent, *_repeats(), _answer("The loader looks fine."))

    result = agent.process_query("Is the loader in src/ correct?")

    assert chat.send_messages.call_count == _CALLS + 1
    assert result["result"].startswith("The loader looks fine.")
    assert result["status"] == "success"


def test_no_write_step_when_the_file_is_already_written(agent, tmp_path):
    chat = _stub_chat(
        agent,
        _native(_write(call_id="early")),
        *_repeats(),
        _answer(f"`{_TARGET}` is written."),
    )

    result = agent.process_query(_REQUEST)

    assert chat.send_messages.call_count == 1 + _CALLS + 1
    assert (tmp_path / _TARGET).exists()
    assert _UNWRITTEN not in result["result"]


def test_a_failed_write_step_still_ends_with_the_gap_named(agent):
    _stub_chat(
        agent,
        *_repeats(),
        RuntimeError("gateway returned HTTP 500"),
        _answer("Here is what I found."),
    )

    result = agent.process_query(_REQUEST)

    assert result["result"].startswith("Here is what I found.")
    assert result["status"] == "incomplete"
    assert _UNWRITTEN in result["result"]


def test_a_claimed_write_with_nothing_requested_is_caught_on_a_loop_break(agent):
    """The gate used to skip the loop-break answer entirely."""
    _stub_chat(
        agent,
        *_repeats(),
        _answer("I saved the findings to notes/findings.md."),
    )

    result = agent.process_query("Look into the loader in src/.")

    assert result["status"] == "incomplete"
    assert result["completion_gaps"]


def test_a_json_protocol_model_gets_the_write_step_too(agent, tmp_path):
    """No native tools: the step sends none and reads the embedded-JSON call."""
    agent._uses_native_tool_calls = lambda: False  # pylint: disable=protected-access
    call = {"thought": "", "tool": _LOOKUP, "tool_args": {"path": "src/"}}
    write = {
        "thought": "",
        "tool": "write_file",
        "tool_args": {"file_path": _TARGET, "content": "# Findings\n"},
    }
    chat = _stub_chat(
        agent,
        *([json.dumps(call)] * _CALLS),
        json.dumps(write),
        _answer(f"`{_TARGET}` is written."),
    )

    result = agent.process_query(_REQUEST)

    assert chat.send_messages.call_args_list[_CALLS].kwargs["tools"] is None
    assert (tmp_path / _TARGET).read_text(encoding="utf-8") == "# Findings\n"
    assert result["completion_gaps"] == []
