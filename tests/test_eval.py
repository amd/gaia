#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit and integration tests for the evaluation tool"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


class MockLLMClient:
    """Mock LLM client for deterministic testing"""

    def __init__(self, model_name="mock-model"):
        self.model_name = model_name
        self.call_count = 0

    def complete(self, prompt, max_tokens=1000):
        """Return predefined responses based on prompt content"""
        self.call_count += 1

        # Mock responses for different evaluation scenarios
        if "evaluate the quality" in prompt.lower():
            return self._mock_evaluation_response()
        elif "analyze" in prompt.lower():
            return self._mock_analysis_response()
        else:
            return "Mock response for: " + prompt[:50]

    def _mock_evaluation_response(self):
        """Mock response for quality evaluation"""
        return json.dumps(
            {
                "quality_score": 8,
                "quality_rating": "good",
                "explanation": "The summary captures the main points effectively.",
                "strengths": ["Clear structure", "Good coverage"],
                "weaknesses": ["Minor details missing"],
                "recommendations": ["Add more specific examples"],
            }
        )

    def _mock_analysis_response(self):
        """Mock response for analysis"""
        return json.dumps(
            {
                "analysis": "Mock analysis of the content",
                "key_points": ["Point 1", "Point 2"],
                "score": 7.5,
            }
        )


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

        # If files exist and were processed successfully, that's also valid behavior
        if result.returncode == 0:
            # Success case - files were found and processed
            combined_output = (result.stdout + result.stderr).lower()
            # Should show some indication of processing
            assert any(
                word in combined_output
                for word in ["found", "processing", "evaluations", "skipping"]
            )
        else:
            # Error case - no files found or other error
            combined_output = (result.stdout + result.stderr).lower()
            # Should show an error message about missing files or directory
            assert any(
                word in combined_output for word in ["no", "not found", "error", "❌"]
            )


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

    def test_evaluate_summary_quality(self):
        """Test summary quality evaluation with mocked Claude"""
        # Test our mock directly since we can't import Evaluator without anthropic
        mock_client = MockLLMClient()
        response = mock_client.complete("evaluate the quality of this summary")
        result = json.loads(response)

        assert result["quality_score"] == 8
        assert result["quality_rating"] == "good"
        assert len(result["strengths"]) > 0
        assert len(result["weaknesses"]) > 0
        assert len(result["recommendations"]) > 0

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

    @pytest.mark.skipif(
        not os.environ.get("RUN_INTEGRATION_TESTS"),
        reason="Integration tests disabled by default",
    )
    def test_eval_end_to_end(self, tmp_path):
        """Test end-to-end evaluation flow with mock data"""
        # This test would run the full evaluation pipeline with mock data
        # Skipped by default to keep tests fast
        pass

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
        f.write_text("async def handle():\n    agent = ChatAgent(config)\n    return agent\n")
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
        assert len(ids) == len(set(ids)), f"Duplicate scenario IDs: {[x for x in ids if ids.count(x) > 1]}"

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
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard
        import json

        def _sc(results):
            sc = build_scorecard("run", results, {})
            p = tmp_path / f"{id(results)}.json"
            p.write_text(json.dumps(sc))
            return p

        baseline_results = [
            {"scenario_id": "a", "status": "PASS", "overall_score": 9.0, "category": "rag_quality", "cost_estimate": {"estimated_usd": 0}},
            {"scenario_id": "b", "status": "PASS", "overall_score": 8.0, "category": "rag_quality", "cost_estimate": {"estimated_usd": 0}},
        ]
        current_results = [
            {"scenario_id": "a", "status": "PASS", "overall_score": 9.0, "category": "rag_quality", "cost_estimate": {"estimated_usd": 0}},
            {"scenario_id": "b", "status": "FAIL", "overall_score": 3.0, "category": "rag_quality", "cost_estimate": {"estimated_usd": 0}},
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
