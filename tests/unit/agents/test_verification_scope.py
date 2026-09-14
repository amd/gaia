# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the verification-scope statement on emitted answers (#3376).

The loop used to say "done" identically whether it ran the test suite or ran
nothing at all. Every emitted answer now carries one bounded line naming which
of three states applies — verified / partially verified / unverified — derived
from the turn's own tool-execution log.

Two layers of coverage:

* the pure classifier/builder, exercised directly;
* every exit path in ``_process_query_impl`` that produces a final answer,
  driven through the real loop with a stubbed chat client. The direct-set
  paths (LLM error, context overflow, cancel-event timeout, parse give-up,
  loop break, max steps) are the point of the issue — they bypass
  ``finalize_answer``, and they are disproportionately the runs that went
  wrong.

The one deliberate exclusion is the console-cancellation path, which returns
an empty result on purpose (#3386).
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import (
    NOT_EXECUTED,
    VERIFICATION_SCOPE_MAX_CHARS,
    VERIFICATION_SCOPE_PREFIX,
    build_verification_scope,
    check_was_executed,
    strip_verification_scope,
    verification_check_label,
)

_SANDBOX_SHELL = "sandbox_shell_for_verification_scope_test"


class _DummyAgent(Agent):
    """Minimal concrete Agent — same pattern as test_loop_break_truthful."""

    #: Set per-test; the stub tool returns it verbatim.
    shell_result = {"status": "success", "return_code": 0, "stdout": "ok"}

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        # Deliberately NOT named ``run_shell_command``: that name is
        # confirmation-gated, and the classifier keys on the ``command``
        # argument, not the tool name. The real name is covered by the
        # classifier tests below.
        @tool
        def sandbox_shell_for_verification_scope_test(command: str) -> dict:
            """Run a command in a sandbox."""
            del command
            return agent.shell_result

        @tool
        def sandbox_check_with_a_timeout(command: str, timeout: int = 60) -> dict:
            """Run a command in a sandbox with a timeout."""
            del command, timeout
            return agent.shell_result

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        return a


def _stub_chat(agent_, *responses):
    """Replace ``agent.chat`` with a stub yielding *responses* in order."""
    queue = list(responses)
    chat = MagicMock()

    def _send(*_, **__):
        payload = queue.pop(0) if queue else queue_exhausted()
        if isinstance(payload, Exception):
            raise payload
        resp = MagicMock()
        resp.text = payload
        resp.stats = {}
        return resp

    def queue_exhausted():
        raise AssertionError("chat stub ran out of scripted responses")

    chat.send_messages = MagicMock(side_effect=_send)
    agent_.chat = chat
    return chat


def _answer(text: str) -> str:
    return json.dumps({"thought": "done", "answer": text})


def _tool_call(command: str) -> str:
    return json.dumps(
        {
            "thought": "checking",
            "tool": _SANDBOX_SHELL,
            "tool_args": {"command": command},
        }
    )


def _scope_line(text: str) -> str:
    """The verification line from an emitted answer (asserts there is one)."""
    lines = [
        line for line in text.splitlines() if line.startswith(VERIFICATION_SCOPE_PREFIX)
    ]
    assert len(lines) == 1, f"expected exactly one scope line in:\n{text}"
    return lines[0]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        ("pytest tests/unit -q", "pytest"),
        ("python -m pytest tests/", "python -m pytest"),
        ("python util/lint.py --all", "util/lint.py"),
        ("npm run test", "npm run test"),
        ("cargo clippy -- -D warnings", "cargo clippy"),
        ("go test ./...", "go test"),
        ("ruff check src/", "ruff"),
        ("tsc --noEmit", "tsc"),
        ("git commit -m 'wip'", None),
        ("ls -la", None),
        ("", None),
    ],
)
def test_command_classification(command, expected):
    assert verification_check_label("run_shell_command", {"command": command}) == (
        expected
    )


def test_tool_name_alone_can_be_a_check():
    assert verification_check_label("run_tests", {}) == "run_tests"


def test_non_check_tool_is_not_a_check():
    assert verification_check_label("read_file", {"file_path": "pytest.ini"}) is None


