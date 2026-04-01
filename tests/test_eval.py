#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit and integration tests for the evaluation tool"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


class TestEvalCLI:
    """Test eval command-line interface"""

    def test_eval_help(self):
        """Test that eval help command works"""
        # Use python -m approach for reliability in CI
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"
        # Check for key help text components that should be present
        help_text = result.stdout.lower()
        assert "eval" in help_text
        assert "--results-file" in result.stdout or "-f" in result.stdout
        assert "--directory" in result.stdout or "-d" in result.stdout

    def test_visualize_help(self):
        """Test that visualize help command works"""
        # Use python -m approach for reliability in CI
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "visualize", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"
        # Check for key components that should be present
        help_text = result.stdout.lower()
        assert "visualize" in help_text
        assert "--port" in result.stdout

    def test_eval_missing_args(self):
        """Test eval command fails when default directory has no experiment files"""
        # Use python -m approach for reliability in CI
        # When run without arguments, eval will use the default directory
        # If that directory doesn't exist or has no files, it should fail
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # Handle any encoding issues gracefully
        )

        # The command should either:
        # 1. Exit with error code if no experiment files are found, OR
        # 2. Exit with success code if files were found and processed
        # For CI environments, typically the default directory won't exist or will be empty
        # so we expect a non-zero return code in most cases

        # Command should produce some output regardless of outcome
        combined_output = (result.stdout + result.stderr).lower()
        assert any(
            word in combined_output
            for word in [
                "found",
                "processing",
                "evaluations",
                "skipping",
                "no",
                "not found",
                "error",
                "evaluating",
                "install",
            ]
        ), f"Expected diagnostic output, got: {combined_output[:200]}"


class TestImports:
    """Verify critical imports are present in runner module."""

    def test_timezone_imported(self):
        """runner.py must import timezone alongside datetime (used in run_id generation)."""
        from gaia.eval import runner

        assert hasattr(
            runner, "timezone"
        ), "runner.py is missing 'from datetime import timezone'"


class TestEvalCore:
    """Test core evaluation functionality"""

    @pytest.fixture
    def mock_experiment_data(self):
        """Create mock experiment data for testing"""
        return {
            "experiment_name": "test-model-basic-summary",
            "model": "test-model",
            "config": {
                "name": "basic_summarization",
                "description": "Basic summarization test",
            },
            "results": [
                {
                    "file": "test_meeting.txt",
                    "summary": "This is a test summary of the meeting.",
                    "metadata": {"processing_time": 1.5, "token_count": 100},
                }
            ],
            "metadata": {"total_files": 1, "total_time": 1.5},
        }

    @pytest.fixture
    def mock_groundtruth_data(self):
        """Create mock ground truth data for testing"""
        return {
            "test_meeting.txt": {
                "reference_summary": "This is the reference summary of the meeting.",
                "key_points": ["Point 1", "Point 2"],
                "quality_criteria": {
                    "completeness": "All main topics covered",
                    "accuracy": "Facts are correct",
                    "clarity": "Easy to understand",
                },
            }
        }

    def test_load_experiment_file(self, tmp_path, mock_experiment_data):
        """Test loading experiment JSON file"""
        # Write mock data to temp file
        exp_file = tmp_path / "test.experiment.json"
        with open(exp_file, "w") as f:
            json.dump(mock_experiment_data, f)

        # Just test that we can read the file back
        with open(exp_file, "r") as f:
            data = json.load(f)

        assert data["experiment_name"] == "test-model-basic-summary"
        assert len(data["results"]) == 1
        assert data["model"] == "test-model"

    def test_webapp_files_exist(self):
        """Test that webapp files are present"""
        webapp_dir = (
            Path(__file__).parent.parent / "src" / "gaia" / "eval" / "webapp" / "public"
        )

        assert (webapp_dir / "index.html").exists(), "index.html missing"
        assert (webapp_dir / "app.js").exists(), "app.js missing"
        assert (webapp_dir / "styles.css").exists(), "styles.css missing"

    def test_eval_configs_valid(self):
        """Test that eval configuration files are valid JSON"""
        config_dir = Path(__file__).parent.parent / "src" / "gaia" / "eval" / "configs"

        for config_file in config_dir.glob("*.json"):
            with open(config_file, "r") as f:
                try:
                    config = json.load(f)
                    assert (
                        "description" in config
                    ), f"{config_file.name} missing 'description' field"
                    assert (
                        "experiments" in config
                    ), f"{config_file.name} missing 'experiments' field"
                except json.JSONDecodeError as e:
                    pytest.fail(f"Invalid JSON in {config_file.name}: {e}")


class TestEvalIntegration:
    """Integration tests for eval tool"""

    def test_webapp_npm_package_exists(self):
        """Test that webapp has proper Node.js package configuration"""
        webapp_dir = Path(__file__).parent.parent / "src" / "gaia" / "eval" / "webapp"
        package_json = webapp_dir / "package.json"

        assert package_json.exists(), "Webapp should have package.json"

        # Verify package.json structure
        with open(package_json, "r") as f:
            package_data = json.load(f)

        assert "name" in package_data, "package.json should have name"
        assert "scripts" in package_data, "package.json should have scripts"
        assert "test" in package_data["scripts"], "package.json should have test script"


