# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The judge sees each attempt's own evidence, and grades a TheRock fix against the fix."""

import json

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import therock

from .conftest import cc_tool, gaia_tool

PASSED = "....\n4 passed in 0.02s\n"
TR_TASK = next(t for t in ft.load_suite("therock") if t.id == "tr-8319")
CODING = next(t for t in ft.load_suite("full") if t.id == "02-bugfix")


def _transcript(*calls, answer="done"):
    return {
        "prompt": "p",
        "answer": answer,
        "conversation": [e for c in calls for e in c],
    }


def _capture(monkeypatch):
    """Record the payload the judge is sent, and grade every attempt the same."""
    sent = []

    def fake_run(cmd, **kwargs):
        sent.append(kwargs["input"])

        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {
                    "result": json.dumps(
                        {
                            key: {
                                "instruction_compliance": 5,
                                "work_quality": 4,
                                "reasoning": 4,
                                "fabrication_free": 5,
                                "one_line": "ok",
                                "answers_correctly": True,
                                "missing": "",
                                "solves_problem": True,
                                "right_place": True,
                                "updated_tests": True,
                                "approach": "different but valid",
                            }
                            for key in ("a", "b", "tr-8319", "tr-7998")
                        }
                    ),
                    "total_cost_usd": 0.2,
                }
            )

        return Proc()

    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(ft.subprocess, "run", fake_run)
    return sent


def test_the_judge_is_shown_the_tool_record_and_the_checks_that_ran(monkeypatch):
    sent = _capture(monkeypatch)
    transcript = _transcript(
        gaia_tool("run_shell_command", {"command": "pytest"}, {"stdout": PASSED}),
        answer="Fixed it; the suite passes.",
    )
    attempt = ft.attempt_from("a", transcript, "+++ b/toybox/dates.py\n", CODING)
    ft.judge_batch([attempt], "judge-model", {})
    (payload,) = sent
    assert "CHECKS THAT ACTUALLY RAN" in payload and "4 passed in 0.02s" in payload
    assert "TOOL RECORD" in payload and "run_shell_command" in payload
    # The rubric tells the judge to grade claims against the record, not the footer.
    rubric = " ".join(payload.split())
    assert "supported by the diff or by a tool result in the record" in rubric
    assert "footer in an answer is written by the harness" in rubric
    assert "CHECKS lists no run at all, score 1-2" in rubric


def test_a_claude_code_attempt_carries_the_same_evidence(monkeypatch):
    sent = _capture(monkeypatch)
    transcript = {
        "prompt": "p",
        "answer": "Fixed it.",
        "events": [
            e for c in [cc_tool(1, "Bash", {"command": "pytest"}, PASSED)] for e in c
        ],
    }
    ft.judge_batch([ft.attempt_from("a", transcript, "diff", CODING)], "m", {})
    (payload,) = sent
    assert "4 passed in 0.02s" in payload and "Bash" in payload


def test_therock_attempts_are_graded_against_the_upstream_fix(monkeypatch):
    sent = _capture(monkeypatch)
    attempt = ft.attempt_from(
        "tr-8319",
        _transcript(answer="Moved gfx90a to postsubmit."),
        "+++ b/build_tools/github_actions/amdgpu_family_matrix.py\n",
        TR_TASK,
        reference="+++ b/build_tools/github_actions/amdgpu_family_matrix.py\n+POSTSUBMIT\n",
    )
    grades = ft.judge_batch([attempt], "m", {})
    (payload,) = sent
    assert "THE PULL REQUEST THAT FIXED IT UPSTREAM" in payload
    assert "ONE correct answer, not the only one" in payload
    # The toybox project is not sent: it has nothing to do with TheRock.
    assert "toybox" not in payload
    assert grades["tr-8319"]["approach"] == "different but valid"
    assert grades["tr-8319"]["solves_problem"] is True


def test_a_therock_grade_must_carry_its_verdicts():
    with pytest.raises(ft.JudgeError, match="solves_problem"):
        ft._validate_grade(
            {
                "instruction_compliance": 5,
                "work_quality": 5,
                "reasoning": 5,
                "fabrication_free": 5,
            },
            question=False,
            upstream_fix=True,
        )
    with pytest.raises(ft.JudgeError, match="approach"):
        ft._validate_grade(
            {
                "instruction_compliance": 5,
                "work_quality": 5,
                "reasoning": 5,
                "fabrication_free": 5,
                "solves_problem": True,
                "right_place": True,
                "updated_tests": False,
                "approach": "brilliant",
            },
            question=False,
            upstream_fix=True,
        )


def test_therock_and_toybox_attempts_are_not_mixed_in_one_call():
    toybox = ft.attempt_from("a", _transcript(), "d", CODING)
    rock = ft.attempt_from("tr-8319", _transcript(), "d", TR_TASK, reference="ref")
    with pytest.raises(ValueError, match="own batch"):
        ft.judge_batch([toybox, rock], "m", {})


def test_the_reference_is_fetched_only_when_the_run_is_judged(tmp_path, monkeypatch):
    """The agent has exited by then; the fix is never on disk while it runs."""
    fetched = []
    monkeypatch.setattr(
        therock,
        "reference_diff",
        lambda url, base, merge: fetched.append((url, base, merge)) or "the fix diff",
    )
    monkeypatch.setattr(
        ft,
        "judge_batch",
        lambda attempts, model, env: {
            a.key: {
                "instruction_compliance": 5,
                "work_quality": 5,
                "reasoning": 5,
                "fabrication_free": 5,
                "solves_problem": False,
                "right_place": True,
                "updated_tests": False,
                "approach": "wrong",
                "one_line": "missed the label check",
            }
            for a in attempts
        },
    )
    run_dir = tmp_path / "run"
    (run_dir / "tr-8319").mkdir(parents=True)
    ft.write_scorecard(
        run_dir,
        {
            "suite": "therock",
            "model": "m",
            "therock_url": "https://example.invalid/TheRock",
            "tasks": [
                {
                    "id": "tr-8319",
                    "check": "diff",
                    "passed": None,
                    "why": "changed 1 of 3 reference files; the judge decides",
                    "error": "",
                    "steps": 9,
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "wall_seconds": 1.0,
                }
            ],
        },
    )
    (run_dir / "tr-8319" / "transcript.json").write_text(
        json.dumps({"prompt": "p", "answer": "a", "conversation": []})
    )
    (run_dir / "tr-8319" / "workspace.diff").write_text("+++ b/x.py\n")
    card = ft.judge_run(run_dir, "m", {})
    assert fetched == [
        (
            "https://example.invalid/TheRock",
            TR_TASK.therock["base"],
            TR_TASK.therock["merge"],
        )
    ]
    assert (run_dir / "tr-8319" / "reference.diff").read_text() == "the fix diff"
    entry = card["tasks"][0]
    assert entry["passed"] is False and "missed the label check" in entry["why"]