def test_non_dict_args_do_not_raise():
    assert verification_check_label("read_file", "pytest") is None


# ---------------------------------------------------------------------------
# Three states
# ---------------------------------------------------------------------------


def _execution(label=None, failed=False, name="some_tool"):
    return {"tool": name, "check_label": label, "failed": failed}


def test_no_tools_at_all_is_unverified():
    statement = build_verification_scope([])
    assert statement.startswith(VERIFICATION_SCOPE_PREFIX)
    assert "unverified" in statement
    assert "no tools ran" in statement


def test_tools_but_no_checks_is_unverified_and_says_how_many():
    statement = build_verification_scope([_execution(), _execution()])
    assert "unverified" in statement
    assert "2 tool calls ran" in statement


def test_singular_tool_call_wording():
    assert "1 tool call ran" in build_verification_scope([_execution()])


def test_all_checks_passed_is_verified():
    statement = build_verification_scope(
        [_execution("pytest"), _execution("ruff"), _execution()]
    )
    assert "verified —" in statement
    assert "partially" not in statement
    assert "pytest, ruff" in statement


def test_mixed_check_outcomes_is_partially_verified():
    statement = build_verification_scope(
        [_execution("ruff"), _execution("pytest", failed=True)]
    )
    assert "partially verified" in statement
    assert "ruff passed" in statement
    assert "pytest did not" in statement


def test_all_checks_failed_is_partially_verified_not_verified():
    statement = build_verification_scope([_execution("pytest", failed=True)])
    assert "partially verified" in statement
    assert "did not pass" in statement


def test_the_three_states_are_distinguishable():
    unverified = build_verification_scope([])
    verified = build_verification_scope([_execution("pytest")])
    partial = build_verification_scope(
        [_execution("pytest"), _execution("ruff", failed=True)]
    )
    assert len({unverified, verified, partial}) == 3
    # "verified" is a substring of "unverified", so the states must be told
    # apart on their own terms, not by substring containment.
    assert unverified.startswith(f"{VERIFICATION_SCOPE_PREFIX}unverified")
    assert verified.startswith(f"{VERIFICATION_SCOPE_PREFIX}verified")
    assert partial.startswith(f"{VERIFICATION_SCOPE_PREFIX}partially verified")


# ---------------------------------------------------------------------------
# Bounded: the statement rides in conversation history
# ---------------------------------------------------------------------------


def test_statement_is_bounded_even_with_many_distinct_checks():
    executions = [_execution(f"checker-{i:03d}-with-a-long-name") for i in range(200)]
    assert len(build_verification_scope(executions)) <= VERIFICATION_SCOPE_MAX_CHARS


def test_label_list_is_capped_with_a_count():
    statement = build_verification_scope(
        [
            _execution("pytest"),
            _execution("ruff"),
            _execution("mypy"),
            _execution("tsc"),
        ]
    )
    assert "+1 more" in statement


def test_duplicate_labels_collapse():
    statement = build_verification_scope([_execution("pytest")] * 5)
    assert statement.count("pytest") == 1


def test_every_state_fits_the_bound():
    for executions in (
        [],
        [_execution()],
        [_execution("pytest")],
        [_execution("pytest", failed=True)],
        [_execution("pytest"), _execution("ruff", failed=True)],
    ):
        assert len(build_verification_scope(executions)) <= VERIFICATION_SCOPE_MAX_CHARS


def test_strip_removes_the_statement_and_leaves_the_answer():
    answer = "All set."
    emitted = f"{answer}\n\n{build_verification_scope([])}"
    assert strip_verification_scope(emitted) == answer


def test_strip_is_a_no_op_without_a_statement():
    assert strip_verification_scope("plain answer") == "plain answer"


# ---------------------------------------------------------------------------
# Exit path: parsed answer (the finalize_answer seam)
# ---------------------------------------------------------------------------


def test_parsed_answer_with_no_tools_is_unverified(agent):
    _stub_chat(agent, _answer("Paris is the capital of France."))
    result = agent.process_query("capital of france?", max_steps=3)
    assert "Paris" in result["result"]
    assert "unverified" in _scope_line(result["result"])


