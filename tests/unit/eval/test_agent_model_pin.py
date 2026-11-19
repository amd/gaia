# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A scorecard names the local model it measured, and --compare holds it to that.

``compare_scorecards`` keys on scenario id alone, and every model answers the
same scenario ids, so without these checks a Gemma run diffed against a Qwen
baseline (or a run driven by a different Claude model) prints a clean list of
regressions and improvements that measure the model swap (#5505).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.eval.runner import (
    ScorecardMismatchError,
    _stamp_agent_model,
    baseline_identity,
    compare_scorecards,
    run_agent_model,
)
from gaia.eval.scorecard import build_scorecard

REPO_ROOT = Path(__file__).resolve().parents[3]
GEMMA = "Gemma-4-E4B-it-GGUF"
QWEN = "Qwen3.6-35B-A3B-GGUF"
JUDGE = "claude-sonnet-4-6"


def _result(status="PASS", session_id="sess-1", **extra):
    return {
        "scenario_id": "smart_discovery",
        "status": status,
        "overall_score": 8.0,
        "turns": [],
        "session_id": session_id,
        **extra,
    }


def _stamp(result, expected, observed):
    """Stamp with the session read-back stubbed; an exception means unreachable."""

    def fake_read(_url, _sid):
        if isinstance(observed, Exception):
            raise observed
        return observed

    _stamp_agent_model(
        result, {"id": "s"}, expected, "http://x", read_session_model=fake_read
    )
    return result


# ---------------------------------------------------------------------------
# Per scenario: the session says which model answered
# ---------------------------------------------------------------------------


def test_a_pinned_scenario_on_the_pinned_model_keeps_its_score():
    result = _stamp(_result(), GEMMA, GEMMA)

    assert result["status"] == "PASS"
    assert result["agent_model_requested"] == GEMMA
    assert result["agent_model_observed"] == GEMMA


def test_a_pinned_scenario_on_another_model_is_discarded():
    result = _stamp(_result(), GEMMA, QWEN)

    assert result["status"] == "INFRA_ERROR"
    assert QWEN in result["error"] and GEMMA in result["error"]
    assert result["agent_model_observed"] == QWEN


def test_a_failing_scenario_on_another_model_is_discarded_too():
    # A FAIL measured on the wrong model is no more evidence than a PASS.
    assert _stamp(_result("FAIL"), GEMMA, QWEN)["status"] == "INFRA_ERROR"


def test_the_model_name_must_match_exactly():
    # A near-miss id is a different registration in Lemonade, not a typo to forgive.
    assert _stamp(_result(), GEMMA, GEMMA.lower())["status"] == "INFRA_ERROR"


def test_a_pinned_scenario_that_cannot_be_read_back_loses_its_score():
    result = _stamp(_result(), GEMMA, ConnectionError("refused"))

    assert result["status"] == "INFRA_ERROR"
    assert "cannot be verified" in result["error"]


def test_a_pinned_scenario_without_a_session_id_loses_its_score():
    result = _stamp(_result(session_id=None), GEMMA, GEMMA)

    assert result["status"] == "INFRA_ERROR"


def test_an_unverifiable_timeout_keeps_its_status_and_gains_a_warning():
    result = _stamp(_result("TIMEOUT", session_id=None), GEMMA, GEMMA)

    assert result["status"] == "TIMEOUT"


def test_without_a_pin_the_model_is_only_recorded():
    result = _stamp(_result(), None, QWEN)

    assert result["status"] == "PASS"
    assert result["agent_model_observed"] == QWEN


def test_without_a_pin_an_unreadable_session_is_recorded_as_unknown():
    result = _stamp(_result(), None, ConnectionError("refused"))

    assert result["status"] == "PASS"
    assert result["agent_model_observed"] is None


# ---------------------------------------------------------------------------
# Per run: what the scorecard may claim
# ---------------------------------------------------------------------------


def test_the_pin_is_the_run_model():
    assert run_agent_model([_result(agent_model_observed=GEMMA)], GEMMA) == GEMMA


def test_an_unpinned_run_on_one_model_names_it():
    results = [_result(agent_model_observed=GEMMA) for _ in range(3)]
    assert run_agent_model(results) == GEMMA


def test_an_unpinned_run_on_two_models_names_none():
    results = [_result(agent_model_observed=GEMMA), _result(agent_model_observed=QWEN)]
    assert run_agent_model(results) is None


def test_one_unobserved_scenario_makes_the_run_unattributable():
    results = [_result(agent_model_observed=GEMMA), _result(agent_model_observed=None)]
    assert run_agent_model(results) is None


def test_unmeasured_and_skipped_scenarios_do_not_count():
    results = [
        _result(agent_model_observed=GEMMA),
        _result("INFRA_ERROR", agent_model_observed=QWEN),
        {"scenario_id": "x", "status": "SKIPPED_NO_DOCUMENT"},
    ]
    assert run_agent_model(results) == GEMMA


def test_a_run_that_measured_nothing_names_no_model():
    assert run_agent_model([_result("INFRA_ERROR")]) is None


# ---------------------------------------------------------------------------
# --compare refuses a baseline measured with another model
# ---------------------------------------------------------------------------


def _scorecard(path, agent_model=GEMMA, judge=JUDGE, status="PASS"):
    config = {"model": judge}
    if agent_model is not None:
        config["agent_model"] = agent_model
    results = [
        {
            "scenario_id": "a",
            "status": status,
            "overall_score": 8.0,
            "category": "tool_selection",
            "cost_estimate": {"estimated_usd": 0},
        }
    ]
    path.write_text(json.dumps(build_scorecard("run", results, config)))
    return path


