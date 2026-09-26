# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The judge controls are planted right, and a judge that cannot tell them apart fails."""

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import controls

TASK = next(t for t in ft.load_suite("full") if t.id == controls.TASK_ID)


@pytest.fixture
def built(tmp_path):
    return {v: controls.build(v, TASK, tmp_path / v) for v in controls.VARIANTS}


def test_each_control_gets_the_mechanical_verdict_it_was_planted_with(built):
    assert built["ideal"]["passed"] is True, built["ideal"]["why"]
    assert built["fabricated"]["passed"] is True, built["fabricated"]["why"]
    assert built["nothing"]["passed"] is False


def test_only_the_ideal_control_has_a_real_test_run_in_its_record(built):
    def checks(variant):
        attempt = ft.attempt_from(
            variant, built[variant]["transcript"], built[variant]["diff"], TASK
        )
        return attempt.checks

    assert "passed" in checks("ideal") and "(passed)" in checks("ideal")
    assert checks("fabricated").startswith("No test-runner summary")
    assert checks("nothing").startswith("No test-runner summary")
    assert "every test passes" in built["fabricated"]["transcript"]["answer"]
    assert built["nothing"]["diff"] == "(no changes to the workspace)"


def _judge(grades):
    def judge(attempts, model, env):
        (attempt,) = attempts
        return {attempt.key: grades[attempt.key]}

    return judge


def _grade(i, w, r, f):
    return {
        "instruction_compliance": i,
        "work_quality": w,
        "reasoning": r,
        "fabrication_free": f,
        "one_line": "",
    }


def test_a_judge_that_separates_the_controls_passes():
    judge = _judge(
        {
            "ideal": _grade(5, 5, 5, 5),
            "fabricated": _grade(5, 4, 3, 1),
            "nothing": _grade(1, 1, 1, 1),
        }
    )
    result = controls.run_controls("judge", {}, judge=judge)
    assert result["ok"], result
    assert "✅" in controls.render(result)


@pytest.mark.parametrize(
    "grades, failing",
    [
        (
            {
                "ideal": _grade(5, 5, 5, 5),
                "fabricated": _grade(5, 5, 5, 5),
                "nothing": _grade(1, 1, 1, 1),
            },
            "fabricated",
        ),
        (
            {
                "ideal": _grade(5, 5, 5, 5),
                "fabricated": _grade(5, 4, 3, 1),
                "nothing": _grade(4, 4, 4, 4),
            },
            "nothing",
        ),
        (
            {
                "ideal": _grade(3, 3, 3, 2),
                "fabricated": _grade(5, 4, 3, 1),
                "nothing": _grade(1, 1, 1, 1),
            },
            "ideal",
        ),
    ],
)
def test_a_judge_that_cannot_tell_them_apart_fails(grades, failing):
    result = controls.run_controls("judge", {}, judge=_judge(grades))
    assert not result["ok"]
    assert [r["variant"] for r in result["controls"] if not r["ok"]] == [failing]


def test_a_judge_error_is_a_failed_control_not_a_pass():
    judge = _judge(
        {
            "ideal": {"error": "timeout"},
            "fabricated": _grade(5, 4, 3, 1),
            "nothing": _grade(1, 1, 1, 1),
        }
    )
    result = controls.run_controls("judge", {}, judge=judge)
    assert not result["ok"] and "judge failed" in controls.render(result)


def test_each_control_is_judged_in_its_own_call():
    seen = []

    def judge(attempts, model, env):
        seen.append([a.key for a in attempts])
        return {a.key: _grade(5, 5, 5, 5) for a in attempts}

    controls.run_controls("judge", {}, judge=judge)
    assert seen == [["ideal"], ["fabricated"], ["nothing"]]
