# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool that reports failure in a JSON string failed, as far as the loop knows.

The email agent's ``{"ok": false, ...}`` envelope and the code-index tools'
``{"error": ...}`` were counted as successes: no error recovery, completion
evidence recorded a write that never happened, turn metrics logged ``ok``.
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tool_outcome import tool_result_failed
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.turn_scope import _is_tool_failure
from gaia.agents.tools._email.graph import OutlookReadBackend
from gaia.agents.tools.email_tools import EmailToolsMixin
from gaia.ui.sse_handler import tool_result_failed as ui_tool_result_failed


@pytest.mark.parametrize(
    "result",
    [
        pytest.param({"status": "error", "error": "x"}, id="dict-status-error"),
        pytest.param({"success": False}, id="dict-success-false"),
        pytest.param({"ok": False, "error": "x"}, id="dict-ok-false"),
        pytest.param({"error": "Not a directory: /x"}, id="dict-error-only"),
        pytest.param({"return_code": 2, "stderr": "boom"}, id="dict-exit-code"),
        pytest.param('{"ok": false, "error": "Message not found"}', id="json-ok"),
        pytest.param('{"status": "error", "error": "x"}', id="json-status"),
        pytest.param('  {"success": false, "message": "x"}\n', id="json-success"),
        pytest.param('{"error": "code_index SDK not initialised"}', id="json-error"),
        pytest.param("Error: File not found: notes.md", id="plain-error-colon"),
        pytest.param("Error reading PDF: file is encrypted", id="plain-error-verb"),
    ],
)
def test_failures_are_read_in_every_shape(result):
    assert tool_result_failed(result)


@pytest.mark.parametrize(
    "result",
    [
        pytest.param({"status": "success"}, id="dict-success"),
        pytest.param({"status": "success", "error": None}, id="dict-null-error"),
        pytest.param({"status": "ok", "error": "retried once"}, id="dict-ok-error"),
        pytest.param(
            {"return_code": 1, "expected_exit": "no matches"}, id="expected-exit"
        ),
        pytest.param('{"ok": true, "data": {"error_count": 3}}', id="json-ok-true"),
        pytest.param('{"status": "ok", "message": "Index cleared"}', id="json-ok"),
        pytest.param('[{"error": "a row"}]', id="json-list"),
        pytest.param('{"ok": false', id="broken-json"),
        pytest.param("No errors found in the log.", id="prose-no-errors"),
        pytest.param("The error was fixed in v2. Error: none.", id="prose-midline"),
        pytest.param("ERROR: a line quoted from a log file", id="log-excerpt"),
        pytest.param("Errors are reported to stderr.", id="prose-errors"),
        pytest.param("Error handling: see section 3", id="heading"),
        pytest.param("", id="empty"),
        pytest.param(None, id="none"),
    ],
)
def test_successes_and_prose_are_not_misread(result):
    assert not tool_result_failed(result)


def test_ui_counts_a_refusal_as_failed_and_the_loop_does_not():
    denied = '{"status": "denied", "error": "user declined"}'
    assert ui_tool_result_failed(denied)
    assert not Agent._is_error_result(denied)


def test_loop_break_names_a_json_string_refusal():
    agent = object.__new__(_DummyAgent)
    denied = json.dumps({"status": "denied", "error": "user declined the call"})
    summary = agent._build_loop_break_summary("send_email", 3, [denied])
    assert "not permitted here: user declined the call" in summary


def test_failure_streak_detector_reads_json_strings():
    assert _is_tool_failure('{"ok": false, "error": "Message not found"}')
    assert not _is_tool_failure('{"ok": true, "data": {}}')
    assert not _is_tool_failure('{"return_code": 1, "stderr": "x"}')


