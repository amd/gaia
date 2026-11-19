# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool can take a large result's handle as an argument (#1973).

A result over the context budget is archived and the model sees a preview and
an ``output_…`` handle. To hand that result to another tool, the model used to
page it back with ``read_tool_output`` and copy it into the next call, which
put the whole payload back in the prompt.
"""

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base import artifacts
from gaia.agents.base.agent import Agent
from gaia.agents.base.artifacts import ArtifactStore, store_for

MARKER = "MIDDLE-7f3a"
TRANSCRIPT = "line " * 20000 + MARKER + " tail" * 20000


class _PipeAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass


class _ArgsCheckingAgent(_PipeAgent):
    """Checks arguments before the base class runs, as the governance mixin does."""

    def _execute_tool(self, tool_name, tool_args):
        self.checked = tool_args
        return super()._execute_tool(tool_name, tool_args)


def _make(cls=_PipeAgent):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = cls(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    return agent


@pytest.fixture
def agent():
    return _make()


def _register(agent, name, function):
    agent._tools_registry[name] = {
        "name": name,
        "function": function,
        "description": name,
        "parameters": {},
    }


def _consumer(agent):
    received = []

    def count_words(text: str, extra: dict = None):
        received.append({"text": text, "extra": extra})
        return {"status": "success", "words": len(text.split())}

    _register(agent, "count_words", count_words)
    return received


def test_a_handle_argument_becomes_the_archived_text(agent):
    received = _consumer(agent)
    handle = store_for(agent).put(TRANSCRIPT)

    result = agent._execute_tool_timed("count_words", {"text": handle})

    assert result["status"] == "success"
    assert received[0]["text"] == TRANSCRIPT


def test_nested_handles_resolve_but_a_handle_inside_text_does_not(agent):
    received = _consumer(agent)
    handle = store_for(agent).put(TRANSCRIPT)
    prose = f"see {handle} for details"

    agent._execute_tool_timed(
        "count_words", {"text": prose, "extra": {"items": [handle, "plain"]}}
    )

    assert received[0]["text"] == prose
    assert received[0]["extra"] == {"items": [TRANSCRIPT, "plain"]}


def test_arguments_without_a_handle_are_passed_through_as_is(agent):
    received = _consumer(agent)
    extra = {"items": ["output_short", "output_" + "g" * 32, " output_" + "a" * 32]}

    agent._execute_tool_timed("count_words", {"text": "hello", "extra": extra})

    assert received[0] == {"text": "hello", "extra": extra}
    assert received[0]["extra"] is extra


def test_an_unknown_handle_stops_the_call(agent):
    received = _consumer(agent)
    handle = "output_" + "0" * 32

    result = agent._execute_tool_timed("count_words", {"text": handle})

    assert received == []
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_arguments"
    assert result["executed"] is False
    assert f"Unknown output handle {handle}" in result["error"]


def test_an_expired_handle_says_expired(agent, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(artifacts.time, "monotonic", lambda: clock[0])
    received = _consumer(agent)
    store = store_for(agent)
    handle = store.put(TRANSCRIPT)
    clock[0] = store.ttl + 1
    store.put("a later write clears the old entry")

    result = agent._execute_tool_timed("count_words", {"text": handle})

    assert received == []
    assert f"Expired output handle {handle}" in result["error"]


def test_another_agents_handle_is_unknown(agent):
    received = _consumer(agent)
    handle = store_for(_make()).put(TRANSCRIPT)

    result = agent._execute_tool_timed("count_words", {"text": handle})

    assert received == []
    assert "Unknown output handle" in result["error"]


@pytest.mark.parametrize(
    "name",
    [
        "read_tool_output",
        "read-tool-output",
        " read_tool_output() ",
        "Read_Tool_Output",
    ],
)
def test_read_tool_output_still_takes_the_handle_itself(agent, name):
    agent._register_output_reader()
    handle = store_for(agent).put(TRANSCRIPT)

    page = agent._execute_tool_timed(name, {"artifact": handle})

    assert page["artifact"] == handle
    assert page["content"] == TRANSCRIPT[:2000]


def test_checks_before_the_base_class_see_the_archived_text():
    agent = _make(_ArgsCheckingAgent)
    _consumer(agent)
    handle = store_for(agent).put(TRANSCRIPT)

    agent._execute_tool_timed("count_words", {"text": handle})

    assert agent.checked == {"text": TRANSCRIPT}


def test_the_base_guardrails_see_the_archived_text(agent):
    written = []
    _register(
        agent,
        "write_file",
        lambda file_path, content: written.append(content) or {"status": "success"},
    )
    handle = store_for(agent).put("   ")

    result = agent._execute_tool_timed(
        "write_file", {"file_path": "notes.md", "content": handle}
    )

    assert written == []
    assert result["status"] == "error"
    assert "is empty" in result["error"]


def test_resolve_tells_unknown_from_expired(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(artifacts.time, "monotonic", lambda: clock[0])
    store = ArtifactStore(ttl=10)
    swept, paged = store.put("swept"), store.put("paged")
    assert store.resolve(swept) == "swept"

    clock[0] = 11
    with pytest.raises(ValueError, match="Expired"):
        store.read(paged)
    store.put("a later write clears the old entry")
    for handle in (swept, paged):
        with pytest.raises(ValueError, match="Expired"):
            store.resolve(handle)
    with pytest.raises(ValueError, match="Unknown"):
        store.resolve("output_" + "1" * 32)


def test_the_expired_list_keeps_only_the_newest(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(artifacts.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(artifacts, "EXPIRED_HANDLES_KEPT", 2)
    store = ArtifactStore(ttl=10)
    oldest, middle, newest = (store.put(t) for t in ("a", "b", "c"))

    clock[0] = 11
    store.put("a later write clears all three")

    with pytest.raises(ValueError, match="Unknown"):
        store.resolve(oldest)
    for handle in (middle, newest):
        with pytest.raises(ValueError, match="Expired"):
            store.resolve(handle)


def test_condensed_results_say_the_handle_can_be_passed_on(agent):
    condensed = agent._handle_large_tool_result("fetch_transcript", TRANSCRIPT, [])

    assert "another tool's argument" in condensed["fetch"]


def test_a_large_result_piped_by_handle_never_enters_the_prompt(agent):
    _register(agent, "fetch_transcript", lambda: TRANSCRIPT)
    received = _consumer(agent)
    sent = []

    def _send(messages, *_, **__):
        sent.append(json.dumps(messages, ensure_ascii=False, default=str))
        if len(sent) == 1:
            reply = {"thought": "", "tool": "fetch_transcript", "tool_args": {}}
        elif len(sent) == 2:
            handle = re.search(r"output_[0-9a-f]{32}", sent[-1]).group(0)
            reply = {
                "thought": "",
                "tool": "count_words",
                "tool_args": {"text": handle},
            }
        else:
            reply = {"thought": "", "answer": "The transcript has 40001 words."}
        response = MagicMock()
        response.text = json.dumps(reply)
        response.stats = {}
        return response

    agent.chat = MagicMock(send_messages=MagicMock(side_effect=_send))

    agent.process_query("How many words are in the transcript?", max_steps=6)

    assert received and received[0]["text"] == TRANSCRIPT
    assert len(sent) == 3
    for prompt in sent:
        assert MARKER not in prompt
        assert len(prompt) < len(TRANSCRIPT) // 2