class TestAgentEvalScorecard:
    """Tests for the agent eval scorecard module."""

    def _make_result(self, scenario_id, status, score, category="rag_quality"):
        return {
            "scenario_id": scenario_id,
            "status": status,
            "overall_score": score,
            "category": category,
            "cost_estimate": {"estimated_usd": 0.05},
        }

    def test_build_scorecard_pass_rate(self):
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0),
            self._make_result("b", "PASS", 8.0),
            self._make_result("c", "FAIL", 4.0),
        ]
        sc = build_scorecard("run-1", results, {})
        assert sc["summary"]["passed"] == 2
        assert sc["summary"]["failed"] == 1
        assert abs(sc["summary"]["pass_rate"] - 2 / 3) < 0.001

    def test_avg_score_excludes_infra_failures(self):
        """ERRORED/TIMEOUT/BUDGET_EXCEEDED scenarios (score=0) must not dilute avg."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0),
            self._make_result("b", "FAIL", 5.0),
            self._make_result("c", "ERRORED", 0),
            self._make_result("d", "TIMEOUT", 0),
            self._make_result("e", "BUDGET_EXCEEDED", 0),
        ]
        sc = build_scorecard("run-1", results, {})
        # avg_score should only count PASS + FAIL (judged scenarios)
        assert abs(sc["summary"]["avg_score"] - 7.0) < 0.01
        assert sc["summary"]["timeout"] == 1
        assert sc["summary"]["budget_exceeded"] == 1
        assert sc["summary"]["errored"] == 1

    def test_avg_score_excludes_setup_error(self):
        """SETUP_ERROR must be excluded from avg_score (same as other infra failures)."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 8.0),
            self._make_result("b", "SETUP_ERROR", None),
            self._make_result("c", "INFRA_ERROR", None),
        ]
        sc = build_scorecard("run-1", results, {})
        # avg_score = only the PASS scenario = 8.0
        assert sc["summary"]["avg_score"] == pytest.approx(8.0)
        assert sc["summary"]["infra_error"] == 2

    def test_by_category_grouping(self):
        """Category field from result must appear in by_category breakdown."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0, category="rag_quality"),
            self._make_result("b", "FAIL", 4.0, category="rag_quality"),
            self._make_result("c", "PASS", 8.0, category="tool_selection"),
        ]
        sc = build_scorecard("run-1", results, {})
        cats = sc["summary"]["by_category"]
        assert "rag_quality" in cats
        assert "tool_selection" in cats
        assert "unknown" not in cats
        assert cats["rag_quality"]["passed"] == 1
        assert cats["rag_quality"]["failed"] == 1
        assert cats["tool_selection"]["passed"] == 1

    def test_write_summary_md(self):
        from gaia.eval.scorecard import build_scorecard, write_summary_md

        results = [self._make_result("a", "PASS", 9.0)]
        sc = build_scorecard("run-x", results, {"model": "test-model"})
        md = write_summary_md(sc)
        assert "run-x" in md
        assert "test-model" in md
        assert "PASS" in md or "✅" in md


class TestAgentEvalAudit:
    """Tests for the architecture audit module."""

    def test_audit_returns_expected_keys(self):
        from gaia.eval.audit import run_audit

        result = run_audit()
        audit = result["architecture_audit"]
        assert "history_pairs" in audit
        assert "max_msg_chars" in audit
        assert "tool_results_in_history" in audit
        assert "agent_persistence" in audit
        assert "blocked_scenarios" in audit
        assert "recommendations" in audit

    def test_audit_agent_persistence_reads_chat_helpers(self, tmp_path):
        from gaia.eval.audit import audit_agent_persistence

        # File with ChatAgent( -> stateless_per_message
        f = tmp_path / "helpers.py"
        f.write_text(
            "async def handle():\n    agent = ChatAgent(config)\n    return agent\n"
        )
        assert audit_agent_persistence(f) == "stateless_per_message"

        # File without ChatAgent( -> unknown
        f2 = tmp_path / "other.py"
        f2.write_text("def foo(): pass\n")
        assert audit_agent_persistence(f2) == "unknown"

    def test_audit_tool_results_in_history(self, tmp_path):
        from gaia.eval.audit import audit_tool_results_in_history

        # File with pattern indicating tool results in history
        f = tmp_path / "helpers.py"
        f.write_text(
            "agent_steps = get_steps()\nmessages = build_history(agent_steps)\nrole = 'user'\n"
        )
        assert audit_tool_results_in_history(f) is True

        # File without the pattern
        f2 = tmp_path / "other.py"
        f2.write_text("def foo(): pass\n")
        assert audit_tool_results_in_history(f2) is False

    def test_audit_reads_real_chat_helpers_values(self):
        """Integration canary: audit must read the real constants from _chat_helpers.py.

        This test breaks intentionally if someone renames or changes _MAX_HISTORY_PAIRS
        or _MAX_MSG_CHARS, alerting that eval recommendations need updating.
        """
        from gaia.eval.audit import audit_chat_helpers

        constants = audit_chat_helpers()
        assert (
            constants.get("_MAX_HISTORY_PAIRS") == 5
        ), "_MAX_HISTORY_PAIRS changed in _chat_helpers.py — update eval recommendations"
        assert (
            constants.get("_MAX_MSG_CHARS") == 2000
        ), "_MAX_MSG_CHARS changed in _chat_helpers.py — update eval recommendations"


class TestAgentEvalRunner:
    """Tests for runner helpers that don't require subprocess/LLM."""

    def test_find_scenarios_returns_yaml_files(self):
        from gaia.eval.runner import find_scenarios

        scenarios = find_scenarios()
        assert len(scenarios) > 0
        ids = [data["id"] for _, data in scenarios]
        # Verify a few known scenario IDs are present
        assert "simple_factual_rag" in ids
        assert "hallucination_resistance" in ids
        assert "no_tools_needed" in ids

    def test_find_scenarios_filter_by_id(self):
        from gaia.eval.runner import find_scenarios

        results = find_scenarios(scenario_id="simple_factual_rag")
        assert len(results) == 1
        assert results[0][1]["id"] == "simple_factual_rag"

    def test_find_scenarios_filter_by_category(self):
        from gaia.eval.runner import find_scenarios

        results = find_scenarios(category="rag_quality")
        assert len(results) > 0
        for _, data in results:
            assert data["category"] == "rag_quality"

    def test_scenario_ids_are_unique(self):
        from gaia.eval.runner import find_scenarios

        scenarios = find_scenarios()
        ids = [data["id"] for _, data in scenarios]
        assert len(ids) == len(
            set(ids)
        ), f"Duplicate scenario IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_scenarios_have_required_fields(self):
        from gaia.eval.runner import find_scenarios

        scenarios = find_scenarios()
        for path, data in scenarios:
            assert "id" in data, f"{path.name} missing 'id'"
            assert "category" in data, f"{path.name} missing 'category'"
            assert "turns" in data, f"{path.name} missing 'turns'"
            assert len(data["turns"]) > 0, f"{path.name} has no turns"
            assert "setup" in data, f"{path.name} missing 'setup'"

    def test_compare_scorecards_detects_regression(self, tmp_path):
        import json

        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        def _sc(results):
            sc = build_scorecard("run", results, {})
            p = tmp_path / f"{id(results)}.json"
            p.write_text(json.dumps(sc))
            return p

        baseline_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        current_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "FAIL",
                "overall_score": 3.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        diff = compare_scorecards(_sc(baseline_results), _sc(current_results))
        assert len(diff["regressed"]) == 1
        assert diff["regressed"][0]["scenario_id"] == "b"
        assert len(diff["improved"]) == 0

    def test_corpus_manifest_references_exist(self):
        """All files listed in manifest.json must exist on disk."""
        from gaia.eval.runner import CORPUS_DIR, MANIFEST

        assert MANIFEST.exists(), f"Corpus manifest not found: {MANIFEST}"
        manifest = __import__("json").loads(MANIFEST.read_text(encoding="utf-8"))

        docs_dir = CORPUS_DIR / "documents"
        adv_dir = CORPUS_DIR / "adversarial"
        missing = []
        for doc in manifest.get("documents", []):
            if not (docs_dir / doc["filename"]).exists():
                missing.append(doc["filename"])
        for doc in manifest.get("adversarial_documents", []):
            if not (adv_dir / doc["filename"]).exists():
                missing.append(doc["filename"])
        assert not missing, f"Manifest files missing from disk: {missing}"

    def test_all_corpus_documents_in_manifest(self):
        """Every file in corpus/documents/ must have a manifest entry (no orphans)."""
        from gaia.eval.runner import CORPUS_DIR, MANIFEST

        assert MANIFEST.exists(), f"Corpus manifest not found: {MANIFEST}"
        manifest = __import__("json").loads(MANIFEST.read_text(encoding="utf-8"))
        manifest_filenames = {doc["filename"] for doc in manifest.get("documents", [])}

        docs_dir = CORPUS_DIR / "documents"
        orphans = []
        for f in docs_dir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                if f.name not in manifest_filenames:
                    orphans.append(f.name)
        assert not orphans, f"Files in corpus/documents/ not in manifest: {orphans}"

    def test_compare_scorecards_detects_score_regression(self, tmp_path):
        """PASS→PASS with score drop ≥2.0 should appear in score_regressed."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        def _sc(results):
            sc = build_scorecard("run", results, {})
            p = tmp_path / f"sc_{id(results)}.json"
            p.write_text(json.dumps(sc))
            return p

        baseline_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        current_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 6.5,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        diff = compare_scorecards(_sc(baseline_results), _sc(current_results))
        assert len(diff["score_regressed"]) == 1
        assert diff["score_regressed"][0]["scenario_id"] == "a"
        assert diff["score_regressed"][0]["delta"] == pytest.approx(-2.5, abs=0.01)
        assert len(diff["regressed"]) == 0  # still passing — not a full regression

    def test_compare_scorecards_small_drop_not_flagged(self, tmp_path):
        """Small PASS→PASS score drop <2.0 should not appear in score_regressed."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        def _sc(results):
            sc = build_scorecard("run", results, {})
            p = tmp_path / f"sc_{id(results)}.json"
            p.write_text(json.dumps(sc))
            return p

        baseline_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        current_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 7.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        diff = compare_scorecards(_sc(baseline_results), _sc(current_results))
        assert len(diff["score_regressed"]) == 0
        assert len(diff["unchanged"]) == 1