def test_parsed_answer_after_a_passing_check_is_verified(agent):
    agent.shell_result = {"status": "success", "return_code": 0, "stdout": "3 passed"}
    _stub_chat(agent, _tool_call("pytest tests/unit -q"), _answer("Tests pass."))
    result = agent.process_query("run the tests", max_steps=5)
    line = _scope_line(result["result"])
    assert line.startswith(f"{VERIFICATION_SCOPE_PREFIX}verified")
    assert "pytest" in line


def test_parsed_answer_after_a_failing_check_is_partially_verified(agent):
    agent.shell_result = {"status": "error", "error": "1 failed", "return_code": 1}
    _stub_chat(agent, _tool_call("pytest tests/unit -q"), _answer("One test failed."))
    result = agent.process_query("run the tests", max_steps=5)
    assert "partially verified" in _scope_line(result["result"])


def test_non_check_tool_still_reads_unverified(agent):
    _stub_chat(agent, _tool_call("ls -la"), _answer("Here are the files."))
    result = agent.process_query("list files", max_steps=5)
    line = _scope_line(result["result"])
    assert "unverified" in line
    assert "1 tool call ran" in line


def test_statement_survives_a_subclass_rewriting_the_answer(agent):
    """``finalize_answer`` runs first; a subclass must not be able to drop it."""
    agent.finalize_answer = lambda answer, _conversation: "REWRITTEN"
    _stub_chat(agent, _answer("original"))
    result = agent.process_query("hello", max_steps=3)
    assert result["result"].startswith("REWRITTEN")
    assert "unverified" in _scope_line(result["result"])


def test_statement_is_emitted_to_the_console_not_only_returned(agent):
    """The scope line must reach the SSE/console surface, not just the dict."""
    _stub_chat(agent, _answer("Done."))
    agent.console.print_final_answer = MagicMock()
    agent.process_query("hello", max_steps=3)
    printed = agent.console.print_final_answer.call_args[0][0]
    assert VERIFICATION_SCOPE_PREFIX in printed


def test_scope_is_reset_between_turns(agent):
    agent.shell_result = {"status": "success", "return_code": 0}
    _stub_chat(
        agent,
        _tool_call("pytest -q"),
        _answer("first"),
        _answer("second"),
    )
    first = agent.process_query("run tests", max_steps=5)
    second = agent.process_query("say hi", max_steps=3)
    assert _scope_line(first["result"]).startswith(
        f"{VERIFICATION_SCOPE_PREFIX}verified"
    )
    assert "unverified" in _scope_line(second["result"])


# ---------------------------------------------------------------------------
# Exit paths that set ``final_answer`` directly — the substance of the issue
# ---------------------------------------------------------------------------


def test_cancel_event_timeout_path_carries_the_statement(agent):
    event = threading.Event()
    event.set()
    agent._cancel_event = event
    _stub_chat(agent, _answer("never reached"))
    result = agent.process_query("do something", max_steps=5)
    assert "exceeded the allowed" in result["result"]
    assert "unverified" in _scope_line(result["result"])


def test_llm_connection_error_path_carries_the_statement(agent):
    _stub_chat(agent, ConnectionError("connection refused"))
    result = agent.process_query("hello", max_steps=3)
    assert "trouble reaching the language model" in result["result"]
    assert "unverified" in _scope_line(result["result"])


def test_context_overflow_path_carries_the_statement(agent):
    overflow = RuntimeError(
        "request (99999 tokens) exceeds the available context size (65536 tokens)"
    )
    _stub_chat(agent, overflow, overflow)
    with patch.object(_DummyAgent, "_is_loaded_ctx_too_small", return_value=False):
        result = agent.process_query("summarize everything", max_steps=3)
    assert "context window" in result["result"]
    assert "unverified" in _scope_line(result["result"])


