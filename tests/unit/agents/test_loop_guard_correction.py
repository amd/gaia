# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The repeated-call loop guard corrects before it stops, and names the cause (#3888).

Throttled calls never ran, so they are not repeats. The first time a call hits
the repeat limit the model gets one correction instead of a terminated turn.
When the guard does stop, the summary says why: rate-limited, not permitted,
a connection failure, or a plain failure — only the connection case blames a
service.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import NOT_EXECUTED

_TOOL = "loop_guard_probe_tool"
_CORRECTION = "Do not repeat it"
_SERVICE_HINT = "check that the underlying service is running"


class _DummyAgent(Agent):
    #: Results the probe tool returns, in order; the last one repeats.
    results: list = [{"status": "success"}]

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self
        agent.calls = 0

        @tool
        def loop_guard_probe_tool(command: str) -> dict:
            """Probe tool for loop-guard tests."""
            del command
            agent.calls += 1
            index = min(agent.calls, len(agent.results)) - 1
            return agent.results[index]

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        a.max_consecutive_repeats = 3
        return a


def _stub_chat(agent_, *responses):
    """Scripted chat; records the messages sent on every call."""
    from unittest.mock import MagicMock

    queue = list(responses)
    sent: list = []
    chat = MagicMock()

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        if not queue:
            raise AssertionError("chat stub ran out of scripted responses")
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    chat.send_messages = MagicMock(side_effect=_send)
    agent_.chat = chat
    return sent


def _legacy_call() -> str:
    return json.dumps(
        {"thought": "try", "tool": _TOOL, "tool_args": {"command": "same"}}
    )


def _native_call(call_id: str) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": _TOOL,
                        "arguments": json.dumps({"command": "same"}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _answer(text: str) -> str:
    return json.dumps({"thought": "done", "answer": text})


def _corrections(sent: list) -> list:
    return [
        m
        for m in sent[-1]
        if m.get("role") == "tool" and _CORRECTION in str(m.get("content"))
    ]


# ---------------------------------------------------------------------------
# Correction before termination
# ---------------------------------------------------------------------------


def test_first_threshold_hit_injects_a_correction_and_lets_the_model_answer(agent):
    agent.results = [{"status": "error", "error": "[Errno 21] Is a directory"}]
    sent = _stub_chat(agent, *[_legacy_call()] * 3, _answer("done differently"))

    result = agent.process_query("fix it", max_steps=10)

    assert result["result"].startswith("done differently")
    assert agent.calls == 2  # the third identical call was replaced, not run
    corrections = _corrections(sent)
    assert len(corrections) == 1
    content = str(corrections[0]["content"])
    # Two calls actually ran; the third was replaced by the correction.
    assert f"called {_TOOL} 2 times with the same arguments" in content
    assert "Is a directory" in content


def test_repeat_after_the_correction_ends_the_turn(agent):
    agent.results = [{"status": "error", "error": "[Errno 21] Is a directory"}]
    _stub_chat(agent, *[_legacy_call()] * 4)

    result = agent.process_query("fix it", max_steps=10)

    assert agent.calls == 2
    assert "kept failing: [Errno 21] Is a directory" in result["result"]
    assert _SERVICE_HINT not in result["result"]


def test_native_path_correction_answers_the_tool_call_id(agent):
    agent.results = [{"status": "error", "error": "404 Not Found"}]
    sent = _stub_chat(
        agent,
        _native_call("c1"),
        _native_call("c2"),
        _native_call("c3"),
        _native_call("c4"),
    )

    result = agent.process_query("fetch it", max_steps=10)

    assert agent.calls == 2
    corrections = _corrections(sent)
    assert [m.get("tool_call_id") for m in corrections] == ["c3"]
    assert "kept failing: 404 Not Found" in result["result"]


def test_no_progress_correction_when_the_call_succeeds(agent):
    agent.results = [{"status": "success", "stdout": "same listing"}]
    sent = _stub_chat(agent, *[_legacy_call()] * 3, _answer("listed"))

    agent.process_query("list", max_steps=10)

    assert "returned no new progress" in str(_corrections(sent)[0]["content"])


# ---------------------------------------------------------------------------
# Throttled calls are not repeats
# ---------------------------------------------------------------------------


def _throttled(wait: float) -> dict:
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error": f"Rate limit: max 3 commands per 10 seconds. Wait {wait:.1f}s",
        "has_errors": True,
        "rate_limited": True,
        "wait_time_seconds": wait,
    }


