# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the test-claim contract at the answer seam.

On a benchmark run against a 144K-line codebase the agent ended its turn with
"111 passed, 13 passed" and a footer saying nothing had been checked — 71 tool
calls, not one of them a test run. The counts were invented. The loop already
computed the truth for the footer; nothing compared it to the answer.

Three layers:

* the fact — a :class:`CheckResult` the tool that ran the check attaches, and
  the footer reading it instead of re-parsing text;
* the claim detector and the record check, exercised directly;
* the seam itself, driven through the real loop with a stubbed chat client.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import _MAX_TEST_CLAIM_CORRECTIONS, Agent
from gaia.agents.base.checks import (
    CHECK_RESULT_KEY,
    CheckResult,
    argv_check_label,
    attach_check,
    check_from_command,
    check_from_python_run,
)
from gaia.agents.base.claims import claims_success, passing_test_claim
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import (
    build_verification_scope,
    is_file_mutation,
    strip_verification_scope,
    unsupported_test_claim,
    verification_check_label,
    verification_record,
)

#: Trimmed from the fabricating run.
FABRICATED = (
    "Fixed the family matrix lookup in amdgpu_family_matrix.py.\n\n"
    "Tests: `configure_multi_arch_ci_test` 111 passed, "
    "`amdgpu_family_matrix_test` 13 passed."
)

HONEST = (
    "Fixed the family matrix lookup in amdgpu_family_matrix.py.\n\n"
    "I never got to run anything — treat that file as unfinished work."
)


def _check(passed=True, label="pytest", target="pytest tests/", kind="test"):
    return CheckResult(
        label=label,
        target=target,
        kind=kind,
        passed=passed,
        summary="111 passed in 2.10s" if passed else "3 failed, 108 passed in 2.10s",
    )


# ---------------------------------------------------------------------------
# The fact: produced where the check ran
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["pytest", "-q"], "pytest"),
        ([".venv/bin/python", "-m", "pytest", "tests/"], "pytest"),
        (["python3.11", "-m", "unittest"], "unittest"),
        (["uv", "run", "pytest"], "pytest"),
        (["npm", "run", "test"], "npm run test"),
        (["ruff", "check", "src/"], "ruff"),
        (["echo", "pytest"], None),
        (["cat", "pytest.ini"], None),
        (["grep", "-r", "pytest", "setup.cfg"], None),
        ([], None),
    ],
)
def test_the_program_has_to_be_the_runner(argv, expected):
    assert argv_check_label(argv) == expected


def test_a_piped_run_that_failed_is_a_failure_whatever_the_exit_code():
    """``pytest | tail`` exits with tail's status; the summary still counts."""
    check = check_from_command(
        "pytest -q | tail -3",
        [["pytest", "-q"], ["tail", "-3"]],
        0,
        "FAILED tests/test_x.py::test_a\n3 failed, 108 passed in 2.10s\n",
    )
    assert check is not None
    assert check.passed is False
    assert check.summary == "3 failed, 108 passed in 2.10s"


def test_a_timed_out_run_did_not_pass():
    check = check_from_command("pytest", [["pytest"]], None, "", "")
    assert check is not None and check.passed is False


def test_a_command_that_is_not_a_check_produces_none():
    assert check_from_command("cat pytest.ini", [["cat", "pytest.ini"]], 0) is None


def test_the_shell_tool_declares_a_non_check_so_the_footer_does_not_guess():
    """The text guess reads ``echo pytest`` as a pytest run; the tool knows."""
    from gaia.agents.base.tools import _TOOL_REGISTRY, get_tool_metadata
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    class _Host(ShellToolsMixin):
        pass

    saved = dict(_TOOL_REGISTRY)
    try:
        _Host().register_shell_tools()
        run_shell_command = get_tool_metadata("run_shell_command")["function"]
        result = run_shell_command(command="echo pytest")
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    assert result["status"] == "success", result
    assert result[CHECK_RESULT_KEY] is None
    record = verification_record(
        "run_shell_command", {"command": "echo pytest"}, result, errored=False
    )
    assert record["check_label"] is None