def test_generic_llm_error_path_carries_the_statement(agent):
    _stub_chat(agent, RuntimeError("kaboom"))
    with patch.object(_DummyAgent, "_is_loaded_ctx_too_small", return_value=False):
        result = agent.process_query("hello", max_steps=3)
    assert "unexpected problem" in result["result"]
    assert "unverified" in _scope_line(result["result"])


def test_parse_give_up_path_carries_the_statement(agent):
    bad = '{"__tool_calls__": [{"function": {"name": "x", "arguments": "{'
    _stub_chat(agent, bad, bad, bad, bad, bad)
    result = agent.process_query("do it", max_steps=10)
    assert "trouble formatting my tool call" in result["result"]
    assert "unverified" in _scope_line(result["result"])


def test_loop_break_summary_path_carries_the_statement(agent):
    """A repeated failing check breaks the loop — and still states its scope.

    This exit is the clearest case for the feature: the loop-break summary
    reads "Task completed with <tool>" even though every call errored, and
    the scope line is what tells the user the check did not pass. Correcting
    that summary itself belongs to the answer-guard work in #3381.
    """
    agent.max_consecutive_repeats = 2
    agent.shell_result = {"status": "error", "error": "boom", "return_code": 1}
    call = _tool_call("pytest -q")
    _stub_chat(agent, call, call, call, call)
    result = agent.process_query("run the tests", max_steps=6)
    assert "Task completed with" in result["result"]
    # Checks ran and did not pass — not "unverified", not "verified".
    assert "partially verified" in _scope_line(result["result"])


def test_max_steps_path_carries_the_statement(agent):
    """No answer was ever produced; the max-steps message still states scope."""
    _stub_chat(agent, _tool_call("ls -la"), _tool_call("ls -la"))
    result = agent.process_query("browse", max_steps=1)
    assert "Reached maximum steps limit" in result["result"]
    assert "unverified" in _scope_line(result["result"])


# ---------------------------------------------------------------------------
# The one deliberate exclusion (#3386)
# ---------------------------------------------------------------------------


def test_console_cancellation_stays_empty(agent):
    """A cancelled turn returns an empty result on purpose — do not fill it."""
    agent.streaming = True
    agent.console.cancelled = threading.Event()
    agent.console.cancelled.set()

    chat = MagicMock()

    def _stream(*_, **__):
        chunk = MagicMock()
        chunk.is_complete = False
        chunk.text = "partial "
        yield chunk

    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat

    result = agent.process_query("hello", max_steps=3)
    assert result["status"] == "cancelled"
    assert result["result"] == ""
    assert VERIFICATION_SCOPE_PREFIX not in result["result"]


def test_empty_answer_is_left_empty(agent):
    """``_with_verification_scope`` never turns a blank answer non-blank."""
    assert agent._with_verification_scope("") == ""
    assert agent._with_verification_scope("   ") == "   "
    assert agent._with_verification_scope(None) is None


# ---------------------------------------------------------------------------
# Agent-UI surface: the SSE cleaners must not leave a scope-line-only message
# ---------------------------------------------------------------------------


def _sse_answer(raw: str) -> str:
    """The ``answer`` event content the Agent UI would emit for *raw*."""
    from gaia.ui.sse_handler import SSEOutputHandler

    captured: list = []
    handler = SSEOutputHandler.__new__(SSEOutputHandler)
    handler._emit = captured.append
    handler._elapsed = lambda: 0.0
    handler._step_count = 0
    handler._tool_count = 0
    handler._turn_metrics = None
    handler.print_final_answer(raw)
    return captured[0]["content"]


def test_sse_keeps_the_scope_line_on_a_real_answer():
    raw = f"Here is the answer.\n\n{build_verification_scope([])}"
    content = _sse_answer(raw)
    assert content.startswith("Here is the answer.")
    assert VERIFICATION_SCOPE_PREFIX in content


def test_sse_card_echo_stays_empty_rather_than_scope_line_only():
    """An answer the cleaners strip to nothing must not become a scope line."""
    raw = f'{{"thought": "done", "answer": ""}}\n\n{build_verification_scope([])}'
    assert _sse_answer(raw) == ""


# ---------------------------------------------------------------------------
# A check that never ran is not a check that failed (#3677)
# ---------------------------------------------------------------------------

