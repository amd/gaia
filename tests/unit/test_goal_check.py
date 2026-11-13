# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Goal mode checker: checks first, then named outputs, then a cited verdict."""

import json
import sys
from types import SimpleNamespace

import pytest

from gaia.agents.base.goal_check import (
    EVALUATOR_MAX_TOKENS,
    GoalCheckError,
    check_goal,
    missing_outputs,
    parse_verdict,
    run_checks,
    tool_digest,
    tool_results,
)
from gaia.agents.base.goal_run import GoalLedger, GoalSpec, GoalVerdict

PY = f'"{sys.executable}"'


class _Chat:
    """Records the evaluator call and replies with a fixed verdict."""

    def __init__(self, reply, finish_reason="stop"):
        self.reply = reply
        self.finish_reason = finish_reason
        self.calls = []

    def send_messages(self, **kwargs):
        self.calls.append(kwargs)
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return SimpleNamespace(text=text, finish_reason=self.finish_reason)


def _verdict(**kw):
    base = {"met": False, "missing": [], "evidence": [], "blocked": False}
    base["question"] = ""
    base.update(kw)
    return base


_RESULT = {
    "result": "All done and verified!",
    "conversation": [
        {"role": "user", "content": "go"},
        {
            "role": "tool",
            "name": "run_shell_command",
            "tool_args": {"command": "pytest"},
            "content": "===== 12 passed in 0.4s =====",
        },
    ],
}


# --- tier 1: check commands -------------------------------------------------


def test_run_checks_keeps_exit_code_and_output(tmp_path):
    ok, bad = run_checks(
        [f"{PY} -c \"print('fine')\"", f'{PY} -c "import sys; sys.exit(3)"'],
        str(tmp_path),
    )
    assert ok.passed and "fine" in ok.output_tail
    assert not bad.passed and bad.exit_code == 3


def test_run_checks_reports_a_timeout_as_failed(tmp_path):
    (result,) = run_checks(
        [f'{PY} -c "import time; time.sleep(5)"'], str(tmp_path), timeout=0.5
    )
    assert not result.passed and "timed out" in result.output_tail


def test_a_failing_check_decides_not_met_without_asking_the_model(tmp_path):
    chat = _Chat(_verdict(met=True, evidence=["12 passed in 0.4s"]))
    spec = GoalSpec(
        "tests pass", checks=(f"{PY} -c \"print('2 failed'); raise SystemExit(1)\"",)
    )
    verdict = check_goal(spec, GoalLedger(), _RESULT, chat, str(tmp_path))
    assert not verdict.met and verdict.decided_by == "check"
    assert "exit 1" in verdict.missing[0] and "2 failed" in verdict.missing[0]
    assert chat.calls == []


# --- tier 2: named outputs --------------------------------------------------


def test_missing_outputs_names_files_the_goal_says_to_write(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "summary.md").write_text("x")
    condition = "write a summary to out/summary.md and save results to report.csv"
    assert missing_outputs(condition, str(tmp_path)) == ["report.csv"]


def test_a_missing_named_output_decides_not_met_without_asking_the_model(tmp_path):
    chat = _Chat(_verdict(met=True, evidence=["12 passed in 0.4s"]))
    spec = GoalSpec("save the totals to totals.csv")
    verdict = check_goal(spec, GoalLedger(), _RESULT, chat, str(tmp_path))
    assert not verdict.met and verdict.decided_by == "evidence"
    assert verdict.missing == ["totals.csv does not exist yet"]
    assert chat.calls == []


# --- tier 3: the evaluator --------------------------------------------------


def test_evaluator_call_shape(tmp_path):
    chat = _Chat(_verdict(met=True, evidence=["12 passed in 0.4s"]))
    check_goal(GoalSpec("the tests pass"), GoalLedger(), _RESULT, chat, str(tmp_path))
    (call,) = chat.calls
    assert call["tools"] == []
    assert call["max_tokens"] == EVALUATOR_MAX_TOKENS
    schema = call["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == {
        "met",
        "missing",
        "evidence",
        "blocked",
        "question",
    }
    prompt = call["messages"][0]["content"]
    assert call["messages"][0]["role"] == "user"
    assert "12 passed in 0.4s" in prompt
    # The answer reaches the evaluator labelled as a claim.
    assert "claims, not evidence" in prompt and "All done and verified!" in prompt


def test_a_cited_met_holds(tmp_path):
    chat = _Chat(_verdict(met=True, evidence=["12 passed in 0.4s"]))
    verdict = check_goal(
        GoalSpec("the tests pass"), GoalLedger(), _RESULT, chat, str(tmp_path)
    )
    assert verdict.met and verdict.evidence == ["12 passed in 0.4s"]
    assert verdict.decided_by == "evaluator"


def test_a_met_quoting_only_the_answer_is_downgraded(tmp_path):
    # The #8099 shape: the answer claims a check no tool ran.
    chat = _Chat(_verdict(met=True, evidence=["All done and verified!"]))
    verdict = check_goal(
        GoalSpec("the tests pass"), GoalLedger(), _RESULT, chat, str(tmp_path)
    )
    assert not verdict.met
    assert verdict.evidence == []
    assert "no tool result or check output from this round" in verdict.missing[-1]


def test_passing_checks_are_citable_and_mark_the_verdict(tmp_path):
    chat = _Chat(_verdict(met=True, evidence=["counts match: 300"]))
    spec = GoalSpec("counts match", checks=(f"{PY} -c \"print('counts match: 300')\"",))
    verdict = check_goal(spec, GoalLedger(), {"conversation": []}, chat, str(tmp_path))
    assert verdict.met and verdict.decided_by == "evaluator"
    assert verdict.checks_passed == 1