@pytest.mark.parametrize(
    "stdout,return_code,passed",
    [
        ("5 passed in 0.10s", 0, True),
        ("1 failed, 4 passed in 0.10s", 1, False),
        ("70 passed, 19 subtests passed in 1.32s", 0, True),
        ("70 passed, 2 subtests failed, 17 subtests passed in 1.32s", 1, False),
        ("Ran 4 tests in 0.002s\n\nOK", 0, True),
        ("Ran 4 tests in 0.002s\n\nFAILED (failures=1)", 1, False),
    ],
)
def test_a_python_run_reports_its_test_outcome(stdout, return_code, passed):
    check = check_from_python_run("snippet:abc", return_code, stdout)
    assert check is not None
    assert check.kind == "test"
    assert check.passed is passed


def test_a_python_run_without_a_runner_summary_is_not_a_check():
    assert check_from_python_run("snippet:abc", 0, "42\n") is None


def test_the_footer_reads_the_fact_without_the_text_parser():
    """A declared check never reaches the regexes that guess from text."""
    result = attach_check({"status": "success", "stdout": "noise"}, _check())
    with (
        patch(
            "gaia.agents.base.verification.runner_summary",
            side_effect=AssertionError("text parser consulted"),
        ),
        patch(
            "gaia.agents.base.verification.command_check_label",
            side_effect=AssertionError("text parser consulted"),
        ),
    ):
        record = verification_record(
            "run_shell_command", {"command": "pytest tests/"}, result, errored=False
        )
        assert verification_check_label(
            "run_shell_command", {"command": "pytest tests/"}, result
        ) == ("pytest")
    assert record["check_label"] == "pytest"
    assert record["check_kind"] == "test"
    assert record["declared"] is True
    assert "verified — pytest ran and passed" in build_verification_scope([record])


def test_the_fact_overrides_the_exit_code():
    """A tool that knows its run failed is believed over a zero exit."""
    result = attach_check({"status": "success", "return_code": 0}, _check(False))
    record = verification_record(
        "run_shell_command", {"command": "pytest -q | tail"}, result, errored=False
    )
    assert record["failed"] is True


def test_declared_not_a_check_is_not_second_guessed():
    """``cat pytest.ini`` ran and was not a test; the text guess would say it was."""
    result = attach_check({"status": "success", "return_code": 0}, None)
    record = verification_record(
        "run_shell_command", {"command": "cat pytest.ini"}, result, errored=False
    )
    assert record["check_label"] is None
    assert result[CHECK_RESULT_KEY] is None


def test_a_legacy_result_still_goes_through_the_fallback():
    """Hub agents and MCP tools that declare nothing keep working."""
    record = verification_record(
        "run_shell_command",
        {"command": "python -m pytest tests/"},
        {"status": "success", "return_code": 0},
        errored=False,
    )
    assert record["check_label"] == "pytest"
    assert record["check_kind"] == "test"
    assert record["declared"] is False
    assert record["failed"] is False


def test_a_legacy_run_python_result_still_goes_through_the_fallback():
    result = {
        "status": "success",
        "return_code": 0,
        "stdout": "70 passed, 19 subtests passed in 1.32s",
    }
    assert verification_check_label("run_python", {}, result) == "pytest"


def test_a_malformed_check_fails_loudly():
    with pytest.raises(ValueError, match="Malformed 'check_result'"):
        verification_record(
            "run_shell_command",
            {"command": "pytest"},
            {"status": "success", CHECK_RESULT_KEY: {"label": "pytest"}},
            errored=False,
        )


# ---------------------------------------------------------------------------
# Claim detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer,expected",
    [
        (FABRICATED, "111 passed"),
        ("69 passed", "69 passed"),
        ("All 12 tests pass.", "12 tests pass"),
        ("The suite is green.", "suite is green"),
        ("Every test is passing now.", "test is passing"),
        ("all tests passing", "tests passing"),
        ("70 passed, 19 subtests passed in 1.32s", "70 passed"),
        ("The full test suite passes.", "test suite passes"),
    ],
)
def test_a_concrete_test_outcome_is_a_claim(answer, expected):
    claim = passing_test_claim(answer)
    assert claim is not None
    assert claim.lower() == expected.lower()


