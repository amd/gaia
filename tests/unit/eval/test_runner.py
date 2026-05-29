# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.eval.runner``.

Tests cover:
  - validate_scenario (schema validation)
  - recompute_turn_score (weighted scoring)
  - _validate_turn_scores (dimension completeness)
  - _aggregate_performance (per-turn → scenario rollup)
  - _compute_effective_timeout (per-scenario timeout scaling)
  - find_scenarios (filtering by id/category/tags, with mocked YAML)
  - build_scenario_prompt (prompt assembly)
  - compare_scorecards (regression detection)
  - AgentEvalRunner.__init__ (configuration)

All file/network/subprocess calls are mocked — no real LLM or Agent UI needed.
"""

import json
from pathlib import Path

import pytest

from gaia.eval.runner import (
    _SCORE_WEIGHTS,
    _aggregate_performance,
    _compute_effective_timeout,
    _validate_turn_scores,
    compare_scorecards,
    recompute_turn_score,
    validate_scenario,
)

# ---------------------------------------------------------------------------
# validate_scenario
# ---------------------------------------------------------------------------


class TestValidateScenario:
    def _valid_scenario(self, **overrides):
        base = {
            "id": "test_scenario",
            "category": "general",
            "persona": "casual_user",
            "setup": {"index_documents": []},
            "turns": [
                {
                    "turn": 1,
                    "objective": "Ask about X",
                    "ground_truth": {"answer": "Y"},
                }
            ],
        }
        base.update(overrides)
        return base

    def test_valid_scenario_passes(self, tmp_path):
        data = self._valid_scenario()
        validate_scenario(tmp_path / "test.yaml", data)  # should not raise

    def test_missing_required_field(self, tmp_path):
        data = self._valid_scenario()
        del data["id"]
        with pytest.raises(ValueError, match="missing top-level field 'id'"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_missing_setup_index_documents(self, tmp_path):
        data = self._valid_scenario(setup={})
        with pytest.raises(ValueError, match="setup.index_documents is missing"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_empty_turns(self, tmp_path):
        data = self._valid_scenario(turns=[])
        with pytest.raises(ValueError, match="turns list is empty"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_duplicate_turn_numbers(self, tmp_path):
        data = self._valid_scenario(
            turns=[
                {
                    "turn": 1,
                    "objective": "X",
                    "ground_truth": {"answer": "A"},
                },
                {
                    "turn": 1,
                    "objective": "Y",
                    "ground_truth": {"answer": "B"},
                },
            ]
        )
        with pytest.raises(ValueError, match="duplicate turn number"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_non_sequential_turns(self, tmp_path):
        data = self._valid_scenario(
            turns=[
                {
                    "turn": 1,
                    "objective": "X",
                    "ground_truth": {"answer": "A"},
                },
                {
                    "turn": 3,
                    "objective": "Y",
                    "ground_truth": {"answer": "B"},
                },
            ]
        )
        with pytest.raises(ValueError, match="sequential"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_turn_without_objective(self, tmp_path):
        data = self._valid_scenario(
            turns=[{"turn": 1, "ground_truth": {"answer": "A"}}]
        )
        with pytest.raises(ValueError, match="missing 'objective'"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_turn_without_ground_truth_or_criteria(self, tmp_path):
        data = self._valid_scenario(turns=[{"turn": 1, "objective": "X"}])
        with pytest.raises(ValueError, match="ground_truth.*success_criteria"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_success_criteria_as_string_is_valid(self, tmp_path):
        data = self._valid_scenario(
            turns=[
                {"turn": 1, "objective": "X", "success_criteria": "Agent says hello"}
            ]
        )
        validate_scenario(tmp_path / "test.yaml", data)  # should not raise

    def test_success_criteria_as_dict_rejected(self, tmp_path):
        data = self._valid_scenario(
            turns=[
                {
                    "turn": 1,
                    "objective": "X",
                    "success_criteria": {"key": "val"},
                }
            ]
        )
        with pytest.raises(ValueError, match="success_criteria must be a string"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_persona_non_string_rejected(self, tmp_path):
        data = self._valid_scenario(persona=42)
        with pytest.raises(ValueError, match="persona must be a string"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_persona_empty_string_rejected(self, tmp_path):
        data = self._valid_scenario(persona="  ")
        with pytest.raises(ValueError, match="persona must be a non-empty string"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_custom_persona_accepted(self, tmp_path):
        data = self._valid_scenario(persona="my_custom_persona")
        validate_scenario(tmp_path / "test.yaml", data)  # should not raise

    def test_missing_path_in_index_documents(self, tmp_path):
        data = self._valid_scenario(setup={"index_documents": [{"title": "doc1"}]})
        with pytest.raises(ValueError, match="missing 'path' field"):
            validate_scenario(tmp_path / "test.yaml", data)


# ---------------------------------------------------------------------------
# recompute_turn_score
# ---------------------------------------------------------------------------


class TestRecomputeTurnScore:
    def _full_scores(self, **overrides):
        scores = {k: 8.0 for k in _SCORE_WEIGHTS}
        scores.update(overrides)
        return scores

    def test_uniform_scores(self):
        scores = {k: 8.0 for k in _SCORE_WEIGHTS}
        assert recompute_turn_score(scores) == pytest.approx(8.0)

    def test_missing_dimension_returns_minus_one(self):
        scores = {k: 8.0 for k in _SCORE_WEIGHTS}
        del scores["correctness"]
        assert recompute_turn_score(scores) == -1.0

    def test_non_numeric_dimension_returns_minus_one(self):
        scores = {k: 8.0 for k in _SCORE_WEIGHTS}
        scores["correctness"] = "high"
        assert recompute_turn_score(scores) == -1.0

    def test_clamps_to_range(self):
        scores = self._full_scores(correctness=15.0, personality=-5.0)
        result = recompute_turn_score(scores)
        # correctness clamped to 10, personality to 0
        expected = (
            10.0 * _SCORE_WEIGHTS["correctness"]
            + 0.0 * _SCORE_WEIGHTS["personality"]
            + sum(
                8.0 * w
                for k, w in _SCORE_WEIGHTS.items()
                if k not in ("correctness", "personality")
            )
        )
        assert result == pytest.approx(expected)

    def test_weighted_correctly(self):
        scores = {k: 0.0 for k in _SCORE_WEIGHTS}
        scores["correctness"] = 10.0
        result = recompute_turn_score(scores)
        assert result == pytest.approx(10.0 * _SCORE_WEIGHTS["correctness"])


# ---------------------------------------------------------------------------
# _validate_turn_scores
# ---------------------------------------------------------------------------


class TestValidateTurnScores:
    def test_no_warnings_when_all_complete(self):
        result = {
            "turns": [
                {
                    "turn": 1,
                    "scores": {k: 8.0 for k in _SCORE_WEIGHTS},
                    "overall_score": 8.0,
                }
            ]
        }
        assert _validate_turn_scores(result) == []

    def test_warns_on_missing_dimensions(self):
        result = {
            "turns": [
                {
                    "turn": 1,
                    "scores": {"correctness": 8.0},  # missing other dimensions
                    "overall_score": 8.0,
                }
            ]
        }
        warnings = _validate_turn_scores(result)
        assert len(warnings) == 1
        assert "Turn 1" in warnings[0]

    def test_no_warning_when_no_overall_score(self):
        result = {"turns": [{"turn": 1, "scores": {}, "overall_score": None}]}
        assert _validate_turn_scores(result) == []


# ---------------------------------------------------------------------------
# _aggregate_performance
# ---------------------------------------------------------------------------


class TestAggregatePerformance:
    def test_aggregates_from_turns(self):
        result = {
            "turns": [
                {
                    "performance": {
                        "tokens_per_second": 40.0,
                        "time_to_first_token": 1.0,
                        "input_tokens": 100,
                        "output_tokens": 200,
                        "flags": ["slow"],
                    }
                },
                {
                    "performance": {
                        "tokens_per_second": 60.0,
                        "time_to_first_token": 0.5,
                        "input_tokens": 150,
                        "output_tokens": 250,
                        "flags": ["ok"],
                    }
                },
            ]
        }
        _aggregate_performance(result, "test-scenario")
        ps = result["performance_summary"]
        assert ps["avg_tokens_per_second"] == pytest.approx(50.0, abs=0.1)
        assert ps["avg_time_to_first_token"] == pytest.approx(0.75, abs=0.001)
        assert ps["total_input_tokens"] == 250
        assert ps["total_output_tokens"] == 450
        assert "slow" in ps["flags"]
        assert "ok" in ps["flags"]

    def test_none_when_no_perf_data(self):
        result = {"turns": [{"performance": None}]}
        _aggregate_performance(result, "s")
        assert result["performance_summary"] is None

    def test_handles_missing_performance_key(self):
        result = {"turns": [{"turn": 1}]}
        _aggregate_performance(result, "s")
        assert result["performance_summary"] is None

    def test_skips_invalid_values(self):
        result = {
            "turns": [
                {
                    "performance": {
                        "tokens_per_second": -1,  # invalid
                        "time_to_first_token": 0,  # invalid
                        "input_tokens": "not_a_number",
                        "output_tokens": 100,
                    }
                }
            ]
        }
        _aggregate_performance(result, "s")
        ps = result["performance_summary"]
        assert ps["avg_tokens_per_second"] is None
        assert ps["avg_time_to_first_token"] is None
        assert ps["total_output_tokens"] == 100


# ---------------------------------------------------------------------------
# _compute_effective_timeout
# ---------------------------------------------------------------------------


class TestComputeEffectiveTimeout:
    def test_base_timeout_when_no_turns_or_docs(self):
        result = _compute_effective_timeout(
            900, {"turns": [], "setup": {"index_documents": []}}
        )
        assert result >= 240  # at least startup overhead

    def test_scales_with_turns_and_docs(self):
        scenario = {
            "turns": [{"turn": 1}, {"turn": 2}],
            "setup": {"index_documents": [{"path": "a.pdf"}, {"path": "b.pdf"}]},
        }
        result = _compute_effective_timeout(900, scenario)
        expected = 240 + 2 * 90 + 2 * 200  # startup + docs + turns
        assert result >= expected

    def test_capped_at_max(self):
        scenario = {
            "turns": [{"turn": i} for i in range(100)],
            "setup": {"index_documents": [{"path": f"{i}.pdf"} for i in range(100)]},
        }
        result = _compute_effective_timeout(900, scenario)
        assert result <= 7200


# ---------------------------------------------------------------------------
# find_scenarios (with mocked filesystem)
# ---------------------------------------------------------------------------


class TestFindScenarios:
    def _write_scenario(self, d, sid, category="general", tags=None):
        import yaml

        data = {
            "id": sid,
            "category": category,
            "persona": "casual_user",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "X", "ground_truth": {"answer": "A"}}],
        }
        if tags:
            data["tags"] = tags
        path = d / f"{sid}.yaml"
        path.write_text(yaml.dump(data), encoding="utf-8")
        return path

    def test_finds_by_category(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.eval.runner.SCENARIOS_DIR", tmp_path)
        monkeypatch.setattr(
            "gaia.eval.runner.USER_SCENARIOS_DIR", tmp_path / "no-exist"
        )
        self._write_scenario(tmp_path, "s1", category="rag")
        self._write_scenario(tmp_path, "s2", category="tool")

        from gaia.eval.runner import find_scenarios

        results = find_scenarios(category="rag")
        assert len(results) == 1
        assert results[0][1]["id"] == "s1"

    def test_finds_by_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.eval.runner.SCENARIOS_DIR", tmp_path)
        monkeypatch.setattr(
            "gaia.eval.runner.USER_SCENARIOS_DIR", tmp_path / "no-exist"
        )
        self._write_scenario(tmp_path, "alpha")
        self._write_scenario(tmp_path, "beta")

        from gaia.eval.runner import find_scenarios

        results = find_scenarios(scenario_id="beta")
        assert len(results) == 1
        assert results[0][1]["id"] == "beta"

    def test_filters_by_tags(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.eval.runner.SCENARIOS_DIR", tmp_path)
        monkeypatch.setattr(
            "gaia.eval.runner.USER_SCENARIOS_DIR", tmp_path / "no-exist"
        )
        self._write_scenario(tmp_path, "s1", tags=["v1", "regression"])
        self._write_scenario(tmp_path, "s2", tags=["v2"])

        from gaia.eval.runner import find_scenarios

        results = find_scenarios(tags=["regression"])
        assert len(results) == 1
        assert results[0][1]["id"] == "s1"

    def test_extra_dirs_override(self, tmp_path, monkeypatch):
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        extra = tmp_path / "extra"
        extra.mkdir()
        monkeypatch.setattr("gaia.eval.runner.SCENARIOS_DIR", builtin)
        monkeypatch.setattr(
            "gaia.eval.runner.USER_SCENARIOS_DIR", tmp_path / "no-exist"
        )
        self._write_scenario(builtin, "overlap", category="old")
        self._write_scenario(extra, "overlap", category="new")

        from gaia.eval.runner import find_scenarios

        results = find_scenarios(extra_dirs=[str(extra)])
        assert len(results) == 1
        assert results[0][1]["category"] == "new"


# ---------------------------------------------------------------------------
# build_scenario_prompt
# ---------------------------------------------------------------------------


class TestBuildScenarioPrompt:
    def test_includes_scenario_yaml(self, monkeypatch):
        # Mock the prompt-file loaders
        monkeypatch.setattr("gaia.eval.runner._load_simulator_content", lambda: "SIM")
        monkeypatch.setattr("gaia.eval.runner._load_judge_turn_content", lambda: "TURN")
        monkeypatch.setattr(
            "gaia.eval.runner._load_judge_scenario_content", lambda: "SCENARIO"
        )
        from gaia.eval.runner import build_scenario_prompt

        scenario = {"id": "test_s", "category": "rag", "turns": []}
        prompt = build_scenario_prompt(
            scenario, {"documents": []}, "http://localhost:4200"
        )
        assert "test_s" in prompt
        assert "SIM" in prompt
        assert "TURN" in prompt
        assert "SCENARIO" in prompt
        assert "http://localhost:4200" in prompt

    def test_agent_type_injected(self, monkeypatch):
        monkeypatch.setattr("gaia.eval.runner._load_simulator_content", lambda: "")
        monkeypatch.setattr("gaia.eval.runner._load_judge_turn_content", lambda: "")
        monkeypatch.setattr("gaia.eval.runner._load_judge_scenario_content", lambda: "")
        from gaia.eval.runner import build_scenario_prompt

        prompt = build_scenario_prompt(
            {"id": "s", "turns": []},
            {},
            "http://localhost:4200",
            agent_type="gaia-lite",
        )
        assert 'agent_type="gaia-lite"' in prompt


# ---------------------------------------------------------------------------
# compare_scorecards
# ---------------------------------------------------------------------------


class TestCompareScorecards:
    def _write_scorecard(self, path, scenarios, summary_overrides=None):
        summary = {
            "total_scenarios": len(scenarios),
            "passed": sum(1 for s in scenarios if s["status"] == "PASS"),
            "failed": sum(1 for s in scenarios if s["status"] == "FAIL"),
            "pass_rate": 0.0,
            "judged_pass_rate": 0.0,
            "avg_score": 0.0,
        }
        total = summary["total_scenarios"]
        if total:
            summary["pass_rate"] = summary["passed"] / total
        if summary_overrides:
            summary.update(summary_overrides)
        data = {"summary": summary, "scenarios": scenarios}
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    def test_detects_regression(self, tmp_path):
        base = tmp_path / "base.json"
        curr = tmp_path / "curr.json"
        self._write_scorecard(
            base,
            [{"scenario_id": "s1", "status": "PASS", "overall_score": 8.0}],
        )
        self._write_scorecard(
            curr,
            [{"scenario_id": "s1", "status": "FAIL", "overall_score": 3.0}],
        )
        result = compare_scorecards(base, curr)
        assert len(result["regressed"]) == 1
        assert result["regressed"][0]["scenario_id"] == "s1"

    def test_detects_improvement(self, tmp_path):
        base = tmp_path / "base.json"
        curr = tmp_path / "curr.json"
        self._write_scorecard(
            base,
            [{"scenario_id": "s1", "status": "FAIL", "overall_score": 3.0}],
        )
        self._write_scorecard(
            curr,
            [{"scenario_id": "s1", "status": "PASS", "overall_score": 8.0}],
        )
        result = compare_scorecards(base, curr)
        assert len(result["improved"]) == 1

    def test_detects_score_regression(self, tmp_path):
        base = tmp_path / "base.json"
        curr = tmp_path / "curr.json"
        self._write_scorecard(
            base,
            [{"scenario_id": "s1", "status": "PASS", "overall_score": 9.0}],
        )
        self._write_scorecard(
            curr,
            [{"scenario_id": "s1", "status": "PASS", "overall_score": 6.5}],
        )
        result = compare_scorecards(base, curr)
        assert len(result["score_regressed"]) == 1

    def test_only_in_baseline_and_current(self, tmp_path):
        base = tmp_path / "base.json"
        curr = tmp_path / "curr.json"
        self._write_scorecard(
            base,
            [{"scenario_id": "old", "status": "PASS", "overall_score": 8.0}],
        )
        self._write_scorecard(
            curr,
            [{"scenario_id": "new", "status": "PASS", "overall_score": 8.0}],
        )
        result = compare_scorecards(base, curr)
        assert "old" in result["only_in_baseline"]
        assert "new" in result["only_in_current"]

    def test_corpus_changed(self, tmp_path):
        base = tmp_path / "base.json"
        curr = tmp_path / "curr.json"
        self._write_scorecard(
            base,
            [{"scenario_id": "s1", "status": "PASS", "overall_score": 8.0}],
        )
        self._write_scorecard(
            curr,
            [
                {
                    "scenario_id": "s1",
                    "status": "SKIPPED_NO_DOCUMENT",
                    "overall_score": None,
                }
            ],
        )
        result = compare_scorecards(base, curr)
        assert len(result["corpus_changed"]) == 1

    def test_missing_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            compare_scorecards(tmp_path / "nope.json", tmp_path / "also-nope.json")

    def test_time_regression(self, tmp_path):
        base = tmp_path / "base.json"
        curr = tmp_path / "curr.json"
        self._write_scorecard(
            base,
            [
                {
                    "scenario_id": "s1",
                    "status": "PASS",
                    "overall_score": 8.0,
                    "elapsed_s": 30.0,
                }
            ],
        )
        self._write_scorecard(
            curr,
            [
                {
                    "scenario_id": "s1",
                    "status": "PASS",
                    "overall_score": 8.0,
                    "elapsed_s": 120.0,
                }
            ],
        )
        result = compare_scorecards(base, curr)
        assert len(result["time_regressed"]) == 1


# ---------------------------------------------------------------------------
# AgentEvalRunner.__init__
# ---------------------------------------------------------------------------


class TestAgentEvalRunnerInit:
    def test_defaults(self):
        from gaia.eval.runner import AgentEvalRunner

        runner = AgentEvalRunner()
        assert runner.backend_url == "http://localhost:4200"
        assert runner.model == "claude-sonnet-4-6"
        assert runner.budget == "2.00"
        assert runner.timeout == 900

    def test_custom_args(self, tmp_path):
        from gaia.eval.runner import AgentEvalRunner

        runner = AgentEvalRunner(
            backend_url="http://custom:5000",
            model="claude-opus-4",
            budget_per_scenario="5.00",
            timeout_per_scenario=1200,
            results_dir=str(tmp_path),
            tags=["regression"],
            agent_type="gaia-lite",
        )
        assert runner.backend_url == "http://custom:5000"
        assert runner.model == "claude-opus-4"
        assert runner.budget == "5.00"
        assert runner.timeout == 1200
        assert runner.results_dir == tmp_path
        assert runner.tags == ["regression"]
        assert runner.agent_type == "gaia-lite"
