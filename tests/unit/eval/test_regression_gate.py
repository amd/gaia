# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the noise-aware flagship regression gate (``gaia.eval.regression_gate``).

The gate's contract: a PASS->FAIL is a regression only once a re-run confirms
it; a category fails on K confirmed losses or a pass-rate drop over P points;
the run fails when its judged passes fall below the baseline's; quarantined
scenarios are reported and never gate; and a quarantine entry without a
measured rate and a tracking issue is refused.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from gaia.eval import regression_gate as rg
from gaia.eval.scorecard import build_scorecard

CONFIG = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "gaia"
    / "flagship_regression_gate.json"
)


@pytest.fixture(autouse=True)
def _isolate_job_summary(monkeypatch):
    """Never append fabricated gate findings to the CI job summary."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _card(statuses: dict, category="tool_selection") -> dict:
    results = [
        {
            "scenario_id": sid,
            "status": status,
            "overall_score": 9.0 if status == "PASS" else 3.0,
            "category": category,
            "cost_estimate": {"estimated_usd": 0},
        }
        for sid, status in statuses.items()
    ]
    return build_scorecard("run", results, {})


def _config(**changes) -> rg.GateConfig:
    base = rg.GateConfig(
        confirm_reruns=2,
        max_reruns_per_category=6,
        category_max_confirmed_losses=2,
        category_max_pass_rate_drop_pp=15,
        aggregate_max_lost_passes=0,
        quarantine={},
    )
    return dataclasses.replace(base, **changes)


def _quarantine(sid, rate=0.5, runs=10, issue=5075):
    return {sid: {"scenario": sid, "pass_rate": rate, "runs": runs, "issue": issue}}


def _ten(passing=10, **overrides) -> dict:
    statuses = {f"s{i}": ("PASS" if i < passing else "FAIL") for i in range(10)}
    statuses.update(overrides)
    return statuses


# -- config ------------------------------------------------------------------


def test_committed_config_loads_and_every_quarantine_entry_is_evidenced():
    config = rg.load_config(CONFIG)
    assert config.category_max_confirmed_losses >= 1
    assert config.confirm_reruns >= 1
    assert config.quarantine, "the committed quarantine is generated from real runs"
    for sid, entry in config.quarantine.items():
        assert 0 <= entry["pass_rate"] <= 1, sid
        assert entry["runs"] >= 2, sid
        assert entry["issue"] > 0, sid


def _write_config(tmp_path, quarantine):
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    doc["quarantine"] = quarantine
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"scenario": "x", "runs": 10, "issue": 1}, "no measured 'pass_rate'"),
        ({"scenario": "x", "pass_rate": 0.5, "runs": 10}, "tracking 'issue'"),
        ({"scenario": "x", "pass_rate": 0.5, "issue": 1}, "'runs' >= 2"),
        ({"scenario": "x", "pass_rate": 1.5, "runs": 10, "issue": 1}, "not in [0, 1]"),
        ({"pass_rate": 0.5, "runs": 10, "issue": 1}, "'scenario' id"),
    ],
)
def test_quarantine_entry_without_evidence_is_refused(tmp_path, entry, message):
    with pytest.raises(
        ValueError, match=message.replace("[", r"\[").replace("]", r"\]")
    ):
        rg.load_config(_write_config(tmp_path, [entry]))


def test_duplicate_quarantine_entry_is_refused(tmp_path):
    entry = {"scenario": "x", "pass_rate": 0.5, "runs": 10, "issue": 1}
    with pytest.raises(ValueError, match="quarantined twice"):
        rg.load_config(_write_config(tmp_path, [entry, dict(entry)]))


def test_unreadable_config_fails_the_cli_loudly(tmp_path, capsys):
    code = rg.main(
        ["--config", str(tmp_path / "missing.json"), "gate", "--baseline-dir", "b"]
        + ["--results-dir", "r", "--category", "c"]
    )
    assert code == 1
    assert "::error::cannot read the regression-gate config" in capsys.readouterr().out


# -- confirmed vs unconfirmed flips ------------------------------------------


def test_flip_cleared_by_a_rerun_is_not_a_regression():
    verdict = rg.evaluate_category(
        "c",
        _card(_ten()),
        _card(_ten(s0="FAIL")),
        {"attempts": {"s0": ["FAIL", "PASS"]}},
        _config(),
    )
    assert verdict.confirmed == []
    assert verdict.cleared == [("s0", ["FAIL", "PASS"])]
    assert verdict.current_passed == verdict.baseline_passed == 10
    assert not verdict.failed


def test_flip_that_fails_every_rerun_is_confirmed():
    verdict = rg.evaluate_category(
        "c",
        _card(_ten()),
        _card(_ten(s0="FAIL")),
        {"attempts": {"s0": ["FAIL", "FAIL"]}},
        _config(),
    )
    assert verdict.confirmed == [("s0", ["FAIL", "FAIL"])]
    assert verdict.current_passed == 9


def test_flip_that_was_never_rerun_counts_as_confirmed():
    verdict = rg.evaluate_category(
        "c", _card(_ten()), _card(_ten(s0="FAIL")), {}, _config()
    )
    assert [sid for sid, _ in verdict.confirmed] == ["s0"]


def test_a_timed_out_rerun_neither_clears_nor_confirms_alone():
    verdict = rg.evaluate_category(
        "c",
        _card(_ten()),
        _card(_ten(s0="FAIL")),
        {"attempts": {"s0": ["TIMEOUT", "FAIL"]}},
        _config(),
    )
    assert [sid for sid, _ in verdict.confirmed] == ["s0"]


def test_flip_whose_reruns_all_went_unmeasured_is_unresolved(tmp_path, capsys):
    reruns = {"attempts": {"s0": ["TIMEOUT", "ERRORED"]}}
    verdict = rg.evaluate_category(
        "c", _card(_ten()), _card(_ten(s0="FAIL")), reruns, _config()
    )
    assert verdict.unresolved == [("s0", ["TIMEOUT", "ERRORED"])]
    assert verdict.confirmed == [] and verdict.gated == 9

    base_dir, results = _lane(tmp_path, _ten(), _ten(s0="FAIL"), reruns)
    assert _gate(base_dir, results, "--enforce") == 0
    out = capsys.readouterr().out
    assert "::warning::c/s0 failed, and its re-runs produced no judged result" in out
    assert "unresolved s0  (re-runs: TIMEOUT, ERRORED)" in out


def test_unmeasured_current_scenario_is_left_to_the_integrity_gate():
    verdict = rg.evaluate_category(
        "c", _card(_ten()), _card(_ten(s0="TIMEOUT")), {}, _config()
    )
    assert verdict.gated == 9
    assert verdict.confirmed == [] and not verdict.failed


# -- category thresholds -----------------------------------------------------


def test_category_fails_at_k_confirmed_losses():
    two = _ten(s0="FAIL", s1="FAIL")
    attempts = {"attempts": {"s0": ["FAIL", "FAIL"], "s1": ["FAIL", "FAIL"]}}
    one = rg.evaluate_category(
        "c",
        _card(_ten()),
        _card(_ten(s0="FAIL")),
        {"attempts": {"s0": ["FAIL", "FAIL"]}},
        _config(category_max_pass_rate_drop_pp=100),
    )
    assert not one.failed
    verdict = rg.evaluate_category(
        "c",
        _card(_ten()),
        _card(two),
        attempts,
        _config(category_max_pass_rate_drop_pp=100),
    )
    assert verdict.failed
    assert "2 confirmed regression(s)" in verdict.reasons[0]


def test_category_fails_when_pass_rate_drops_more_than_p_points():
    small = {"a": "PASS", "b": "PASS", "c": "PASS", "d": "PASS"}
    verdict = rg.evaluate_category(
        "c",
        _card(small),
        _card(dict(small, a="FAIL")),
        {"attempts": {"a": ["FAIL", "FAIL"]}},
        _config(),
    )
    assert verdict.drop_pp == pytest.approx(25.0)
    assert verdict.failed
    assert "pass rate fell 25.0 points" in verdict.reasons[0]


def test_an_improvement_offsets_a_loss_in_the_pass_rate():
    base = {"a": "PASS", "b": "PASS", "c": "PASS", "d": "FAIL"}
    cur = {"a": "FAIL", "b": "PASS", "c": "PASS", "d": "PASS"}
    verdict = rg.evaluate_category(
        "c", _card(base), _card(cur), {"attempts": {"a": ["FAIL", "FAIL"]}}, _config()
    )
    assert verdict.improved == ["d"]
    assert verdict.drop_pp == 0
    assert not verdict.failed


# -- quarantine --------------------------------------------------------------


def test_quarantined_flip_is_reported_but_never_gates():
    config = _config(quarantine=_quarantine("s0"), category_max_confirmed_losses=1)
    verdict = rg.evaluate_category(
        "c", _card(_ten()), _card(_ten(s0="FAIL")), {}, config
    )
    assert verdict.quarantined_flips == [("s0", [])]
    assert verdict.gated == 9 and not verdict.failed
    assert rg.flips_to_confirm(_card(_ten()), _card(_ten(s0="FAIL")), config) == []


def test_gate_warns_on_a_quarantined_flip_with_its_rate_and_issue(tmp_path, capsys):
    base_dir, results = _lane(tmp_path, _ten(), _ten(s0="FAIL"))
    config_path = _write_config(
        tmp_path, [{"scenario": "s0", "pass_rate": 0.45, "runs": 11, "issue": 5075}]
    )
    code = rg.main(
        ["--config", str(config_path), "gate", "--baseline-dir", str(base_dir)]
        + ["--results-dir", str(results), "--category", "c", "--enforce"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "::warning::c/s0 failed but is quarantined" in out
    assert "45% over 11 runs, tracked in #5075" in out


# -- gate CLI ----------------------------------------------------------------


def _lane(tmp_path, base, cur, reruns=None, category="c"):
    base_dir = tmp_path / "baselines"
    results = tmp_path / "eval-out"
    base_dir.mkdir(exist_ok=True)
    (results / category).mkdir(parents=True, exist_ok=True)
    (base_dir / f"scorecard_{category}.json").write_text(
        json.dumps(_card(base)), encoding="utf-8"
    )
    (results / category / "scorecard.json").write_text(
        json.dumps(_card(cur)), encoding="utf-8"
    )
    if reruns is not None:
        (results / category / rg.RERUNS_FILE).write_text(
            json.dumps(reruns), encoding="utf-8"
        )
    return base_dir, results


def _gate(base_dir, results, *extra):
    return rg.main(
        ["--config", str(CONFIG), "gate", "--baseline-dir", str(base_dir)]
        + ["--results-dir", str(results), "--category", "c", *extra]
    )


def test_gate_blocks_a_confirmed_regression_under_enforce(tmp_path, capsys):
    base_dir, results = _lane(
        tmp_path,
        _ten(),
        _ten(s0="FAIL", s1="FAIL"),
        {"attempts": {"s0": ["FAIL", "FAIL"], "s1": ["FAIL", "FAIL"]}},
    )
    assert _gate(base_dir, results, "--enforce") == 1
    out = capsys.readouterr().out
    assert "::error::Regression gate: c:" in out
    assert "CONFIRMED  s0  (re-runs: FAIL, FAIL)" in out


def test_gate_reports_without_enforce(tmp_path, capsys):
    base_dir, results = _lane(tmp_path, _ten(), _ten(s0="FAIL", s1="FAIL"))
    assert _gate(base_dir, results) == 0
    assert "::warning::Regression gate: c:" in capsys.readouterr().out


def test_gate_passes_and_shows_flips_cleared_on_rerun(tmp_path, capsys):
    base_dir, results = _lane(
        tmp_path,
        _ten(),
        _ten(s0="FAIL", s1="FAIL"),
        {"attempts": {"s0": ["PASS"], "s1": ["FAIL", "PASS"]}},
    )
    assert _gate(base_dir, results, "--enforce") == 0
    out = capsys.readouterr().out
    assert "cleared    s0  (re-runs: PASS)" in out
    assert "cleared    s1  (re-runs: FAIL, PASS)" in out


def test_gate_fails_a_category_with_no_scorecard_under_enforce(tmp_path, capsys):
    base_dir, results = _lane(tmp_path, _ten(), _ten())
    (results / "c" / "scorecard.json").unlink()
    assert _gate(base_dir, results, "--enforce") == 1
    assert "NOT JUDGED" in capsys.readouterr().out


def test_gate_does_not_fail_a_category_with_no_baseline(tmp_path, capsys):
    base_dir, results = _lane(tmp_path, _ten(), _ten(s0="FAIL", s1="FAIL"))
    (base_dir / "scorecard_c.json").unlink()
    assert _gate(base_dir, results, "--enforce") == 0
    assert "not compared - no baseline yet" in capsys.readouterr().out


# -- aggregate floor ---------------------------------------------------------


def _artifacts(tmp_path, cur_a, cur_b, reruns_a=None):
    lanes = tmp_path / "ci_lanes.json"
    lanes.write_text(
        json.dumps(
            {
                "lanes": [
                    {"lane": "one", "categories": ["a"]},
                    {"lane": "two", "categories": ["b"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    base_dir = tmp_path / "baselines"
    base_dir.mkdir()
    for category, lane, cur, reruns in (
        ("a", "one", cur_a, reruns_a),
        ("b", "two", cur_b, None),
    ):
        (base_dir / f"scorecard_{category}.json").write_text(
            json.dumps(_card(_ten(passing=5))), encoding="utf-8"
        )
        root = tmp_path / "artifacts" / f"eval-{lane}" / category
        root.mkdir(parents=True)
        (root / "scorecard.json").write_text(json.dumps(_card(cur)), encoding="utf-8")
        if reruns is not None:
            (root / rg.RERUNS_FILE).write_text(json.dumps(reruns), encoding="utf-8")
    return lanes, base_dir, tmp_path / "artifacts"


def _aggregate(lanes, base_dir, artifacts, *extra):
    return rg.main(
        ["--config", str(CONFIG), "aggregate", "--lanes-file", str(lanes)]
        + ["--baseline-dir", str(base_dir), "--artifacts-dir", str(artifacts), *extra]
    )


def test_aggregate_fails_below_the_baselines_judged_passes(tmp_path, capsys):
    # One confirmed loss in each lane: no category trips, the run total does.
    cur = _ten(passing=5, s0="FAIL")
    lanes, base_dir, artifacts = _artifacts(tmp_path, cur, cur)
    assert _aggregate(lanes, base_dir, artifacts, "--enforce") == 1
    out = capsys.readouterr().out
    assert "**8** now, **10** in the baseline" in out
    assert "::error::Regression gate: the run passes 8 of 20" in out
    assert _aggregate(lanes, base_dir, artifacts) == 0


def test_aggregate_passes_at_the_floor_and_counts_rerun_passes(tmp_path):
    cur = _ten(passing=5, s0="FAIL")
    lanes, base_dir, artifacts = _artifacts(
        tmp_path, cur, _ten(passing=5), reruns_a={"attempts": {"s0": ["PASS"]}}
    )
    assert _aggregate(lanes, base_dir, artifacts, "--enforce") == 0


# -- confirm -----------------------------------------------------------------


def _fake_rerun(tmp_path, outcomes):
    """A stand-in for `gaia eval agent --scenario`, scripted per scenario."""
    calls = []

    def rerun_once(sid, log):
        calls.append(sid)
        status = outcomes[sid].pop(0)
        run_dir = tmp_path / "runs" / f"{sid}-{len(calls)}"
        run_dir.mkdir(parents=True)
        (run_dir / "scorecard.json").write_text(
            json.dumps(_card({sid: status})), encoding="utf-8"
        )
        Path(log).write_text(f"Output: {run_dir}\n", encoding="utf-8")
        return 0, run_dir

    return rerun_once, calls


def test_confirm_stops_at_the_first_pass_and_records_every_attempt(tmp_path):
    rerun_once, calls = _fake_rerun(
        tmp_path, {"s0": ["FAIL", "PASS"], "s1": ["FAIL", "FAIL"], "s2": ["PASS"]}
    )
    out_dir = tmp_path / "eval-out" / "c" / "reruns"
    record = rg.confirm_category(
        "c",
        _card(_ten()),
        _card(_ten(s0="FAIL", s1="FAIL", s2="FAIL")),
        _config(),
        2,
        out_dir,
        rerun_once,
    )
    assert record["attempts"] == {
        "s0": ["FAIL", "PASS"],
        "s1": ["FAIL", "FAIL"],
        "s2": ["PASS"],
    }
    assert calls == ["s0", "s0", "s1", "s1", "s2"]
    assert (out_dir / "s0.2.json").exists() and (out_dir / "s0.2.log").exists()
    saved = json.loads((out_dir.parent / rg.RERUNS_FILE).read_text(encoding="utf-8"))
    assert saved["attempts"] == record["attempts"]


def test_confirm_skips_quarantined_scenarios_and_respects_the_budget(tmp_path):
    rerun_once, calls = _fake_rerun(tmp_path, {"s1": ["FAIL", "FAIL"]})
    record = rg.confirm_category(
        "c",
        _card(_ten()),
        _card(_ten(s0="FAIL", s1="FAIL", s2="FAIL")),
        _config(quarantine=_quarantine("s0"), max_reruns_per_category=1),
        2,
        tmp_path / "reruns",
        rerun_once,
    )
    assert calls == ["s1", "s1"]
    assert record["flips"] == ["s1", "s2"]
    assert record["not_rerun"] == ["s2"]


def test_confirm_raises_when_a_rerun_crashes(tmp_path):
    def crashed(sid, log):
        return 1, None

    with pytest.raises(RuntimeError, match="harness failure, not a FAIL"):
        rg.confirm_category(
            "c",
            _card(_ten()),
            _card(_ten(s0="FAIL")),
            _config(),
            1,
            tmp_path / "reruns",
            crashed,
        )


def test_run_dir_is_parsed_from_the_last_output_line(tmp_path):
    log = tmp_path / "c.log"
    log.write_text(
        "\ufeffnoise\nOutput: C:\\first\\run\nmore\nOutput: C:\\second\\run  \n",
        encoding="utf-8",
    )
    assert rg.run_dir_from_log(log) == Path("C:\\second\\run")
    assert rg.run_dir_from_log(tmp_path / "absent.log") is None