@pytest.mark.parametrize(
    "answer",
    [
        HONEST,
        "The tests do not pass.",
        "69 passed, 3 failed.",
        "3 failed",
        "I could not get the last test passing.",
        "Unverified — I did not run the suite.",
        "Run `pytest tests/unit` to confirm the tests pass.",
        "You should run the test suite before tagging.",
        "Next steps:\n- Run the full test suite\n- Tag a release",
        "I rewrote the loader and documented TOYBOX_CONFIG.",
        "Done. Both bugs are fixed.",
        "",
    ],
)
def test_no_concrete_test_outcome_is_not_a_claim(answer):
    assert passing_test_claim(answer) is None


def test_the_broad_success_detector_lives_in_one_place():
    """``claims_success`` moved here; the eval imports it rather than copying."""
    from gaia.eval import code_bench

    assert code_bench.claims_success is claims_success
    assert claims_success("Both bugs are fixed now.")
    assert not claims_success("I could not get the last test passing.")


# ---------------------------------------------------------------------------
# The record side
# ---------------------------------------------------------------------------


def _run(check=None, name="run_shell_command", command="pytest tests/"):
    """A record built the way the loop builds it, from a declared result."""
    result = attach_check({"status": "success", "return_code": 0}, check)
    return verification_record(name, {"command": command}, result, errored=False)


def _edit(failed=False):
    return verification_record(
        "edit_file",
        {"file_path": "a.py"},
        {"status": "error" if failed else "success"},
        errored=failed,
    )


def test_no_check_at_all_does_not_support_a_pass_claim():
    claim, why = unsupported_test_claim(FABRICATED, [_run(), _run()])
    assert claim == "111 passed"
    assert "no test run" in why


def test_a_passing_check_supports_the_claim():
    assert unsupported_test_claim(FABRICATED, [_run(_check())]) is None


def test_a_failed_check_does_not_support_a_pass_claim():
    claim, why = unsupported_test_claim(FABRICATED, [_run(_check(False))])
    assert claim == "111 passed"
    assert "did not pass" in why


def test_a_check_that_ran_before_the_last_edit_does_not_support_the_claim():
    claim, why = unsupported_test_claim(FABRICATED, [_run(_check()), _edit()])
    assert claim == "111 passed"
    assert "before the last file change" in why


def test_a_check_rerun_after_the_edit_supports_the_claim():
    records = [_run(_check()), _edit(), _run(_check())]
    assert unsupported_test_claim(FABRICATED, records) is None


def test_an_edit_that_failed_is_not_a_file_change():
    assert unsupported_test_claim(FABRICATED, [_run(_check()), _edit(True)]) is None


def test_a_lint_run_is_not_a_test_run():
    lint = _check(label="ruff", target="ruff check", kind="lint")
    claim, why = unsupported_test_claim(FABRICATED, [_run(lint)])
    assert claim == "111 passed"
    assert "no test run" in why


def test_a_refused_check_never_ran():
    refused = verification_record(
        "run_shell_command",
        {"command": "pytest"},
        {"status": "error", "executed": False},
        errored=True,
    )
    claim, why = unsupported_test_claim(FABRICATED, [refused])
    assert claim == "111 passed"
    assert "no test run" in why


def test_an_answer_without_a_claim_is_never_unsupported():
    assert unsupported_test_claim(HONEST, []) is None
    assert unsupported_test_claim("", []) is None