def test_rate_limited_calls_do_not_count_toward_the_limit(agent):
    agent.results = [_throttled(2.0)] * 4 + [{"status": "success", "stdout": "ok"}]
    sent = _stub_chat(agent, *[_legacy_call()] * 5, _answer("listed"))

    with patch.object(agent, "_wait_out_rate_limit") as wait:
        result = agent.process_query("list", max_steps=10)

    assert result["result"].startswith("listed")
    assert agent.calls == 5
    assert not _corrections(sent)
    assert wait.call_count == 4
    wait.assert_called_with(_throttled(2.0))


def test_rate_limit_wait_is_capped(agent):
    with patch("gaia.agents.base.agent.time.sleep") as sleep:
        agent._wait_out_rate_limit(_throttled(60.0))
    sleep.assert_called_once_with(Agent._RATE_LIMIT_WAIT_CAP_S)


def test_rate_limit_wait_honours_the_cancel_event(agent):
    import threading

    agent._cancel_event = threading.Event()
    with (
        patch("gaia.agents.base.agent.time.sleep") as sleep,
        patch.object(agent._cancel_event, "wait") as wait,
    ):
        agent._wait_out_rate_limit(_throttled(3.0))
    wait.assert_called_once_with(3.0)
    sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Terminal summary names the cause
# ---------------------------------------------------------------------------


def _summary(agent_, result: dict, tool_name: str = "run_shell_command") -> str:
    return agent_._build_loop_break_summary(tool_name, 4, [result])


def test_summary_for_a_throttled_call_says_rate_limited(agent):
    summary = _summary(agent, _throttled(4.0))
    assert "rate-limited and did not run" in summary
    assert _SERVICE_HINT not in summary


@pytest.mark.parametrize(
    "result",
    [
        {
            **NOT_EXECUTED,
            "status": "error",
            "error": "Shell operators (&, >, >>, <, &&, ||, ;, `, $()) are not "
            "allowed for security reasons.",
            "has_errors": True,
        },
        {"status": "denied", "error": "The user declined this tool call."},
    ],
)
def test_summary_for_a_refusal_says_not_permitted(agent, result):
    summary = _summary(agent, result)
    assert "not permitted here" in summary
    assert "different approach" in summary
    assert _SERVICE_HINT not in summary


@pytest.mark.parametrize(
    "error",
    [
        "Connection refused (localhost:8000)",
        "[WinError 10061] No connection could be made because the target "
        "machine actively refused it",
    ],
)
def test_summary_for_a_connection_error_keeps_the_service_hint(agent, error):
    summary = _summary(agent, {"status": "error", "error": error})
    assert f"kept failing: {error}" in summary
    assert _SERVICE_HINT in summary


@pytest.mark.parametrize(
    "error",
    [
        "[WinError 10061] No connection could be made because the target "
        "machine actively refused it",
        "[WinError 10060] A connection attempt failed because the connected "
        "party did not properly respond after a period of time",
        "[WinError 10061]",
    ],
)
def test_summary_reads_a_windows_socket_error_as_a_connection_failure(agent, error):
    """Windows says "actively refused" for a dead service, not a policy refusal.

    That wording matches none of the connection patterns but does match
    ``refus`` in the not-permitted set, so it used to tell the user to split
    the task into steps when the fix was to start the service.
    """
    summary = _summary(agent, {"status": "error", "error": error})
    assert _SERVICE_HINT in summary
    assert "not permitted here" not in summary


def test_summary_for_prose_about_refusing_is_a_plain_failure(agent):
    """A tool declining to clobber a file is a plain failure, not a policy refusal."""
    summary = _summary(
        agent, {"status": "error", "error": "refusing to overwrite existing out.txt"}
    )
    assert "not permitted here" not in summary
    assert "kept failing: refusing to overwrite" in summary


def test_summary_for_a_plain_failure_does_not_blame_a_service(agent):
    summary = _summary(agent, {"status": "error", "error": "404 Not Found"})
    assert "kept failing: 404 Not Found" in summary
    assert _SERVICE_HINT not in summary


def test_summary_uses_the_last_stderr_line_when_there_is_no_error_key(agent):
    result = {
        "status": "success",
        "return_code": 1,
        "stdout": "",
        "stderr": 'Traceback (most recent call last):\n  File "t.py", line 1\n'
        "ModuleNotFoundError: No module named 'toybox'\n",
    }
    summary = _summary(agent, result, tool_name="execute_python_file")
    assert "kept failing: ModuleNotFoundError: No module named 'toybox'" in summary
    assert "the tool returned an error" not in summary
    assert _SERVICE_HINT not in summary


def test_summary_falls_back_to_the_return_code(agent):
    summary = _summary(agent, {"return_code": 2, "stderr": "  \n"})
    assert "exited with return code 2" in summary