def test_earlier_proof_alone_cannot_prove_the_goal_still_holds(tmp_path):
    # Tests passed in round 1; round 2 changed the code and ran nothing.
    ledger = GoalLedger()
    ledger.record(GoalVerdict(met=False, missing=["x"], evidence=["12 passed"]), [])
    chat = _Chat(_verdict(met=True, evidence=["12 passed"]))
    verdict = check_goal(
        GoalSpec("tests pass"), ledger, {"conversation": []}, chat, str(tmp_path)
    )
    assert not verdict.met
    assert verdict.evidence == ["12 passed"]
    assert "from this round" in verdict.missing[-1]


def test_tool_arguments_are_not_proof(tmp_path):
    # The agent wrote the claim itself; no test ever ran.
    result = {
        "conversation": [
            {
                "role": "tool",
                "name": "write_file",
                "tool_args": {"path": "log.txt", "content": "All 14 tests passed"},
                "content": "Wrote 19 bytes to log.txt",
            }
        ]
    }
    chat = _Chat(_verdict(met=True, evidence=["All 14 tests passed"]))
    verdict = check_goal(
        GoalSpec("tests pass"), GoalLedger(), result, chat, str(tmp_path)
    )
    assert not verdict.met
    # The evaluator still sees the arguments, labelled as such.
    assert "args:" in chat.calls[0]["messages"][0]["content"]


def test_the_end_of_long_output_reaches_the_evaluator_and_counts(tmp_path):
    log = "collecting...\n" + "x" * 5000 + "\n===== 12 passed in 3.1s ====="
    result = {
        "conversation": [{"role": "tool", "name": "run_shell_command", "content": log}]
    }
    chat = _Chat(_verdict(met=True, evidence=["12 passed in 3.1s"]))
    verdict = check_goal(
        GoalSpec("tests pass"), GoalLedger(), result, chat, str(tmp_path)
    )
    assert verdict.met
    assert "12 passed in 3.1s" in chat.calls[0]["messages"][0]["content"]


def test_proof_accumulates_across_rounds_until_the_goal_holds(tmp_path):
    # Round 1 proves part a; round 2 proves part b and cites a's earlier proof.
    spec = GoalSpec("both a.csv and b.csv summed")
    ledger = GoalLedger()
    round1 = {
        "conversation": [
            {"role": "tool", "name": "read_file", "content": "a.csv: 1 2 3 sum=6"}
        ]
    }
    chat = _Chat(
        _verdict(missing=["sum b.csv"], evidence=["a.csv: 1 2 3 sum=6", "made up b"])
    )
    verdict = check_goal(spec, ledger, round1, chat, str(tmp_path))
    assert not verdict.met
    assert verdict.evidence == ["a.csv: 1 2 3 sum=6"]  # the invented quote is dropped
    ledger.record(verdict, [])

    round2 = {
        "conversation": [
            {"role": "tool", "name": "read_file", "content": "b.csv: 4 5 sum=9"}
        ]
    }
    chat = _Chat(
        _verdict(met=True, evidence=["a.csv: 1 2 3 sum=6", "b.csv: 4 5 sum=9"])
    )
    verdict = check_goal(spec, ledger, round2, chat, str(tmp_path))
    assert verdict.met
    assert "a.csv: 1 2 3 sum=6" in chat.calls[0]["messages"][0]["content"]


def test_blocked_carries_the_question(tmp_path):
    chat = _Chat(
        _verdict(
            missing=["the plan file"], blocked=True, question="Where is plan-b2.txt?"
        )
    )
    verdict = check_goal(
        GoalSpec("summarize plan"), GoalLedger(), _RESULT, chat, str(tmp_path)
    )
    assert verdict.blocked and verdict.question == "Where is plan-b2.txt?"


def test_an_evaluator_reply_cut_off_is_an_error(tmp_path):
    chat = _Chat(_verdict(), finish_reason="length")
    with pytest.raises(GoalCheckError, match="reply budget"):
        check_goal(GoalSpec("x"), GoalLedger(), _RESULT, chat, str(tmp_path))


def test_an_evaluator_failure_is_raised_with_context(tmp_path):
    class _Down:
        def send_messages(self, **kwargs):
            raise ConnectionError("model server unreachable")

    with pytest.raises(GoalCheckError, match="model server unreachable"):
        check_goal(GoalSpec("x"), GoalLedger(), _RESULT, _Down(), str(tmp_path))


def test_parse_verdict_rejects_non_json():
    with pytest.raises(GoalCheckError, match="not JSON"):
        parse_verdict("sure, it's done", [], [])


def test_parse_verdict_accepts_a_fenced_reply():
    text = "```json\n" + json.dumps(_verdict(met=True, evidence=["abcdefgh"])) + "\n```"
    assert parse_verdict(text, ["xx ABCDEFGH yy"], []).met


def test_parse_verdict_ignores_quotes_too_short_to_prove_anything():
    verdict = parse_verdict(json.dumps(_verdict(met=True, evidence=["ok"])), ["ok"], [])
    assert not verdict.met


def test_run_checks_closes_stdin(tmp_path):
    (result,) = run_checks(
        [f'{PY} -c "import sys; print(repr(sys.stdin.read()))"'],
        str(tmp_path),
        timeout=30,
    )
    assert result.passed and "''" in result.output_tail


def test_tool_digest_keeps_newest_results_when_over_budget():
    conversation = [
        {"role": "tool", "name": f"t{n}", "content": "x" * 1400} for n in range(20)
    ]
    digest = tool_digest(tool_results(conversation))
    assert digest[-1].startswith("[t19]")
    assert len(digest) < 20
    assert sum(len(entry) for entry in digest) <= 16000