# Every shape ``run_shell_command`` can return, and whether the call ran. A
# refusal and a failing run are both ``status: error``; only the refusal says
# so, because only the refusal knows.
EXECUTION_EVIDENCE_CASES = [
    (
        "allowlist_refusal",
        {
            **NOT_EXECUTED,
            "status": "error",
            "error": "Command 'python3.14' is not available to this agent.",
            "has_errors": True,
        },
        False,
    ),
    (
        "declined_confirmation",
        {"status": "denied", "error": "The user declined to run this command."},
        False,
    ),
    (
        "missing_executable",
        {
            **NOT_EXECUTED,
            "status": "error",
            "error": "[Errno 2] No such file or directory: 'pytest'",
            "has_errors": True,
        },
        False,
    ),
    (
        "policy_refusal_before_the_prompt",
        {
            **NOT_EXECUTED,
            "status": "error",
            "error": "Shell operators are not allowed for security reasons.",
            "has_errors": True,
        },
        False,
    ),
    (
        "ran_and_failed",
        {
            "status": "success",
            "return_code": 1,
            "stdout": "1 failed",
            "stderr": "",
            "has_errors": True,
        },
        True,
    ),
    (
        "ran_and_passed",
        {"status": "success", "return_code": 0, "stdout": "5 passed", "stderr": ""},
        True,
    ),
    (
        "timed_out_mid_run",
        {
            "status": "error",
            "error": "Command timed out after 180 seconds",
            "has_errors": True,
            "timed_out": True,
            "duration_seconds": 180.0,
        },
        True,
    ),
    # The direction the evidence-sniffing first draft got wrong: a tool that
    # really did run a check and reports only a message must NOT read as
    # refused. Silence is not a refusal.
    (
        "bare_error_from_a_check_that_ran",
        {"status": "error", "error": "pytest exited 1", "has_errors": True},
        True,
    ),
    ("bare_error_with_no_status", {"error": "something went wrong"}, True),
    ("plain_string_result", "5 passed", True),
]


def _blocked(label="pytest", name="run_shell_command"):
    return {"tool": name, "check_label": label, "failed": True, "ran": False}


def test_a_refused_check_is_unverified_not_partially_verified():
    statement = build_verification_scope([_blocked()])
    assert statement.startswith(f"{VERIFICATION_SCOPE_PREFIX}unverified")
    assert "did not run" in statement
    assert "did not pass" not in statement


def test_a_refused_check_names_what_was_requested():
    statement = build_verification_scope([_blocked("pytest")])
    assert "pytest" in statement
    assert "refused before execution" in statement


def test_a_declined_check_is_not_reported_as_passing():
    """``status: denied`` is not an error result, so it read as a pass."""
    declined = {
        "tool": "run_shell_command",
        "check_label": "pytest",
        "failed": False,
        "ran": False,
    }
    statement = build_verification_scope([declined])
    assert "ran and passed" not in statement
    assert statement.startswith(f"{VERIFICATION_SCOPE_PREFIX}unverified")


def test_a_refused_check_alongside_a_passing_one_is_only_partial():
    statement = build_verification_scope(
        [{"tool": "t", "check_label": "ruff", "failed": False}, _blocked("pytest")]
    )
    assert statement.startswith(f"{VERIFICATION_SCOPE_PREFIX}partially verified")
    assert "ruff ran and passed" in statement
    assert "pytest did not run" in statement


def test_a_refused_check_does_not_inflate_the_ran_tool_count():
    statement = build_verification_scope(
        [
            {"tool": "read_file", "check_label": None, "failed": False},
            _blocked("pytest"),
        ]
    )
    assert "2 tool calls ran" not in statement


def test_non_check_tools_that_never_ran_are_not_counted_as_having_run():
    statement = build_verification_scope(
        [{"tool": "read_file", "check_label": None, "failed": True, "ran": False}]
    )
    assert "no tools ran" in statement