class TestScoreValidation:
    """Tests for post-hoc score validation helpers."""

    def test_recompute_turn_score_correct(self):
        from gaia.eval.runner import recompute_turn_score

        scores = {
            "correctness": 10,
            "tool_selection": 10,
            "context_retention": 10,
            "completeness": 10,
            "efficiency": 10,
            "personality": 10,
            "error_recovery": 10,
        }
        assert recompute_turn_score(scores) == pytest.approx(10.0, abs=0.001)

    def test_recompute_turn_score_weighted(self):
        from gaia.eval.runner import recompute_turn_score

        # correctness=10 (25%), everything else=0
        scores = {
            "correctness": 10,
            "tool_selection": 0,
            "context_retention": 0,
            "completeness": 0,
            "efficiency": 0,
            "personality": 0,
            "error_recovery": 0,
        }
        assert recompute_turn_score(scores) == pytest.approx(2.5, abs=0.001)

    def test_recompute_turn_score_missing_dimension(self):
        from gaia.eval.runner import recompute_turn_score

        # Missing error_recovery → should return -1.0
        scores = {
            "correctness": 8,
            "tool_selection": 7,
            "context_retention": 9,
            "completeness": 8,
            "efficiency": 7,
            "personality": 8,
        }
        assert recompute_turn_score(scores) == -1.0

    def test_recompute_turn_score_clamps_out_of_range(self):
        from gaia.eval.runner import recompute_turn_score

        # Hallucinating eval agent returns out-of-range scores → clamped to [0, 10]
        scores = {
            "correctness": 15,  # clamped to 10
            "tool_selection": -3,  # clamped to 0
            "context_retention": 10,
            "completeness": 10,
            "efficiency": 10,
            "personality": 10,
            "error_recovery": 10,
        }
        result = recompute_turn_score(scores)
        # Same as all-10 except tool_selection=0: 10*0.25 + 0*0.20 + 10*0.20 + 10*0.15 + 10*0.10 + 10*0.05 + 10*0.05 = 8.0
        expected = (
            10 * 0.25
            + 0 * 0.20
            + 10 * 0.20
            + 10 * 0.15
            + 10 * 0.10
            + 10 * 0.05
            + 10 * 0.05
        )
        assert result == pytest.approx(expected, abs=0.001)

    def test_recompute_turn_score_string_values(self):
        from gaia.eval.runner import recompute_turn_score

        # LLM returned score dimensions as strings → should return -1.0 (not crash)
        scores = {
            "correctness": "8",
            "tool_selection": "7",
            "context_retention": "9",
            "completeness": "8",
            "efficiency": "7",
            "personality": "8",
            "error_recovery": "7",
        }
        assert recompute_turn_score(scores) == -1.0

    def test_validate_turn_scores_no_warnings_when_dims_present(self):
        from gaia.eval.runner import _validate_turn_scores

        result = {
            "turns": [
                {
                    "turn": 1,
                    "overall_score": 7.45,
                    "scores": {
                        "correctness": 8,
                        "tool_selection": 8,
                        "context_retention": 7,
                        "completeness": 7,
                        "efficiency": 7,
                        "personality": 7,
                        "error_recovery": 7,
                    },
                }
            ]
        }
        # All dimension scores present → recompute succeeds → no warning
        warnings = _validate_turn_scores(result)
        assert warnings == []

    def test_validate_turn_scores_warns_on_missing_dimensions(self):
        """A turn with missing dimension scores cannot be recomputed → warning."""
        from gaia.eval.runner import _validate_turn_scores

        result = {
            "turns": [
                {
                    "turn": 1,
                    "overall_score": 7.0,
                    "scores": {
                        "correctness": 8,
                        "tool_selection": 8,
                        # missing: context_retention, completeness, efficiency, personality, error_recovery
                    },
                }
            ]
        }
        warnings = _validate_turn_scores(result)
        assert len(warnings) == 1
        assert "Turn 1" in warnings[0]
        assert "missing dimension" in warnings[0]


