# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Targeted eval runs: several ``--scenario`` ids, and everything downstream.

A targeted run must never pass for a full one. So these tests pin four things:
the CLI refuses any requested id that would not run, the scorecard records the
selection, ``--compare`` and the integrity gate judge only the selected
scenarios and name the rest as not run, and a run whose Claude quota ran out
(scenarios ERRORED with zero tokens, as on v0.25.1-rc2) is caught.
"""

import json

import pytest
import yaml

from gaia.eval import runner
from gaia.eval.integrity_gate import check_category, main, zero_token_errors
from gaia.eval.lane_summary import main as summary_main
from gaia.eval.runner import (
    AgentEvalRunner,
    _driver_usage,
    compare_scorecards,
    find_scenarios,
    parse_scenario_ids,
    unmatched_scenario_errors,
)
from gaia.eval.scorecard import build_scorecard

# What rc2's `claude -p` printed once the quota was gone, cut at the runner's
# 500-character limit exactly as the scorecards kept it.
RC2_QUOTA_STDOUT = (
    '{"duration_api_ms":0,"stop_reason":"stop_sequence","session_id":"0dcf6c6b",'
    '"total_cost_usd":0,"usage":{"output_tokens_details":{"thinking_tokens":0},'
    '"input_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,'
    '"output_tokens":0,"server_tool_use":{"web_search_requests":0},'
    '"service_tier":"standard"}}'
)


@pytest.fixture(autouse=True)
def _isolate_job_summary(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _result(sid, status="PASS", score=9.0, category="adversarial", **extra):
    return {
        "scenario_id": sid,
        "status": status,
        "overall_score": score,
        "category": category,
        "cost_estimate": {"estimated_usd": 0},
        **extra,
    }


@pytest.fixture
def scenario_dir(tmp_path, monkeypatch):
    """Three scenarios in two categories, one carrying a `live` tag."""
    monkeypatch.setattr(runner, "SCENARIOS_DIR", tmp_path)
    monkeypatch.setattr(runner, "USER_SCENARIOS_DIR", tmp_path / "absent")
    for sid, category, tags in (
        ("alpha", "cat_a", []),
        ("beta", "cat_a", ["live"]),
        ("gamma", "cat_b", []),
    ):
        data = {
            "id": sid,
            "category": category,
            "persona": "casual_user",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "X", "ground_truth": {"answer": "A"}}],
        }
        if tags:
            data["tags"] = tags
        (tmp_path / f"{sid}.yaml").write_text(yaml.dump(data), encoding="utf-8")
    return tmp_path


# --- CLI parsing -------------------------------------------------------------


def test_scenario_flag_is_repeatable_and_comma_separated():
    from gaia.cli import build_parser

    args = build_parser().parse_args(
        ["eval", "agent", "--scenario", "a,b", "--scenario", " c , a "]
    )
    assert parse_scenario_ids(args.scenario) == ["a", "b", "c"]


def test_no_scenario_flag_means_no_filter():
    from gaia.cli import build_parser

    args = build_parser().parse_args(["eval", "agent", "--category", "x"])
    assert parse_scenario_ids(args.scenario) is None


@pytest.mark.parametrize("value", [None, [], "", [" , ,"]])
def test_empty_values_are_no_filter_not_an_empty_selection(value):
    assert parse_scenario_ids(value) is None


def test_a_single_string_still_works():
    assert parse_scenario_ids("alpha") == ["alpha"]


# --- selection ---------------------------------------------------------------


def test_find_scenarios_takes_several_ids(scenario_dir):
    got = find_scenarios(scenario_id=["gamma", "alpha"])
    assert [data["id"] for _p, data in got] == ["alpha", "gamma"]


def test_every_requested_id_that_runs_reports_nothing(scenario_dir):
    matched = find_scenarios(scenario_id=["alpha"], category="cat_a")
    assert unmatched_scenario_errors(["alpha"], matched, category="cat_a") == []


def test_unknown_id_is_named_and_valid_ids_listed(scenario_dir):
    matched = find_scenarios(scenario_id=["alpha", "nope"], category="cat_a")
    errors = unmatched_scenario_errors(["alpha", "nope"], matched, category="cat_a")
    assert errors[0] == "'nope' is not a scenario id"
    assert errors[-1] == "Valid scenario ids in category 'cat_a': alpha, beta"


def test_id_in_another_category_is_refused_not_skipped(scenario_dir):
    matched = find_scenarios(scenario_id=["gamma"], category="cat_a")
    errors = unmatched_scenario_errors(["gamma"], matched, category="cat_a")
    assert "'gamma' is in category 'cat_b', not 'cat_a'" in errors


def test_id_dropped_by_an_excluded_tag_is_refused(scenario_dir):
    matched = find_scenarios(
        scenario_id=["alpha", "beta"], category="cat_a", exclude_tags=["live"]
    )
    errors = unmatched_scenario_errors(
        ["alpha", "beta"], matched, category="cat_a", exclude_tags=["live"]
    )
    assert "'beta' carries the excluded tag(s) ['live']" in errors


def _stub_the_run(monkeypatch):
    monkeypatch.setattr(runner, "preflight_check", lambda *a, **k: [])
    monkeypatch.setattr(runner, "_documents_exist", lambda _sd: True)
    monkeypatch.setattr(runner, "_missing_requirements", lambda _sd, _m: [])
    monkeypatch.setattr(
        runner,
        "run_scripted_scenario",
        lambda _path, data, *a, **k: _result(data["id"], category=data["category"]),
    )


def test_a_partly_unknown_selection_exits_before_running_anything(
    scenario_dir, tmp_path, monkeypatch, capsys
):
    _stub_the_run(monkeypatch)
    ran = []
    monkeypatch.setattr(
        runner, "run_scripted_scenario", lambda *a, **k: ran.append(a) or {}
    )
    eval_runner = AgentEvalRunner(results_dir=tmp_path / "results")
    with pytest.raises(SystemExit) as exc:
        eval_runner._run_locked(scenario_id=["alpha", "nope"], category="cat_a")
    assert exc.value.code == 1
    assert not ran
    err = capsys.readouterr().err
    assert "Not every requested --scenario would run" in err
    assert "'nope' is not a scenario id" in err


def test_a_targeted_scorecard_records_its_selection(
    scenario_dir, tmp_path, monkeypatch
):
    _stub_the_run(monkeypatch)
    eval_runner = AgentEvalRunner(results_dir=tmp_path / "results")
    card = eval_runner._run_locked(scenario_id=["alpha"], category="cat_a")
    assert card["config"]["scenario_filter"] == ["alpha"]
    assert [s["scenario_id"] for s in card["scenarios"]] == ["alpha"]


def test_a_full_scorecard_has_no_selection_key(scenario_dir, tmp_path, monkeypatch):
    _stub_the_run(monkeypatch)
    eval_runner = AgentEvalRunner(results_dir=tmp_path / "results")
    card = eval_runner._run_locked(category="cat_a")
    assert "scenario_filter" not in card["config"]


# --- driver usage ------------------------------------------------------------


def test_driver_usage_is_read_from_a_failed_claude_run():
    usage = _driver_usage(RC2_QUOTA_STDOUT)
    assert usage == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "duration_api_ms": 0,
    }


@pytest.mark.parametrize("stdout", ["", "not json", "[1]", '{"result": "x"}', None])
def test_driver_usage_is_none_without_a_usage_block(stdout):
    assert _driver_usage(stdout) is None


# --- compare -----------------------------------------------------------------


def _write(path, results, config=None):
    path.write_text(json.dumps(build_scorecard("run", results, config or {})))
    return path


def test_compare_judges_only_the_selected_scenarios(tmp_path, capsys):
    base = _write(
        tmp_path / "base.json",
        [_result("a"), _result("b"), _result("c", "FAIL", 2.0)],
    )
    cur = _write(
        tmp_path / "cur.json",
        [_result("a", "FAIL", 3.0)],
        {"scenario_filter": ["a"]},
    )
    result = compare_scorecards(base, cur)
    out = capsys.readouterr().out

    assert [e["scenario_id"] for e in result["regressed"]] == ["a"]
    assert result["only_in_baseline"] == []
    assert result["not_run"] == ["b", "c"]
    assert "TARGETED : 1 selected scenario(s)" in out
    assert "NOT RUN (2 scenario(s))" in out
    assert "removed or renamed" not in out
    # The baseline row covers the selected scenario only: 1 of 1 passed.
    assert "Pass rate (all)                      100%         0%" in out


def test_compare_of_a_full_run_still_reports_a_vanished_scenario(tmp_path):
    base = _write(tmp_path / "base.json", [_result("a"), _result("b")])
    cur = _write(tmp_path / "cur.json", [_result("a")])
    result = compare_scorecards(base, cur)
    assert result["only_in_baseline"] == ["b"]
    assert result["not_run"] == []


# --- integrity gate ----------------------------------------------------------


def test_gate_expects_only_the_selected_scenarios(tmp_path):
    base = _write(tmp_path / "base.json", [_result("a"), _result("b")])
    cur = _write(tmp_path / "cur.json", [_result("a")], {"scenario_filter": ["a"]})
    problems, status = check_category(base, cur, "adversarial")
    assert problems == []
    assert "targeted run of 1 selected, 1 other baseline scenario(s) not run" in status


def test_gate_still_catches_a_selected_scenario_that_vanished(tmp_path):
    base = _write(tmp_path / "base.json", [_result("a"), _result("b")])
    cur = _write(tmp_path / "cur.json", [_result("a")], {"scenario_filter": ["a", "b"]})
    problems, _status = check_category(base, cur, "adversarial")
    assert problems == ["adversarial: 1 selected scenario(s) missing from the run: b"]


def test_zero_token_errors_from_recorded_usage_and_from_rc2_text():
    card = build_scorecard(
        "run",
        [
            _result(
                "quota_new",
                "ERRORED",
                None,
                driver_usage=_driver_usage(RC2_QUOTA_STDOUT),
            ),
            _result(
                "quota_rc2", "ERRORED", None, error="stdout: " + RC2_QUOTA_STDOUT[:480]
            ),
            _result(
                "real_error",
                "ERRORED",
                None,
                driver_usage={"input_tokens": 40000, "output_tokens": 12},
            ),
            _result(
                "reached_claude",
                "ERRORED",
                None,
                driver_usage={
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "duration_api_ms": 812,
                },
            ),
            _result("crash", "ERRORED", None, error="Traceback: boom"),
            _result("fine", "PASS"),
        ],
        {},
    )
    assert zero_token_errors(card) == ["quota_new", "quota_rc2"]


def _gate_dirs(tmp_path, results):
    base_dir, out_dir = tmp_path / "b", tmp_path / "o"
    (out_dir / "adversarial").mkdir(parents=True)
    base_dir.mkdir()
    _write(base_dir / "scorecard_adversarial.json", [_result("a"), _result("b")])
    _write(out_dir / "adversarial" / "scorecard.json", results)
    return [
        "--baseline-dir",
        str(base_dir),
        "--results-dir",
        str(out_dir),
        "--category",
        "adversarial",
    ]


def test_quota_exhaustion_fails_the_gate_even_in_report_mode(tmp_path, capsys):
    argv = _gate_dirs(
        tmp_path,
        [
            _result("a"),
            _result("b", "ERRORED", None, error="stdout: " + RC2_QUOTA_STDOUT),
        ],
    )
    assert main(argv + ["--fail-on-zero-token-errors"]) == 1
    out = capsys.readouterr().out
    assert (
        "::error::Integrity: 1 scenario(s) ERRORED before Claude spent a single token: b"
        in out
    )


def test_without_the_flag_quota_exhaustion_only_reports(tmp_path):
    argv = _gate_dirs(
        tmp_path,
        [
            _result("a"),
            _result("b", "ERRORED", None, error="stdout: " + RC2_QUOTA_STDOUT),
        ],
    )
    # A tag run: report mode, exit code unchanged from before the flag existed.
    assert main(argv) == 0


def test_an_ordinary_error_is_not_mistaken_for_quota(tmp_path):
    argv = _gate_dirs(
        tmp_path,
        [_result("a"), _result("b", "ERRORED", None, error="Traceback: boom")],
    )
    assert main(argv + ["--fail-on-zero-token-errors"]) == 0


# --- run summary -------------------------------------------------------------


def test_summary_of_a_targeted_run_covers_only_its_lanes(tmp_path, capsys):
    lanes_file = tmp_path / "lanes.json"
    lanes_file.write_text(
        json.dumps(
            {
                "lanes": [
                    {
                        "lane": "conversation",
                        "categories": ["adversarial", "personality"],
                    },
                    {"lane": "memory", "categories": ["memory"]},
                ]
            }
        )
    )
    artifacts = tmp_path / "artifacts"
    (artifacts / "eval-conversation" / "adversarial").mkdir(parents=True)
    _write(
        artifacts / "eval-conversation" / "adversarial" / "scorecard.json",
        [_result("a")],
        {"scenario_filter": ["a"]},
    )
    matrix = json.dumps(
        {"include": [{"lane": "conversation", "categories": "adversarial"}]}
    )
    selection = "TARGETED run: 1 scenario(s) in 1 category(ies)"
    assert (
        summary_main(
            [
                "--lanes-file",
                str(lanes_file),
                "--artifacts-dir",
                str(artifacts),
                "--matrix-json",
                matrix,
                "--selection",
                selection,
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"> **{selection}.** This is not a full sweep" in out
    assert "`memory`" not in out
    assert "personality" not in out
    assert "uploaded no artifact" not in out


def test_summary_reads_a_lone_artifact_unpacked_without_its_folder(tmp_path, capsys):
    # download-artifact puts a single matching artifact's files straight into
    # the target dir; seen on the first one-lane targeted run.
    lanes_file = tmp_path / "lanes.json"
    lanes_file.write_text(
        json.dumps({"lanes": [{"lane": "conversation", "categories": ["adversarial"]}]})
    )
    artifacts = tmp_path / "artifacts"
    (artifacts / "adversarial").mkdir(parents=True)
    (artifacts / "lane-status.json").write_text(
        json.dumps({"lane": "conversation", "job_status": "success"})
    )
    _write(artifacts / "adversarial" / "scorecard.json", [_result("a")])
    argv = ["--lanes-file", str(lanes_file), "--artifacts-dir", str(artifacts)]
    assert summary_main(argv) == 0
    out = capsys.readouterr().out
    assert "| `conversation` | adversarial | 1 | 1 | 0 | 0 | — | measured |" in out
    assert "uploaded no artifact" not in out


def test_a_flat_artifact_from_another_lane_is_not_borrowed(tmp_path, capsys):
    lanes_file = tmp_path / "lanes.json"
    lanes_file.write_text(
        json.dumps({"lanes": [{"lane": "memory", "categories": ["memory"]}]})
    )
    (tmp_path / "lane-status.json").write_text(json.dumps({"lane": "conversation"}))
    argv = ["--lanes-file", str(lanes_file), "--artifacts-dir", str(tmp_path)]
    assert summary_main(argv) == 0
    assert "`memory`** uploaded no artifact" in capsys.readouterr().out


def test_summary_rejects_a_malformed_matrix(tmp_path):
    lanes_file = tmp_path / "lanes.json"
    lanes_file.write_text(json.dumps({"lanes": []}))
    argv = ["--lanes-file", str(lanes_file), "--artifacts-dir", str(tmp_path)]
    assert summary_main(argv + ["--matrix-json", "{}"]) == 1