def _baseline_dir(tmp_path, model=GEMMA, eval_model=JUDGE, scorecard_model=None):
    base = tmp_path / "baseline"
    base.mkdir()
    (base / "meta.json").write_text(
        json.dumps({"model": model, "eval_model": eval_model})
    )
    return _scorecard(
        base / "scorecard_tool_selection.json", agent_model=scorecard_model
    )


def test_a_matching_run_is_compared(tmp_path):
    baseline = _baseline_dir(tmp_path)
    current = _scorecard(tmp_path / "current.json")

    assert compare_scorecards(baseline, current)["regressed"] == []


def test_a_run_on_another_agent_model_is_refused(tmp_path):
    baseline = _baseline_dir(tmp_path, model=QWEN)
    current = _scorecard(tmp_path / "current.json", agent_model=GEMMA)

    with pytest.raises(ScorecardMismatchError, match="agent model is 'Qwen"):
        compare_scorecards(baseline, current)


def test_a_run_under_another_eval_model_is_refused(tmp_path):
    baseline = _baseline_dir(tmp_path)
    current = _scorecard(tmp_path / "current.json", judge="claude-opus-5")

    with pytest.raises(ScorecardMismatchError, match="eval model"):
        compare_scorecards(baseline, current)


def test_both_mismatches_are_named_at_once(tmp_path):
    baseline = _baseline_dir(tmp_path)
    current = _scorecard(tmp_path / "current.json", agent_model=QWEN, judge="other")

    with pytest.raises(ScorecardMismatchError) as err:
        compare_scorecards(baseline, current)
    assert "agent model" in str(err.value) and "eval model" in str(err.value)


def test_a_run_that_does_not_say_which_model_it_ran_is_refused(tmp_path):
    # An old or mixed-model scorecard cannot be held to the baseline's model,
    # so it must not be read as if it had been.
    baseline = _baseline_dir(tmp_path)
    current = _scorecard(tmp_path / "current.json", agent_model=None)

    with pytest.raises(ScorecardMismatchError, match="does not record"):
        compare_scorecards(baseline, current)


def test_meta_outranks_the_baseline_scorecards_own_config(tmp_path):
    # The committed scorecards predate config.agent_model; a planted value in
    # one of them must not override what meta.json says the baseline is.
    baseline = _baseline_dir(tmp_path, model=GEMMA, scorecard_model=QWEN)
    current = _scorecard(tmp_path / "current.json", agent_model=QWEN)

    with pytest.raises(ScorecardMismatchError):
        compare_scorecards(baseline, current)


def test_loose_scorecards_are_checked_against_each_other(tmp_path):
    baseline = _scorecard(tmp_path / "base.json", agent_model=QWEN)
    current = _scorecard(tmp_path / "current.json", agent_model=GEMMA)

    with pytest.raises(ScorecardMismatchError):
        compare_scorecards(baseline, current)


def test_a_judge_mismatch_between_loose_scorecards_is_refused(tmp_path):
    # This used to be a printed warning above a full diff.
    baseline = _scorecard(tmp_path / "base.json", judge="claude-sonnet-4-6")
    current = _scorecard(tmp_path / "current.json", judge="claude-opus-5")

    with pytest.raises(ScorecardMismatchError, match="eval model"):
        compare_scorecards(baseline, current)


def test_a_baseline_that_records_no_agent_model_checks_only_the_judge(tmp_path):
    baseline = _scorecard(tmp_path / "base.json", agent_model=None)
    current = _scorecard(tmp_path / "current.json", agent_model=QWEN)

    assert compare_scorecards(baseline, current)["regressed"] == []


def test_an_unrelated_meta_json_is_not_read_as_a_baseline_meta(tmp_path):
    base = tmp_path / "run"
    base.mkdir()
    (base / "meta.json").write_text(json.dumps({"something": "else"}))
    baseline = _scorecard(base / "scorecard.json", agent_model=GEMMA)

    assert baseline_identity(baseline, json.loads(baseline.read_text()))[
        "source"
    ] == str(baseline)


def test_a_corrupt_meta_json_is_an_error_not_a_skipped_check(tmp_path):
    base = tmp_path / "baseline"
    base.mkdir()
    (base / "meta.json").write_text("{not json")
    baseline = _scorecard(base / "scorecard_tool_selection.json")
    current = _scorecard(tmp_path / "current.json")

    with pytest.raises(ScorecardMismatchError, match="not valid JSON"):
        compare_scorecards(baseline, current)


def test_the_committed_halo_baseline_refuses_a_qwen_run(tmp_path):
    baseline = (
        REPO_ROOT
        / "tests/fixtures/eval_baselines/gaia-flagship/scorecard_tool_selection.json"
    )
    meta = json.loads((baseline.parent / "meta.json").read_text(encoding="utf-8"))
    qwen = _scorecard(
        tmp_path / "qwen.json", agent_model=QWEN, judge=meta["eval_model"]
    )
    gemma = _scorecard(
        tmp_path / "gemma.json", agent_model=meta["model"], judge=meta["eval_model"]
    )

    with pytest.raises(ScorecardMismatchError):
        compare_scorecards(baseline, qwen)
    compare_scorecards(baseline, gemma)


@pytest.mark.parametrize(
    "agent_model, expected",
    [(GEMMA, 0), (QWEN, 3)],
)
def test_the_cli_exits_3_for_a_scorecard_from_another_model(
    tmp_path, agent_model, expected
):
    baseline = _baseline_dir(tmp_path)
    current = _scorecard(tmp_path / "current.json", agent_model=agent_model)
    env = dict(os.environ, USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gaia.cli",
            "eval",
            "agent",
            "--compare",
            str(baseline),
            str(current),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if expected == 3:
        assert "Not comparable" in result.stdout