def test_a_tool_that_ran_and_errored_still_counts_as_a_tool_call_that_ran():
    """An error is not a refusal — read_file on a missing path did run."""
    statement = build_verification_scope(
        [
            {
                "tool": "read_file",
                "check_label": None,
                "failed": True,
                "ran": check_was_executed(
                    {"status": "error", "error": "File not found"}
                ),
            }
        ]
    )
    assert "1 tool call ran" in statement


def test_the_footer_is_still_bounded_with_blocked_checks():
    executions = [_blocked(f"checker-{i:03d}-with-a-long-name") for i in range(200)]
    assert len(build_verification_scope(executions)) <= VERIFICATION_SCOPE_MAX_CHARS


# --- through the real agent loop -------------------------------------------


REJECTION_RESULT = {
    **NOT_EXECUTED,
    "status": "error",
    "error": (
        "Command 'python3.14' is not available to this agent. It is granted "
        "only to a skill that declares 'shell:execute:python3.14'."
    ),
    "has_errors": True,
}


def test_loop_reports_a_rejected_pytest_as_never_having_run(agent):
    agent.shell_result = REJECTION_RESULT
    _stub_chat(
        agent,
        _tool_call("python3.14 -m pytest tests/"),
        _answer("The command was rejected, so no tests ran."),
    )

    line = _scope_line(agent.process_query("run the tests")["result"])

    assert line.startswith(f"{VERIFICATION_SCOPE_PREFIX}unverified")
    assert "did not run" in line
    assert "did not pass" not in line


def test_loop_reports_a_declined_pytest_as_never_having_run(agent):
    agent.shell_result = {"status": "denied", "error": "The user said no."}
    _stub_chat(
        agent,
        _tool_call("pytest tests/"),
        _answer("You declined, so nothing was checked."),
    )

    line = _scope_line(agent.process_query("run the tests")["result"])

    assert line.startswith(f"{VERIFICATION_SCOPE_PREFIX}unverified")
    assert "ran and passed" not in line


def test_loop_still_reports_a_real_failing_test_as_having_run(agent):
    agent.shell_result = {
        "status": "success",
        "return_code": 1,
        "stdout": "1 failed",
        "stderr": "",
        "has_errors": True,
    }
    _stub_chat(agent, _tool_call("pytest tests/"), _answer("One test failed."))

    line = _scope_line(agent.process_query("run the tests")["result"])

    assert "partially verified" in line
    assert "did not pass" in line


def test_loop_still_reports_a_passing_test_as_verified(agent):
    agent.shell_result = {
        "status": "success",
        "return_code": 0,
        "stdout": "5 passed",
        "stderr": "",
    }
    _stub_chat(agent, _tool_call("pytest tests/"), _answer("All green."))

    line = _scope_line(agent.process_query("run the tests")["result"])

    assert line.startswith(f"{VERIFICATION_SCOPE_PREFIX}verified")


# --- the real shell tool declares its own refusals --------------------------
#
# ``check_was_executed`` believes what a tool says. These pin that the tool
# actually says it — a refusal that forgets the stamp reads as a check that ran.