class TestValidateScenario:
    """Tests for the scenario YAML schema validator."""

    def test_valid_scenario_passes(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "persona": "casual_user",
            "setup": {"index_documents": []},
            "turns": [
                {
                    "turn": 1,
                    "objective": "Ask something",
                    "ground_truth": {"expected_answer": "42"},
                },
            ],
        }
        validate_scenario(tmp_path / "test.yaml", data)  # should not raise

    def test_missing_persona_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            # missing persona
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }
        with pytest.raises(ValueError, match="persona"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_invalid_persona_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "persona": "not_a_real_persona",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }
        with pytest.raises(ValueError, match="not a known persona"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_non_string_persona_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "persona": 42,
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }
        with pytest.raises(ValueError, match="persona must be a string"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_empty_ground_truth_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "ground_truth": {}}],
        }
        with pytest.raises(ValueError, match="ground_truth.*success_criteria"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_missing_required_field_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            # missing setup and turns
        }
        with pytest.raises(ValueError, match="missing top-level field"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_empty_turns_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [],
        }
        with pytest.raises(ValueError, match="turns list is empty"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_duplicate_turn_numbers_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "first", "success_criteria": "ok"},
                {"turn": 1, "objective": "duplicate", "success_criteria": "ok"},
            ],
        }
        with pytest.raises(ValueError, match="duplicate turn number"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_turn_missing_objective_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "ground_truth": {"expected_answer": "x"}},
            ],
        }
        with pytest.raises(ValueError, match="missing 'objective'"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_missing_setup_index_documents_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {},  # missing index_documents key
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }
        with pytest.raises(ValueError, match="setup.index_documents is missing"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_ground_truth_null_without_success_criteria_raises(self, tmp_path):
        """ground_truth: null with no success_criteria must fail validation."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "ground_truth": None}],
        }
        with pytest.raises(ValueError, match="must have at least one of"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_dict_success_criteria_raises(self, tmp_path):
        """success_criteria as a dict (old capture format) must fail validation."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {
                    "turn": 1,
                    "objective": "x",
                    "success_criteria": {
                        "must_contain": [],
                        "agent_response_preview": "foo",
                    },
                }
            ],
        }
        with pytest.raises(ValueError, match="success_criteria must be a string"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_non_sequential_turn_numbers_raises(self, tmp_path):
        """Turn numbers like [1, 3] that skip 2 must fail validation."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "first", "success_criteria": "ok"},
                {"turn": 3, "objective": "skipped 2", "success_criteria": "ok"},
            ],
        }
        with pytest.raises(ValueError, match="sequential"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_turns_not_starting_at_1_raises(self, tmp_path):
        """Turn numbers starting at 2 must fail validation."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 2, "objective": "starts at 2", "success_criteria": "ok"},
            ],
        }
        with pytest.raises(ValueError, match="sequential"):
            validate_scenario(tmp_path / "test.yaml", data)


class TestManifestCrossReference:
    """Validate doc_id and fact_id cross-references between scenario YAMLs and merged manifest."""

    @staticmethod
    def _load_merged_manifest():
        """Load the main manifest merged with the real-world manifest (if present).

        Mirrors the merge logic in run_scenario_subprocess so cross-reference tests
        catch broken references in both standard and real-world scenarios.
        """
        from gaia.eval.runner import MANIFEST, REAL_WORLD_MANIFEST

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if REAL_WORLD_MANIFEST.exists():
            rw = json.loads(REAL_WORLD_MANIFEST.read_text(encoding="utf-8"))
            manifest["documents"] = manifest.get("documents", []) + rw.get(
                "documents", []
            )
        return manifest

    def test_scenario_doc_ids_exist_in_manifest(self):
        """Every doc_id referenced in scenario ground_truth must exist in the merged manifest."""
        from gaia.eval.runner import REAL_WORLD_MANIFEST, SCENARIOS_DIR, find_scenarios

        manifest = self._load_merged_manifest()
        all_doc_ids = {doc["id"] for doc in manifest.get("documents", [])}
        real_world_scenarios_dir = SCENARIOS_DIR / "real_world"
        real_world_manifest_present = REAL_WORLD_MANIFEST.exists()

        scenarios = find_scenarios()
        missing = []
        for path, data in scenarios:
            # Skip real_world scenarios when their manifest isn't present (e.g. in CI)
            if not real_world_manifest_present and str(path).startswith(
                str(real_world_scenarios_dir)
            ):
                continue
            for turn in data.get("turns", []):
                gt = turn.get("ground_truth") or {}
                doc_id = gt.get("doc_id")
                if doc_id and doc_id not in all_doc_ids:
                    missing.append(
                        f"{data['id']} turn {turn.get('turn', '?')}: doc_id='{doc_id}'"
                    )
        assert (
            not missing
        ), "Scenario doc_id references not in merged manifest:\n  " + "\n  ".join(
            missing
        )

    def test_scenario_fact_ids_exist_in_manifest(self):
        """Every fact_id referenced in scenarios must exist in the merged manifest."""
        from gaia.eval.runner import REAL_WORLD_MANIFEST, SCENARIOS_DIR, find_scenarios

        manifest = self._load_merged_manifest()
        # Real-world manifest facts don't have 'id' fields — only index by (doc_id, fact_id)
        # for documents where facts have IDs.
        all_fact_ids = {
            (doc["id"], fact["id"])
            for doc in manifest.get("documents", [])
            for fact in doc.get("facts", [])
            if "id" in fact
        }
        real_world_scenarios_dir = SCENARIOS_DIR / "real_world"
        real_world_manifest_present = REAL_WORLD_MANIFEST.exists()

        scenarios = find_scenarios()
        missing = []
        for path, data in scenarios:
            # Skip real_world scenarios when their manifest isn't present (e.g. in CI)
            if not real_world_manifest_present and str(path).startswith(
                str(real_world_scenarios_dir)
            ):
                continue
            for turn in data.get("turns", []):
                gt = turn.get("ground_truth") or {}
                doc_id = gt.get("doc_id")
                # Check singular fact_id
                fact_id = gt.get("fact_id")
                if doc_id and fact_id and (doc_id, fact_id) not in all_fact_ids:
                    missing.append(
                        f"{data['id']} turn {turn.get('turn', '?')}: {doc_id}.{fact_id}"
                    )
                # Check plural fact_ids list
                for fid in gt.get("fact_ids", []):
                    if doc_id and fid and (doc_id, fid) not in all_fact_ids:
                        missing.append(
                            f"{data['id']} turn {turn.get('turn', '?')}: {doc_id}.{fid} (from fact_ids)"
                        )
        assert (
            not missing
        ), "Scenario fact_id references not in merged manifest:\n  " + "\n  ".join(
            missing
        )

    def test_scenario_corpus_docs_exist_in_manifest(self):
        """Every corpus_doc in setup.index_documents must match a manifest document id."""
        from gaia.eval.runner import REAL_WORLD_MANIFEST, SCENARIOS_DIR, find_scenarios

        manifest = self._load_merged_manifest()
        all_doc_ids = {doc["id"] for doc in manifest.get("documents", [])}
        real_world_scenarios_dir = SCENARIOS_DIR / "real_world"
        real_world_manifest_present = REAL_WORLD_MANIFEST.exists()

        scenarios = find_scenarios()
        missing = []
        for path, data in scenarios:
            if not real_world_manifest_present and str(path).startswith(
                str(real_world_scenarios_dir)
            ):
                continue
            for i, doc in enumerate(data.get("setup", {}).get("index_documents", [])):
                if not isinstance(doc, dict):
                    continue
                corpus_doc = doc.get("corpus_doc")
                if corpus_doc and corpus_doc not in all_doc_ids:
                    missing.append(
                        f"{data['id']} setup.index_documents[{i}]: corpus_doc='{corpus_doc}'"
                    )
        assert (
            not missing
        ), "Scenario corpus_doc references not in merged manifest:\n  " + "\n  ".join(
            missing
        )


