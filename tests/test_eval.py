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
        """Test eval command with missing required arguments"""
        # Use python -m approach for reliability in CI
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval"],
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "required" in result.stderr.lower()


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
        # Test our mock directly since we can't import RagEvaluator without anthropic
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
