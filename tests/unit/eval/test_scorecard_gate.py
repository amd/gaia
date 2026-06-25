# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TDD tests for gaia.eval.scorecard_gate — written before implementation exists."""

import datetime
from pathlib import Path

import yaml

from gaia.eval.release_scorecard import (
    ResultPayload,
    compute_aggregate,
    render_scorecard,
)
from gaia.eval.scorecard_gate import main

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_payload(version="1.0.0", accuracy=0.5):
    metrics = [{"name": "category_accuracy", "value": accuracy, "weight": 1.0}]
    components, agg_value = compute_aggregate(metrics)
    return ResultPayload(
        agent_name="test-agent",
        agent_version=version,
        dataset_reference="test/fixture",
        dataset_description="test dataset",
        dataset_size=100,
        methodology="unit test",
        config={"model": "test"},
        test_cases_run=10,
        metrics=metrics,
        aggregate_name="weighted_accuracy",
        generated_at=datetime.datetime.utcnow().isoformat(),
        inherited_from=None,
    )


def _write_card(directory: Path, version: str, accuracy: float) -> Path:
    payload = _make_payload(version=version, accuracy=accuracy)
    path = directory / f"{version}.md"
    path.write_text(render_scorecard(payload))
    return path


# ---------------------------------------------------------------------------
# Case (a) — missing card → exit 1
# ---------------------------------------------------------------------------


class TestMissingCard:
    def test_missing_card_returns_1(self, tmp_path):
        result = main(["--scorecards-dir", str(tmp_path), "--version", "1.0.0"])
        assert result == 1


# ---------------------------------------------------------------------------
# Case (b) — strict regression → exit 1
# ---------------------------------------------------------------------------


class TestStrictRegression:
    def test_regression_returns_1(self, tmp_path):
        _write_card(tmp_path, "0.2.3", accuracy=0.8)
        _write_card(tmp_path, "0.2.4", accuracy=0.5)
        result = main(["--scorecards-dir", str(tmp_path), "--version", "0.2.4"])
        assert result == 1


# ---------------------------------------------------------------------------
# Case (c) — no prior → exit 0
# ---------------------------------------------------------------------------


class TestNoPrior:
    def test_first_adoption_returns_0(self, tmp_path):
        _write_card(tmp_path, "1.0.0", accuracy=0.6)
        result = main(["--scorecards-dir", str(tmp_path), "--version", "1.0.0"])
        assert result == 0


# ---------------------------------------------------------------------------
# Case (d) — equal score (carry-forward) → exit 0
# ---------------------------------------------------------------------------


class TestEqualScore:
    def test_equal_score_returns_0(self, tmp_path):
        _write_card(tmp_path, "0.2.3", accuracy=0.5)
        _write_card(tmp_path, "0.2.4", accuracy=0.5)
        result = main(["--scorecards-dir", str(tmp_path), "--version", "0.2.4"])
        assert result == 0


# ---------------------------------------------------------------------------
# --allow-regression → exit 0
# ---------------------------------------------------------------------------


class TestAllowRegression:
    def test_allow_regression_flag_returns_0(self, tmp_path):
        _write_card(tmp_path, "0.2.3", accuracy=0.8)
        _write_card(tmp_path, "0.2.4", accuracy=0.5)
        result = main(
            [
                "--scorecards-dir",
                str(tmp_path),
                "--version",
                "0.2.4",
                "--allow-regression",
            ]
        )
        assert result == 0

    def test_allow_regression_prints_warning_line(self, tmp_path, capsys):
        _write_card(tmp_path, "0.2.3", accuracy=0.8)
        _write_card(tmp_path, "0.2.4", accuracy=0.5)
        main(
            [
                "--scorecards-dir",
                str(tmp_path),
                "--version",
                "0.2.4",
                "--allow-regression",
            ]
        )
        captured = capsys.readouterr()
        assert "::warning::" in captured.out


# ---------------------------------------------------------------------------
# --manifest reads version
# ---------------------------------------------------------------------------


class TestManifestFlag:
    def test_manifest_reads_version(self, tmp_path):
        scorecards_dir = tmp_path / "scorecards"
        scorecards_dir.mkdir()
        _write_card(scorecards_dir, "1.2.3", accuracy=0.6)

        manifest_path = tmp_path / "gaia-agent.yaml"
        manifest_path.write_text("version: 1.2.3\nname: test-agent\n")

        result = main(
            [
                "--scorecards-dir",
                str(scorecards_dir),
                "--manifest",
                str(manifest_path),
            ]
        )
        assert result == 0

    def test_manifest_with_regression(self, tmp_path):
        scorecards_dir = tmp_path / "scorecards"
        scorecards_dir.mkdir()
        _write_card(scorecards_dir, "1.2.2", accuracy=0.9)
        _write_card(scorecards_dir, "1.2.3", accuracy=0.3)

        manifest_path = tmp_path / "gaia-agent.yaml"
        manifest_path.write_text("version: 1.2.3\nname: test-agent\n")

        result = main(
            [
                "--scorecards-dir",
                str(scorecards_dir),
                "--manifest",
                str(manifest_path),
            ]
        )
        assert result == 1


# ---------------------------------------------------------------------------
# Invalid prior → exit 1
# ---------------------------------------------------------------------------


class TestInvalidPrior:
    def test_corrupt_prior_returns_1(self, tmp_path):
        # Write corrupt/invalid prior card
        corrupt_path = tmp_path / "0.2.3.md"
        corrupt_path.write_text("this is not valid yaml front matter at all\ngarbage\n")

        # Write a valid candidate card
        _write_card(tmp_path, "0.2.4", accuracy=0.9)

        result = main(["--scorecards-dir", str(tmp_path), "--version", "0.2.4"])
        assert result == 1

    def test_empty_prior_returns_1(self, tmp_path):
        # Prior exists but is empty
        empty_path = tmp_path / "0.2.3.md"
        empty_path.write_text("")

        _write_card(tmp_path, "0.2.4", accuracy=0.9)

        result = main(["--scorecards-dir", str(tmp_path), "--version", "0.2.4"])
        assert result == 1


# ---------------------------------------------------------------------------
# Workflow YAML test: publish job must list scorecard-gate in needs
# ---------------------------------------------------------------------------


class TestWorkflowYaml:
    def test_publish_job_needs_scorecard_gate(self):
        workflow_path = (
            Path(__file__).parents[3]
            / ".github"
            / "workflows"
            / "release_agent_email.yml"
        )
        assert workflow_path.exists(), f"Workflow file not found: {workflow_path}"
        content = workflow_path.read_text()
        parsed = yaml.safe_load(content)

        assert "jobs" in parsed, "Workflow has no 'jobs' key"
        assert (
            "publish" in parsed["jobs"]
        ), "Workflow has no 'publish' job — add it or check the job name"
        needs = parsed["jobs"]["publish"].get("needs", [])
        # needs can be a string or a list
        if isinstance(needs, str):
            needs = [needs]
        assert (
            "scorecard-gate" in needs
        ), f"'publish' job must list 'scorecard-gate' in its needs; got: {needs}"


# ---------------------------------------------------------------------------
# Error handling — bad CLI input returns 1 (not exception)
# ---------------------------------------------------------------------------


class TestCliErrorHandling:
    def test_missing_scorecards_dir_flag_returns_1(self):
        result = main(["--version", "1.0.0"])
        assert result == 1

    def test_missing_version_and_manifest_returns_1(self, tmp_path):
        result = main(["--scorecards-dir", str(tmp_path)])
        assert result == 1