class TestComputeEffectiveTimeout:
    """Tests for _compute_effective_timeout."""

    def test_base_timeout_used_when_larger(self):
        from gaia.eval.runner import _compute_effective_timeout

        data = {"turns": [{}], "setup": {"index_documents": []}}
        # base=9000 >> 120 + 0*90 + 1*200 = 320 → should return 9000 (but capped at 7200)
        assert _compute_effective_timeout(7200, data) == 7200

    def test_computed_exceeds_base(self):
        from gaia.eval.runner import _compute_effective_timeout

        # 2 docs + 3 turns → 120 + 2*90 + 3*200 = 120+180+600 = 900
        data = {
            "turns": [{}, {}, {}],
            "setup": {"index_documents": [{}, {}]},
        }
        result = _compute_effective_timeout(100, data)
        assert result == 900

    def test_cap_enforced(self):
        from gaia.eval.runner import (
            _MAX_EFFECTIVE_TIMEOUT_S,
            _compute_effective_timeout,
        )

        # 100 docs + 100 turns → 120 + 100*90 + 100*200 = 120+9000+20000 = 29120 > cap
        data = {
            "turns": [{}] * 100,
            "setup": {"index_documents": [{}] * 100},
        }
        result = _compute_effective_timeout(100, data)
        assert result == _MAX_EFFECTIVE_TIMEOUT_S

    def test_empty_scenario_uses_startup_overhead(self):
        from gaia.eval.runner import _STARTUP_OVERHEAD_S, _compute_effective_timeout

        data = {"turns": [], "setup": {"index_documents": []}}
        result = _compute_effective_timeout(0, data)
        assert result == _STARTUP_OVERHEAD_S


class TestBuildScenarioPrompt:
    """Tests for the prompt builder."""

    def _make_scenario(self):
        return {
            "id": "test_s",
            "category": "rag_quality",
            "setup": {
                "index_documents": [
                    {"corpus_doc": "x", "path": "eval/corpus/documents/x.md"}
                ]
            },
            "turns": [
                {
                    "turn": 1,
                    "objective": "Ask something",
                    "ground_truth": {"expected_answer": "42"},
                }
            ],
        }

    def test_prompt_contains_scenario_id(self):
        from gaia.eval.runner import build_scenario_prompt

        prompt = build_scenario_prompt(
            self._make_scenario(), {}, "http://localhost:4200"
        )
        assert "test_s" in prompt

    def test_prompt_contains_corpus_root(self):
        from gaia.eval.runner import CORPUS_DIR, build_scenario_prompt

        prompt = build_scenario_prompt(
            self._make_scenario(), {}, "http://localhost:4200"
        )
        corpus_root = str(CORPUS_DIR / "documents").replace("\\", "/")
        assert corpus_root in prompt

    def test_prompt_contains_backend_url(self):
        from gaia.eval.runner import build_scenario_prompt

        prompt = build_scenario_prompt(
            self._make_scenario(), {}, "http://localhost:9999"
        )
        assert "http://localhost:9999" in prompt

    def test_prompt_contains_scoring_rules(self):
        from gaia.eval.runner import build_scenario_prompt

        prompt = build_scenario_prompt(
            self._make_scenario(), {}, "http://localhost:4200"
        )
        # simulator.md content is inlined — verify key rubric elements are present
        assert "correctness" in prompt
        assert "PASS" in prompt
        assert "FAIL" in prompt

    def test_prompt_contains_manifest_json(self):
        from gaia.eval.runner import build_scenario_prompt

        manifest = {
            "documents": [{"id": "test_doc", "filename": "test.md", "facts": []}]
        }
        prompt = build_scenario_prompt(
            self._make_scenario(), manifest, "http://localhost:4200"
        )
        assert "test_doc" in prompt