@pytest.mark.parametrize(
    "tool_name,expected",
    [
        ("edit_file", True),
        ("write_file", True),
        ("replace_function", True),
        ("read_file", False),
        ("run_shell_command", False),
    ],
)
def test_file_mutation_tools(tool_name, expected):
    assert is_file_mutation(tool_name) is expected


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class _DummyAgent(Agent):
    #: Set per-test; the stub runner returns it verbatim.
    run_result = attach_check({"status": "success", "return_code": 0}, None)

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def sandbox_runner_for_test_claim_test(command: str) -> dict:
            """Run a command in a sandbox."""
            del command
            return agent.run_result

        @tool
        def sandbox_editor_for_test_claim_test(file_path: str) -> dict:
            """Edit a file in a sandbox."""
            del file_path
            return {"status": "success"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with (
        patch("gaia.agents.base.agent.AgentSDK"),
        patch(
            "gaia.agents.base.verification._FILE_MUTATION_TOOLS",
            frozenset({"sandbox_editor_for_test_claim_test"}),
        ),
    ):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        yield a


def _stub_chat(agent_, *responses):
    queue = list(responses)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        if not queue:
            raise AssertionError("chat stub ran out of scripted responses")
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    chat = MagicMock()
    chat.send_messages = MagicMock(side_effect=_send)
    agent_.chat = chat
    return sent


def _answer(text: str) -> str:
    return json.dumps({"thought": "done", "answer": text})


def _run_tests() -> str:
    return json.dumps(
        {
            "thought": "checking",
            "tool": "sandbox_runner_for_test_claim_test",
            "tool_args": {"command": "pytest tests/"},
        }
    )


def _edit_file() -> str:
    return json.dumps(
        {
            "thought": "editing",
            "tool": "sandbox_editor_for_test_claim_test",
            "tool_args": {"file_path": "a.py"},
        }
    )


def _final_text(result) -> str:
    return strip_verification_scope(result["result"]).strip()


def test_an_unsupported_claim_is_corrected_once(agent):
    sent = _stub_chat(agent, _answer(FABRICATED), _answer(HONEST))

    result = agent.process_query("Fix the matrix lookup", max_steps=10)

    assert len(sent) == 2
    correction = sent[1][-1]["content"]
    assert "111 passed" in correction
    assert "no test run" in correction
    assert _final_text(result) == HONEST


def test_a_supported_claim_is_emitted_unchanged(agent):
    agent.run_result = attach_check({"status": "success"}, _check())
    sent = _stub_chat(agent, _run_tests(), _answer(FABRICATED))

    result = agent.process_query("Fix the matrix lookup", max_steps=10)

    assert len(sent) == 2
    assert _final_text(result) == FABRICATED


def test_a_failed_check_in_the_record_is_named_in_the_correction(agent):
    agent.run_result = attach_check({"status": "success"}, _check(False))
    sent = _stub_chat(agent, _run_tests(), _answer(FABRICATED), _answer(HONEST))

    agent.process_query("Fix the matrix lookup", max_steps=10)

    assert len(sent) == 3
    assert "did not pass" in sent[2][-1]["content"]


def test_a_check_before_the_last_edit_does_not_support_the_claim(agent):
    agent.run_result = attach_check({"status": "success"}, _check())
    sent = _stub_chat(
        agent, _run_tests(), _edit_file(), _answer(FABRICATED), _answer(HONEST)
    )

    agent.process_query("Fix the matrix lookup", max_steps=10)

    assert len(sent) == 4
    assert "before the last file change" in sent[3][-1]["content"]


def test_a_check_after_the_last_edit_supports_the_claim(agent):
    agent.run_result = attach_check({"status": "success"}, _check())
    sent = _stub_chat(agent, _edit_file(), _run_tests(), _answer(FABRICATED))

    result = agent.process_query("Fix the matrix lookup", max_steps=10)

    assert len(sent) == 3
    assert _final_text(result) == FABRICATED


def test_an_answer_without_a_claim_is_never_corrected(agent):
    sent = _stub_chat(agent, _answer(HONEST))

    result = agent.process_query("Fix the matrix lookup", max_steps=10)

    assert len(sent) == 1
    assert _final_text(result) == HONEST


def test_a_second_unsupported_answer_is_suppressed(agent):
    repeats = [_answer(FABRICATED)] * (_MAX_TEST_CLAIM_CORRECTIONS + 2)
    sent = _stub_chat(agent, *repeats)

    result = agent.process_query("Fix the matrix lookup", max_steps=10)

    assert len(sent) == _MAX_TEST_CLAIM_CORRECTIONS + 1
    assert FABRICATED not in result["result"]
    assert result["status"] == "incomplete"


def test_no_step_left_returns_incomplete_without_the_claim(agent):
    sent = _stub_chat(agent, _answer(FABRICATED))

    result = agent.process_query("Fix the matrix lookup", max_steps=1)

    assert len(sent) == 1
    assert FABRICATED not in result["result"]
    assert result["status"] == "incomplete"
    assert "unverified" in result["result"]