@pytest.fixture
def shell_tool():
    """The real ``run_shell_command``, on a bare mixin."""
    import importlib

    from gaia.agents.base.tools import _TOOL_REGISTRY

    module = importlib.import_module("gaia.agents.tools.shell_tools")
    mixin = module.ShellToolsMixin()
    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_shell_tools()
        entry = _TOOL_REGISTRY.get("run_shell_command")
        assert entry is not None, "run_shell_command was not registered"
        yield entry["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


REFUSED_COMMANDS = [
    ("not_on_the_allowlist", "/usr/local/bin/python3.14 -m pytest tests/"),
    ("shell_operators", "pytest tests/ && echo done"),
    ("unknown_binary", "definitely-not-a-real-binary --version"),
]


@pytest.mark.parametrize(
    "command",
    [c for _, c in REFUSED_COMMANDS],
    ids=[i for i, _ in REFUSED_COMMANDS],
)
def test_a_refused_command_says_it_did_not_run(shell_tool, command):
    result = shell_tool(command)
    assert result["status"] == "error", result
    assert check_was_executed(result) is False, result


def test_a_refused_pytest_leaves_the_turn_unverified(shell_tool):
    """The whole chain: real refusal -> real classifier -> footer."""
    result = shell_tool("/usr/local/bin/python3.14 -m pytest tests/")
    statement = build_verification_scope(
        [
            {
                "tool": "run_shell_command",
                "check_label": verification_check_label(
                    "run_shell_command",
                    {"command": "/usr/local/bin/python3.14 -m pytest tests/"},
                ),
                "failed": True,
                "ran": check_was_executed(result),
            }
        ]
    )
    assert statement.startswith(f"{VERIFICATION_SCOPE_PREFIX}unverified")
    assert "did not pass" not in statement


def test_a_command_that_really_runs_says_it_ran(shell_tool):
    result = shell_tool("echo hello")
    assert result["status"] == "success", result
    assert check_was_executed(result) is True


# --- every dispatch-time rejection declares itself too (#3677 review) --------
#
# ``_execute_tool`` has five exits that return before the tool body: unknown
# name, guardrail refusal, missing argument, unexpected argument, uncoercible
# argument. All five reach ``_note_verification_signal``, so all five have to
# say nothing ran — a model that calls the shell tool for pytest with one
# hallucinated kwarg is rejected at dispatch, and the footer used to call that
# a test that ran and failed.

DISPATCH_REJECTIONS = [
    ("unknown_tool_name", "no_such_tool_at_all", {"command": "pytest tests/"}),
    (
        "missing_required_argument",
        _SANDBOX_SHELL,
        {},
    ),
    (
        "unexpected_argument",
        _SANDBOX_SHELL,
        {"command": "pytest tests/", "timeout_sec": 30},
    ),
    (
        "uncoercible_argument",
        "sandbox_check_with_a_timeout",
        {"command": "pytest tests/", "timeout": "not-a-number"},
    ),
]


@pytest.mark.parametrize(
    "tool_name,tool_args",
    [(name, args) for _, name, args in DISPATCH_REJECTIONS],
    ids=[case[0] for case in DISPATCH_REJECTIONS],
)
def test_a_call_rejected_at_dispatch_says_it_did_not_run(agent, tool_name, tool_args):
    result = agent._execute_tool(tool_name, tool_args)
    assert result["status"] == "error", result
    assert check_was_executed(result) is False, result


def test_a_hallucinated_kwarg_on_a_pytest_call_leaves_the_turn_unverified(agent):
    """The exact shape the review reproduced."""
    args = {"command": "pytest tests/", "timeout_sec": 30}
    result = agent._execute_tool(_SANDBOX_SHELL, args)
    statement = build_verification_scope(
        [
            {
                "tool": _SANDBOX_SHELL,
                "check_label": verification_check_label(_SANDBOX_SHELL, args),
                "failed": True,
                "ran": check_was_executed(result),
            }
        ]
    )
    assert statement.startswith(f"{VERIFICATION_SCOPE_PREFIX}unverified")
    assert "ran and did not pass" not in statement


# --- a refusal the agent recovered from is not an unrun check ---------------


def test_a_refused_check_that_later_ran_is_not_also_reported_as_unrun():
    statement = build_verification_scope(
        [
            _blocked("pytest"),
            {"tool": "run_shell_command", "check_label": "pytest", "failed": False},
        ]
    )
    assert statement == f"{VERIFICATION_SCOPE_PREFIX}verified — pytest ran and passed."


def test_a_different_check_left_unrun_is_still_reported():
    statement = build_verification_scope(
        [
            _blocked("ruff"),
            {"tool": "run_shell_command", "check_label": "pytest", "failed": False},
        ]
    )
    assert statement.startswith(f"{VERIFICATION_SCOPE_PREFIX}partially verified")
    assert "ruff did not run" in statement


def test_a_refused_check_that_later_ran_and_failed_is_reported_as_failing():
    statement = build_verification_scope(
        [
            _blocked("pytest"),
            {"tool": "run_shell_command", "check_label": "pytest", "failed": True},
        ]
    )
    assert "ran and did not pass" in statement
    assert "did not run" not in statement