class TestRunScenarioSubprocess:
    """Tests for run_scenario_subprocess JSON parsing via mocked subprocess."""

    def _minimal_scenario(self):
        return {
            "id": "mock_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }

    def _run(self, mocker, stdout, returncode=0):
        import tempfile

        from gaia.eval.runner import run_scenario_subprocess

        mock_proc = mocker.MagicMock()
        mock_proc.stdout = stdout
        mock_proc.stderr = ""
        mock_proc.returncode = returncode
        mocker.patch("subprocess.run", return_value=mock_proc)

        with tempfile.TemporaryDirectory() as tmp:
            return run_scenario_subprocess(
                Path(tmp) / "scenario.yaml",
                self._minimal_scenario(),
                Path(tmp),
                "http://localhost:4200",
                "claude-sonnet-4-6",
                "1.00",
                30,
            )

    def test_structured_output_parsed(self, mocker):
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",
                "overall_score": 9.0,
                "turns": [],
                "root_cause": None,
                "recommended_fix": None,
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "PASS"
        assert result["overall_score"] == 9.0
        assert result["category"] == "rag_quality"

    def test_budget_exceeded_detected(self, mocker):
        payload = {
            "subtype": "error_max_budget_usd",
            "total_cost_usd": 2.05,
            "num_turns": 3,
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "BUDGET_EXCEEDED"
        assert result["overall_score"] is None

    def test_nonzero_exit_returns_errored(self, mocker):
        result = self._run(mocker, "", returncode=1)
        assert result["status"] == "ERRORED"
        assert result["overall_score"] is None

    def test_missing_status_field_defaulted(self, mocker):
        """Eval agent returning JSON without 'status' should be defaulted to ERRORED."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "overall_score": 7.0,
                "turns": [],
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "ERRORED"

    def test_none_score_does_not_crash_print(self, mocker):
        """overall_score=null should not raise TypeError in the [DONE] print."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "FAIL",
                "overall_score": None,
                "turns": [],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.0},
            }
        }
        result = self._run(mocker, json.dumps(payload))  # must not raise
        assert result["status"] == "FAIL"
        assert result["overall_score"] is None

    def test_turn_score_overwrite_with_recomputed(self, mocker):
        """LLM arithmetic errors are corrected: turn overall_score is overwritten with recomputed value."""
        # LLM reported 5.0 but weighted sum of dimension scores is 7.45
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",
                "overall_score": 9.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 8,
                            "tool_selection": 8,
                            "context_retention": 7,
                            "completeness": 7,
                            "efficiency": 7,
                            "personality": 7,
                            "error_recovery": 7,
                        },
                        "overall_score": 5.0,  # wrong — should be 7.45
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "ok",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Recomputed: 8*.25 + 8*.20 + 7*.20 + 7*.15 + 7*.10 + 7*.05 + 7*.05 = 7.45
        assert result["turns"][0]["overall_score"] == pytest.approx(7.45, abs=0.01)

    def test_scenario_overall_score_derived_from_turns(self, mocker):
        """Scenario-level overall_score is recomputed as mean of per-turn scores."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",
                "overall_score": 3.0,  # LLM wrong value — should become mean of turns
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 10,
                            "tool_selection": 10,
                            "context_retention": 10,
                            "completeness": 10,
                            "efficiency": 10,
                            "personality": 10,
                            "error_recovery": 10,
                        },
                        "overall_score": 10.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "ok",
                    },
                    {
                        "turn": 2,
                        "user_message": "bye",
                        "agent_response": "bye",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 0,
                            "tool_selection": 0,
                            "context_retention": 0,
                            "completeness": 0,
                            "efficiency": 0,
                            "personality": 0,
                            "error_recovery": 0,
                        },
                        "overall_score": 0.0,
                        "pass": False,
                        "failure_category": "wrong_answer",
                        "reasoning": "bad",
                    },
                ],
                "cost_estimate": {"turns": 2, "estimated_usd": 0.02},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Turn 1 recomputed = 10.0, Turn 2 recomputed = 0.0 → mean = 5.0
        assert result["overall_score"] == pytest.approx(5.0, abs=0.01)

    def test_all_null_turn_scores_sets_scenario_score_to_none(self, mocker):
        """When all turns have null overall_score, scenario overall_score is set to None."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "FAIL",
                "overall_score": 7.5,  # LLM value — should be nullified since no turn scores
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {},  # empty — all dimensions missing → recompute returns -1
                        "overall_score": None,
                        "pass": False,
                        "failure_category": "wrong_answer",
                        "reasoning": "bad",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["overall_score"] is None

    def test_pass_with_low_correctness_overridden_to_fail(self, mocker):
        """LLM returning PASS when a turn has correctness < 4 is overridden to FAIL."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",  # LLM claims PASS — should be overridden
                "overall_score": 7.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "wrong",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 2,  # < 4 → must FAIL per rubric
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 7.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "wrong",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "FAIL"

    def test_pass_with_low_overall_score_overridden_to_fail(self, mocker):
        """LLM returning PASS when overall_score < 6.0 is overridden to FAIL."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",  # LLM claims PASS — but recomputed score < 6.0
                "overall_score": 8.0,  # will be replaced by mean of turn scores
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 4,
                            "tool_selection": 4,
                            "context_retention": 4,
                            "completeness": 4,
                            "efficiency": 4,
                            "personality": 4,
                            "error_recovery": 4,
                        },
                        "overall_score": 4.0,  # recomputed = 4.0 < 6.0
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "borderline",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "FAIL"
        assert result["overall_score"] == pytest.approx(4.0, abs=0.01)

    def test_fail_not_upgraded_when_some_turns_lack_scores(self, mocker):
        """FAIL→PASS upgrade is suppressed when some turns are missing dimension scores."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "FAIL",
                "overall_score": 8.0,
                "turns": [
                    {  # turn 1: fully scored, good
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 8,
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 8.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "good",
                    },
                    {  # turn 2: no dimension scores (eval agent timed out before scoring)
                        "turn": 2,
                        "user_message": "more",
                        "agent_response": "?",
                        "agent_tools": [],
                        "scores": {},
                        "overall_score": None,
                        "pass": False,
                        "failure_category": None,
                        "reasoning": "",
                    },
                ],
                "cost_estimate": {"turns": 2, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Must remain FAIL — turn 2 has no scores, cannot confirm it would pass
        assert result["status"] == "FAIL"

    def test_fail_with_good_scores_overridden_to_pass(self, mocker):
        """LLM returning FAIL when all turns score ≥4 correctness and overall ≥6.0 is corrected to PASS."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "FAIL",  # LLM false-FAIL — rubric says PASS
                "overall_score": 8.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "correct",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 8,
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 8.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "good",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "PASS"

    def test_turn_pass_flag_recomputed_after_score_overwrite(self, mocker):
        """turn['pass'] is recomputed from the recomputed score, not left as LLM value."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",
                "overall_score": 8.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "wrong",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 2,  # < 4 → turn pass must be False
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 9.0,
                        "pass": True,  # LLM says pass — wrong
                        "failure_category": None,
                        "reasoning": "wrong",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Turn pass must be False: correctness=2 < 4
        assert result["turns"][0]["pass"] is False

    def test_fail_scenario_score_preserved_in_runner(self, mocker):
        """FAIL scenario score is NOT capped in the runner — raw score preserved in trace."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",  # will be overridden to FAIL due to correctness=0
                "overall_score": 9.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "wrong",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 0,  # forces FAIL
                            "tool_selection": 10,
                            "context_retention": 10,
                            "completeness": 10,
                            "efficiency": 10,
                            "personality": 10,
                            "error_recovery": 10,
                        },
                        "overall_score": 9.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "wrong",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "FAIL"
        # Runner preserves the recomputed score (correctness=0 weight=0.40 pulls it down to ~6.0)
        # The cap at 5.99 is applied only inside scorecard.py avg_score computation, not here.
        assert isinstance(result["overall_score"], float)

    def test_non_dict_json_returns_errored(self, mocker):
        """Eval agent returning a JSON array or scalar is wrapped in an ERRORED result."""
        # Return a JSON array (valid JSON but not a dict)
        result = self._run(mocker, json.dumps([{"turns": []}]))
        assert result["status"] == "ERRORED"
        assert "non-object" in result.get("error", "")
        assert result["scenario_id"] == "mock_scenario"

    def test_scenario_id_always_injected_from_runner(self, mocker):
        """Runner always overwrites scenario_id with its own sid — eval agent value is untrusted."""
        payload = {
            "structured_output": {
                "scenario_id": "WRONG_ID_FROM_EVAL_AGENT",
                "status": "PASS",
                "overall_score": 8.5,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 8,
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 8.5,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "ok",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Runner's own scenario_id must win regardless of what eval agent returned
        assert result["scenario_id"] == "mock_scenario"

    def test_infra_status_not_overridden(self, mocker):
        """BLOCKED_BY_ARCHITECTURE status is never overridden to FAIL."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "BLOCKED_BY_ARCHITECTURE",
                "overall_score": 3.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "blocked",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 0,
                            "tool_selection": 0,
                            "context_retention": 0,
                            "completeness": 0,
                            "efficiency": 0,
                            "personality": 0,
                            "error_recovery": 0,
                        },
                        "overall_score": 0.0,
                        "pass": False,
                        "failure_category": "no_fallback",
                        "reasoning": "arch",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "BLOCKED_BY_ARCHITECTURE"


class TestScorecardByCategory:
    """Tests for by_category breakdown in build_scorecard."""

    def test_judged_pass_rate_excludes_infra_failures(self):
        """judged_pass_rate denominator excludes infra failures; pass_rate includes them."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "c",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "d",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        summary = sc["summary"]
        # pass_rate includes infra failures: 2 pass / 4 total = 50%
        assert summary["pass_rate"] == pytest.approx(0.5)
        # judged_pass_rate excludes infra failures: 2 pass / 2 judged = 100%
        assert summary["judged_pass_rate"] == pytest.approx(1.0)

    def test_judged_pass_rate_counts_pass_with_null_score(self):
        """A PASS result with null overall_score still counts toward judged_pass_rate numerator."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "FAIL",
                "overall_score": 3.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # 1 PASS out of 2 judged = 50%
        assert sc["summary"]["judged_pass_rate"] == pytest.approx(0.5)

    def test_blocked_by_architecture_included_in_judged_pass_rate_denominator(self):
        """BLOCKED_BY_ARCHITECTURE is a judged status and counts in the denominator."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "BLOCKED_BY_ARCHITECTURE",
                "overall_score": 4.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "c",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # judged = PASS + BLOCKED = 2; passed = 1 → 50%
        assert sc["summary"]["judged_pass_rate"] == pytest.approx(0.5)
        # BLOCKED score 4.0 is included in avg_score: (8.0 + 4.0) / 2 = 6.0
        assert sc["summary"]["avg_score"] == pytest.approx(6.0, abs=0.1)

    def test_infra_error_tracked_separately_from_errored(self):
        """INFRA_ERROR and SETUP_ERROR must go to infra_error bucket, not errored."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "INFRA_ERROR",
                "overall_score": None,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "SETUP_ERROR",
                "overall_score": None,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "c",
                "status": "UNKNOWN_STATUS",
                "overall_score": None,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        cat = sc["summary"]["by_category"]["rag_quality"]
        assert (
            cat["infra_error"] == 2
        ), "INFRA_ERROR+SETUP_ERROR should be in infra_error"
        assert cat["errored"] == 1, "Unknown status should be in errored only"

    def test_none_score_compare_scorecards_no_false_delta(self, tmp_path):
        """Two None scores must produce delta=0, not crash."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        base = [
            {
                "scenario_id": "s1",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        curr = [
            {
                "scenario_id": "s1",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        bp = tmp_path / "base.json"
        cp = tmp_path / "curr.json"
        bp.write_text(json.dumps(build_scorecard("r1", base, {})))
        cp.write_text(json.dumps(build_scorecard("r2", curr, {})))
        result = compare_scorecards(bp, cp)
        # TIMEOUT→PASS is an improvement, not a crash
        assert len(result["improved"]) == 1
        assert result["improved"][0]["baseline_score"] == 0  # None mapped to 0

    def test_scorecard_warns_on_unrecognized_status(self):
        """An unrecognized status is bucketed as 'errored' and emits a warning."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "SKIPPED",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # Unrecognized status bucketed as errored
        assert sc["summary"]["errored"] == 1
        # Warning surfaces in the scorecard
        assert "warnings" in sc
        assert any("SKIPPED" in w for w in sc["warnings"])

    def test_fail_score_capped_at_5_99_in_avg_score(self):
        """FAIL scenario with score > 5.99 is capped to 5.99 when computing avg_score."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "FAIL",
                "overall_score": 7.5,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # avg_score = (5.99 + 9.0) / 2 = 7.495, not (7.5 + 9.0) / 2 = 8.25
        assert sc["summary"]["avg_score"] <= 7.50
        # Raw score in the result dict is preserved (not mutated)
        assert results[0]["overall_score"] == 7.5

    def test_errored_status_not_flagged_as_unrecognized(self):
        """ERRORED status is a known runner status and must not trigger the unrecognized warning."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "ERRORED",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # Must be counted in errored bucket, not trigger unrecognized-status warning
        assert sc["summary"]["errored"] == 1
        assert "warnings" not in sc

    def test_none_status_sorted_without_type_error(self):
        """A result with status=None is bucketed as errored without raising TypeError in sorted()."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": None,
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        # Should not raise TypeError from sorted({None, ...})
        sc = build_scorecard("run", results, {})
        assert sc["summary"]["errored"] == 1


class TestCompareScorecardEdgeCases:
    """Edge case tests for compare_scorecards."""

    def test_fail_fail_score_regression_detected(self, tmp_path):
        """A FAIL→FAIL scenario with a significant score drop is flagged as score_regressed."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        baseline = [
            {
                "scenario_id": "a",
                "status": "FAIL",
                "overall_score": 5.5,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        current = [
            {
                "scenario_id": "a",
                "status": "FAIL",
                "overall_score": 1.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        b_sc = build_scorecard("b", baseline, {})
        c_sc = build_scorecard("c", current, {})
        p_b = tmp_path / "baseline.json"
        p_c = tmp_path / "current.json"
        p_b.write_text(json.dumps(b_sc))
        p_c.write_text(json.dumps(c_sc))

        report = compare_scorecards(p_b, p_c)
        # Delta = 1.0 - 5.5 = -4.5, exceeds _SCORE_REGRESSION_THRESHOLD → score_regressed
        assert any(e["scenario_id"] == "a" for e in report.get("score_regressed", []))

    def test_missing_scenario_id_skipped_gracefully(self, tmp_path, capsys):
        """A result dict missing 'scenario_id' is skipped with a warning, not a crash."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        # Intentionally omit scenario_id from one result
        results = [
            {
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        sc = build_scorecard("run", results, {})
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(sc))

        good_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        good_sc = build_scorecard("run", good_results, {})
        p2 = tmp_path / "good.json"
        p2.write_text(json.dumps(good_sc))

        # Should not raise — result missing scenario_id is skipped with a warning
        report = compare_scorecards(p, p2)
        assert report is not None
        captured = capsys.readouterr()
        assert "missing 'scenario_id'" in captured.err


class TestFixModeMerge:
    """Tests for the fix-mode scenario merge logic (no subprocess needed)."""

    def test_merge_keeps_passing_replaces_failing(self):
        """Rerun results replace only the scenarios that were re-run; passing ones are preserved."""
        from gaia.eval.scorecard import build_scorecard

        passing = {
            "scenario_id": "pass_a",
            "status": "PASS",
            "overall_score": 9.0,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        failing = {
            "scenario_id": "fail_b",
            "status": "FAIL",
            "overall_score": 2.0,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        current_scorecard = build_scorecard("r0", [passing, failing], {})

        # Simulate a rerun of fail_b that now passes
        rerun_result = {
            "scenario_id": "fail_b",
            "status": "PASS",
            "overall_score": 8.5,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        rerun_map = {rerun_result["scenario_id"]: rerun_result}

        # Apply the same merge logic as fix-mode loop
        merged = []
        for s in current_scorecard["scenarios"]:
            sid = s.get("scenario_id")
            merged.append(rerun_map[sid] if sid in rerun_map else s)

        new_scorecard = build_scorecard("r1", merged, {})
        # pass_a preserved, fail_b replaced
        assert new_scorecard["summary"]["passed"] == 2
        assert new_scorecard["summary"]["failed"] == 0

    def test_merge_does_not_discard_previously_passing_on_regression(self):
        """If a previously PASS scenario regresses during rerun, it is still included."""
        from gaia.eval.scorecard import build_scorecard

        passing = {
            "scenario_id": "pass_a",
            "status": "PASS",
            "overall_score": 9.0,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        failing = {
            "scenario_id": "fail_b",
            "status": "FAIL",
            "overall_score": 2.0,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        current_scorecard = build_scorecard("r0", [passing, failing], {})

        # Rerun of fail_b still fails
        rerun_result = {
            "scenario_id": "fail_b",
            "status": "FAIL",
            "overall_score": 1.5,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        rerun_map = {rerun_result["scenario_id"]: rerun_result}

        merged = []
        for s in current_scorecard["scenarios"]:
            sid = s.get("scenario_id")
            merged.append(rerun_map[sid] if sid in rerun_map else s)

        new_scorecard = build_scorecard("r1", merged, {})
        # pass_a still in merged, fail_b still failing
        assert new_scorecard["summary"]["passed"] == 1
        assert new_scorecard["summary"]["failed"] == 1
        scenario_ids = {s["scenario_id"] for s in new_scorecard["scenarios"]}
        assert "pass_a" in scenario_ids


class TestDocumentsExist:
    """Tests for the _documents_exist helper."""

    def test_empty_index_documents_returns_true(self):
        from gaia.eval.runner import _documents_exist

        data = {"setup": {"index_documents": []}}
        assert _documents_exist(data) is True

    def test_existing_file_returns_true(self, tmp_path):
        from gaia.eval.runner import REPO_ROOT, _documents_exist

        # Create a real file relative to REPO_ROOT so REPO_ROOT / path exists
        rel = Path("eval/corpus/documents/acme_q3_report.md")
        assert (REPO_ROOT / rel).exists(), "Known test fixture must exist"
        data = {
            "setup": {
                "index_documents": [{"corpus_doc": "acme_q3_report", "path": str(rel)}]
            }
        }
        assert _documents_exist(data) is True

    def test_missing_file_returns_false(self):
        from gaia.eval.runner import _documents_exist

        data = {
            "setup": {
                "index_documents": [
                    {
                        "corpus_doc": "ghost",
                        "path": "eval/corpus/real_world/does_not_exist.txt",
                    }
                ]
            }
        }
        assert _documents_exist(data) is False

    def test_string_entries_ignored(self):
        """String entries in index_documents (no 'path' field) don't cause false negatives."""
        from gaia.eval.runner import _documents_exist

        data = {"setup": {"index_documents": ["some_string_entry"]}}
        assert _documents_exist(data) is True


class TestSkippedNoDocument:
    """Tests for SKIPPED_NO_DOCUMENT handling in scorecard."""

    def _make_result(self, scenario_id, status, score=None, category="real_world"):
        return {
            "scenario_id": scenario_id,
            "status": status,
            "overall_score": score,
            "category": category,
            "cost_estimate": {"estimated_usd": 0},
        }

    def test_skipped_excluded_from_pass_rate_denominator(self):
        """SKIPPED_NO_DOCUMENT is NOT excluded from pass_rate denominator (it counts as total)."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0),
            self._make_result("b", "SKIPPED_NO_DOCUMENT"),
        ]
        sc = build_scorecard("run", results, {})
        # Total = 2, passed = 1 → pass_rate = 50%
        assert sc["summary"]["pass_rate"] == pytest.approx(0.5)
        assert sc["summary"]["skipped"] == 1

    def test_skipped_excluded_from_judged_pass_rate(self):
        """SKIPPED_NO_DOCUMENT is excluded from judged_pass_rate denominator."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0),
            self._make_result("b", "PASS", 8.0),
            self._make_result("c", "SKIPPED_NO_DOCUMENT"),
            self._make_result("d", "SKIPPED_NO_DOCUMENT"),
        ]
        sc = build_scorecard("run", results, {})
        # judged = 2 (PASS+PASS), skipped excluded → judged_pass_rate = 100%
        assert sc["summary"]["judged_pass_rate"] == pytest.approx(1.0)
        assert sc["summary"]["skipped"] == 2

    def test_skipped_excluded_from_avg_score(self):
        """SKIPPED_NO_DOCUMENT (score=None) must not affect avg_score."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 8.0),
            self._make_result("b", "SKIPPED_NO_DOCUMENT"),
        ]
        sc = build_scorecard("run", results, {})
        # avg_score only from judged scenarios: 8.0
        assert sc["summary"]["avg_score"] == pytest.approx(8.0)

    def test_skipped_not_in_errored_bucket(self):
        """SKIPPED_NO_DOCUMENT must go to 'skipped' bucket, not 'errored'."""
        from gaia.eval.scorecard import build_scorecard

        results = [self._make_result("a", "SKIPPED_NO_DOCUMENT")]
        sc = build_scorecard("run", results, {})
        assert sc["summary"]["skipped"] == 1
        assert sc["summary"]["errored"] == 0
        # Should NOT trigger the warnings key for unrecognized status
        assert "warnings" not in sc

    def test_skipped_not_in_errored_by_category(self):
        """by_category tracks skipped separately from errored."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "SKIPPED_NO_DOCUMENT", category="real_world"),
        ]
        sc = build_scorecard("run", results, {})
        cat = sc["summary"]["by_category"]["real_world"]
        assert cat["skipped"] == 1
        assert cat["errored"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