# ---------------------------------------------------------------------------
# Through the loop
# ---------------------------------------------------------------------------


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def clear_tool_registry():
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _run(tool_name, tool_result, answer):
    """One tool call returning *tool_result*, then *answer*; the LLM's inputs."""

    class _OneToolAgent(_DummyAgent):
        def _register_tools(self):
            @tool
            def write_file(file_path: str, content: str) -> dict:
                """Write content to a file."""
                return {"status": "success", "file_path": file_path}

            def _other(file_path: str) -> str:
                return tool_result

            _other.__name__ = tool_name
            _other.__doc__ = "Do the thing."
            tool(_other)

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _OneToolAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *_args, **_kwargs: False

    sent = []

    def _send(messages, *_, **__):
        resp = MagicMock()
        resp.stats = {}
        if not sent:
            resp.text = json.dumps(
                {
                    "thought": "working",
                    "tool": tool_name,
                    "tool_args": {"file_path": "notes/routine.md"},
                }
            )
        else:
            resp.text = json.dumps({"thought": "", "answer": answer})
        sent.append([dict(m) for m in messages])
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)
    outcome = agent.process_query("Save the routine", max_steps=10)
    return agent, sent, outcome


def _contents(batch):
    return "\n".join(str(m.get("content")) for m in batch)


def test_json_string_failure_triggers_error_recovery(clear_tool_registry):
    _, sent, _ = _run(
        "lookup_record",
        json.dumps({"ok": False, "error": "Record 42 not found"}),
        "The record does not exist.",
    )
    assert "TOOL EXECUTION FAILED" in _contents(sent[1])
    assert "Record 42 not found" in _contents(sent[1])


def test_json_string_success_does_not_trigger_error_recovery(clear_tool_registry):
    _, sent, _ = _run(
        "lookup_record",
        json.dumps({"ok": True, "data": {"error_count": 0}}),
        "Found it.",
    )
    assert "TOOL EXECUTION FAILED" not in _contents(sent[1])


def test_prose_mentioning_error_does_not_trigger_error_recovery(clear_tool_registry):
    _, sent, _ = _run(
        "lookup_record",
        "No errors found. The error log from last week is empty.",
        "All clear.",
    )
    assert "TOOL EXECUTION FAILED" not in _contents(sent[1])


CLAIM = "I have saved the routine to `notes/routine.md`."


def test_json_string_failed_write_is_not_completion_evidence(clear_tool_registry):
    """A write that reported failure in JSON wrote nothing; the claim is unbacked."""
    _, sent, outcome = _run(
        "write_python_file",
        json.dumps({"success": False, "error": "path not allowed"}),
        CLAIM,
    )
    assert outcome["status"] == "incomplete"
    assert CLAIM not in outcome["result"]
    assert any("No successful write" in _contents(batch) for batch in sent[1:])


def test_json_string_successful_write_still_backs_the_claim(clear_tool_registry):
    _, sent, outcome = _run(
        "write_python_file",
        json.dumps({"success": True, "file_path": "notes/routine.md"}),
        CLAIM,
    )
    assert not any("No successful write" in _contents(batch) for batch in sent[1:])


# ---------------------------------------------------------------------------
# The real email tool, over a mocked Microsoft Graph
# ---------------------------------------------------------------------------


def _run_email(graph_handler):
    """``list_inbox`` against *graph_handler*, then an answer; the LLM's inputs."""

    class _EmailAgent(EmailToolsMixin, _DummyAgent):
        def _register_tools(self):
            self.register_email_tools()

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _EmailAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *_args, **_kwargs: False
    agent._email_backend = OutlookReadBackend(
        lambda: "test-token",
        http_client=httpx.Client(transport=httpx.MockTransport(graph_handler)),
    )

    sent = []

    def _send(messages, *_, **__):
        resp = MagicMock()
        resp.stats = {}
        if not sent:
            resp.text = json.dumps(
                {"thought": "check mail", "tool": "list_inbox", "tool_args": {}}
            )
        else:
            resp.text = json.dumps({"thought": "", "answer": "Here is your inbox."})
        sent.append([dict(m) for m in messages])
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)
    agent.process_query("What's in my inbox?", max_steps=10)
    return sent


def test_email_tool_failure_reaches_error_recovery(clear_tool_registry):
    sent = _run_email(lambda request: httpx.Response(503, text="Service Unavailable"))
    second = _contents(sent[1])
    assert "TOOL EXECUTION FAILED" in second
    assert "Microsoft Graph request failed" in second


def test_email_tool_success_does_not_reach_error_recovery(clear_tool_registry):
    sent = _run_email(lambda request: httpx.Response(200, json={"value": []}))
    assert "TOOL EXECUTION FAILED" not in _contents(sent[1])
