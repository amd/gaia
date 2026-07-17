# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TDD tests for gaia.eval.scorecard_gate — new single-file SCORECARD.md interface."""

import datetime
from pathlib import Path

import yaml

from gaia.eval.release_scorecard import (
    ResultPayload,
    compute_aggregate,
    parse_scorecard,
    render_scorecard,
)
from gaia.eval.scorecard_gate import env_ctx_size, main

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
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        inherited_from=None,
    )


def _write_card(directory: Path, version: str, accuracy: float) -> Path:
    """Write a valid SCORECARD.md to directory/SCORECARD.md."""
    payload = _make_payload(version=version, accuracy=accuracy)
    path = directory / "SCORECARD.md"
    path.write_text(render_scorecard(payload))
    return path


def _write_card_named(path: Path, version: str, accuracy: float) -> Path:
    """Write a valid SCORECARD.md to an explicit path."""
    payload = _make_payload(version=version, accuracy=accuracy)
    path.write_text(render_scorecard(payload))
    return path


# ---------------------------------------------------------------------------
# Case (a) — missing card → exit 1
# ---------------------------------------------------------------------------


class TestMissingCard:
    def test_missing_card_returns_1(self, tmp_path):
        scorecard = tmp_path / "SCORECARD.md"
        result = main(["--scorecard", str(scorecard)])
        assert result == 1


# ---------------------------------------------------------------------------
# Case (b) — strict regression with --baseline-file → exit 1
# ---------------------------------------------------------------------------


class TestStrictRegression:
    def test_regression_returns_1(self, tmp_path):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        baseline = _write_card(baseline_dir, "0.2.3", accuracy=0.8)

        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        candidate = _write_card(candidate_dir, "0.2.4", accuracy=0.5)

        result = main(["--scorecard", str(candidate), "--baseline-file", str(baseline)])
        assert result == 1


# ---------------------------------------------------------------------------
# Case (c) — no baseline → presence-only pass → exit 0
# ---------------------------------------------------------------------------


class TestNoPrior:
    def test_first_adoption_returns_0(self, tmp_path):
        candidate = _write_card(tmp_path, "1.0.0", accuracy=0.6)
        result = main(["--scorecard", str(candidate)])
        assert result == 0


# ---------------------------------------------------------------------------
# Case (d) — equal score (carry-forward) with --baseline-file → exit 0
# ---------------------------------------------------------------------------


class TestEqualScore:
    def test_equal_score_returns_0(self, tmp_path):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        baseline = _write_card(baseline_dir, "0.2.3", accuracy=0.5)

        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        candidate = _write_card(candidate_dir, "0.2.4", accuracy=0.5)

        result = main(["--scorecard", str(candidate), "--baseline-file", str(baseline)])
        assert result == 0


# ---------------------------------------------------------------------------
# Case (e) — improved score → exit 0
# ---------------------------------------------------------------------------


class TestImprovedScore:
    def test_improved_score_returns_0(self, tmp_path):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        baseline = _write_card(baseline_dir, "0.2.3", accuracy=0.5)

        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        candidate = _write_card(candidate_dir, "0.2.4", accuracy=0.8)

        result = main(["--scorecard", str(candidate), "--baseline-file", str(baseline)])
        assert result == 0


# ---------------------------------------------------------------------------
# --allow-regression → exit 0
# ---------------------------------------------------------------------------


class TestAllowRegression:
    def test_allow_regression_flag_returns_0(self, tmp_path):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        baseline = _write_card(baseline_dir, "0.2.3", accuracy=0.8)

        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        candidate = _write_card(candidate_dir, "0.2.4", accuracy=0.5)

        result = main(
            [
                "--scorecard",
                str(candidate),
                "--baseline-file",
                str(baseline),
                "--allow-regression",
            ]
        )
        assert result == 0

    def test_allow_regression_prints_warning_line(self, tmp_path, capsys):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        baseline = _write_card(baseline_dir, "0.2.3", accuracy=0.8)

        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        candidate = _write_card(candidate_dir, "0.2.4", accuracy=0.5)

        main(
            [
                "--scorecard",
                str(candidate),
                "--baseline-file",
                str(baseline),
                "--allow-regression",
            ]
        )
        captured = capsys.readouterr()
        assert "::warning::" in captured.out


# ---------------------------------------------------------------------------
# --baseline-file missing → exit 1
# ---------------------------------------------------------------------------


class TestBaselineFileMissing:
    def test_missing_baseline_file_returns_1(self, tmp_path):
        candidate = _write_card(tmp_path, "1.0.0", accuracy=0.6)
        result = main(
            [
                "--scorecard",
                str(candidate),
                "--baseline-file",
                str(tmp_path / "nonexistent-SCORECARD.md"),
            ]
        )
        assert result == 1


# ---------------------------------------------------------------------------
# Invalid candidate (corrupt YAML front matter) → exit 1
# ---------------------------------------------------------------------------


class TestInvalidCandidate:
    def test_corrupt_candidate_returns_1(self, tmp_path):
        corrupt_path = tmp_path / "SCORECARD.md"
        corrupt_path.write_text("this is not valid yaml front matter at all\ngarbage\n")
        result = main(["--scorecard", str(corrupt_path)])
        assert result == 1

    def test_empty_candidate_returns_1(self, tmp_path):
        empty_path = tmp_path / "SCORECARD.md"
        empty_path.write_text("")
        result = main(["--scorecard", str(empty_path)])
        assert result == 1


# ---------------------------------------------------------------------------
# Invalid baseline → exit 1
# ---------------------------------------------------------------------------


class TestInvalidPrior:
    def test_corrupt_baseline_returns_1(self, tmp_path):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        corrupt = baseline_dir / "SCORECARD.md"
        corrupt.write_text("this is not valid yaml front matter at all\ngarbage\n")

        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        candidate = _write_card(candidate_dir, "0.2.4", accuracy=0.9)

        result = main(["--scorecard", str(candidate), "--baseline-file", str(corrupt)])
        assert result == 1

    def test_empty_baseline_returns_1(self, tmp_path):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        empty = baseline_dir / "SCORECARD.md"
        empty.write_text("")

        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        candidate = _write_card(candidate_dir, "0.2.4", accuracy=0.9)

        result = main(["--scorecard", str(candidate), "--baseline-file", str(empty)])
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
    def test_missing_scorecard_flag_returns_1(self):
        result = main([])
        assert result == 1

    def test_baseline_file_and_ref_mutually_exclusive(self, tmp_path):
        candidate = _write_card(tmp_path, "1.0.0", accuracy=0.6)
        result = main(
            [
                "--scorecard",
                str(candidate),
                "--baseline-file",
                str(candidate),
                "--baseline-ref",
                "v1.0.0",
            ]
        )
        assert result == 1


# ---------------------------------------------------------------------------
# Acceptance bar (#1437) + URGENT floor + variance-aware regression (#1894)
# ---------------------------------------------------------------------------


def _make_acceptance_card(
    path: Path,
    *,
    version: str,
    within_one: float,
    urgent_recall: float = 0.85,
    stdev: float | None = None,
    environment: dict | None = None,
) -> Path:
    """Write a SCORECARD.md whose gated aggregate is within-one-bucket, with an
    urgent_recall secondary and (optionally) a recorded within-one stdev and/or
    a run environment (e.g. {"ctx_size": 16384})."""
    metrics = [
        {"name": "within_one_bucket_accuracy", "value": within_one, "weight": 1.0},
        {"name": "urgent_recall", "value": urgent_recall, "weight": 0.0},
        {"name": "category_accuracy", "value": 0.42, "weight": 0.0},
    ]
    config = {"model": "test", "n_runs": 3}
    if stdev is not None:
        config["acceptance_variance"] = {
            "n_runs": 3,
            "within_one_bucket_accuracy": {"n": 3, "mean": within_one, "stdev": stdev},
        }
    payload = ResultPayload(
        agent_name="test-agent",
        agent_version=version,
        dataset_reference="test/fixture",
        dataset_description="test dataset",
        dataset_size=100,
        methodology="unit test",
        config=config,
        test_cases_run=100,
        metrics=metrics,
        aggregate_name="weighted_accuracy",
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        inherited_from=None,
    )
    if environment is not None:
        payload.environment = environment
    path.write_text(render_scorecard(payload))
    return path


class TestAbsoluteAcceptanceBar:
    def test_below_bar_fails(self, tmp_path):
        card = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.0", within_one=0.75
        )
        # 75.0 < 80 → block, even with no baseline (first adoption).
        assert main(["--scorecard", str(card), "--min-aggregate", "80"]) == 1

    def test_at_or_above_bar_passes(self, tmp_path):
        card = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.0", within_one=0.83
        )
        assert main(["--scorecard", str(card), "--min-aggregate", "80"]) == 0

    def test_no_min_aggregate_skips_bar(self, tmp_path):
        # Report mode: a low score still passes presence-only when no bar is set.
        card = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.0", within_one=0.10
        )
        assert main(["--scorecard", str(card)]) == 0


class TestUrgentRecallFloor:
    def test_below_floor_fails(self, tmp_path):
        card = _make_acceptance_card(
            tmp_path / "SCORECARD.md",
            version="0.3.0",
            within_one=0.90,
            urgent_recall=0.50,
        )
        # Aggregate passes (90) but urgent mail is buried (0.50 < 0.70) → block.
        assert (
            main(
                [
                    "--scorecard",
                    str(card),
                    "--min-aggregate",
                    "80",
                    "--min-urgent-recall",
                    "0.70",
                ]
            )
            == 1
        )

    def test_above_floor_passes(self, tmp_path):
        card = _make_acceptance_card(
            tmp_path / "SCORECARD.md",
            version="0.3.0",
            within_one=0.83,
            urgent_recall=0.78,
        )
        assert (
            main(
                [
                    "--scorecard",
                    str(card),
                    "--min-aggregate",
                    "80",
                    "--min-urgent-recall",
                    "0.70",
                ]
            )
            == 0
        )

    def test_floor_set_but_metric_absent_fails_loud(self, tmp_path):
        # A category-only card (no urgent_recall) must fail loud, not pass silently.
        card = _write_card(tmp_path, "0.3.0", accuracy=0.9)
        assert main(["--scorecard", str(card), "--min-urgent-recall", "0.70"]) == 1


class TestVarianceAwareRegression:
    def test_dip_within_noise_band_passes(self, tmp_path):
        # Baseline 84 ± stdev 0.02 (×100 = 2.0 band, k=1). Candidate 83 ≥ 84−2=82 → pass.
        base = _make_acceptance_card(
            tmp_path / "base.md", version="0.3.0", within_one=0.84, stdev=0.02
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.1", within_one=0.83, stdev=0.02
        )
        assert main(["--scorecard", str(cand), "--baseline-file", str(base)]) == 0

    def test_dip_beyond_noise_band_fails(self, tmp_path):
        # Candidate 80 < 84−2=82 → real regression, blocks.
        base = _make_acceptance_card(
            tmp_path / "base.md", version="0.3.0", within_one=0.84, stdev=0.02
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.1", within_one=0.80, stdev=0.02
        )
        assert main(["--scorecard", str(cand), "--baseline-file", str(base)]) == 1

    def test_no_baseline_stdev_uses_strict_check(self, tmp_path):
        # Baseline without variance → strict '<': 83 < 84 fails.
        base = _make_acceptance_card(
            tmp_path / "base.md", version="0.3.0", within_one=0.84
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.1", within_one=0.83
        )
        assert main(["--scorecard", str(cand), "--baseline-file", str(base)]) == 1

    def test_equal_score_carry_forward_passes(self, tmp_path):
        base = _make_acceptance_card(
            tmp_path / "base.md", version="0.3.0", within_one=0.84, stdev=0.02
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.1", within_one=0.84, stdev=0.02
        )
        assert main(["--scorecard", str(cand), "--baseline-file", str(base)]) == 0


# ---------------------------------------------------------------------------
# Ctx-size mismatch 4-state matrix (#1892): --require-ctx-match / --allow-ctx-mismatch
# ---------------------------------------------------------------------------


class TestCtxSizeMismatch:
    def test_ctx_match_passes_normally(self, tmp_path):
        # Same ctx_size on both sides → normal regression check runs, unaffected.
        base = _make_acceptance_card(
            tmp_path / "base.md",
            version="0.3.0",
            within_one=0.84,
            environment={"ctx_size": 16384},
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md",
            version="0.3.1",
            within_one=0.85,
            environment={"ctx_size": 16384},
        )
        assert main(["--scorecard", str(cand), "--baseline-file", str(base)]) == 0

    def test_ctx_mismatch_without_flag_fails(self, tmp_path):
        # Differing ctx_size, no override flag → not apples-to-apples: block.
        base = _make_acceptance_card(
            tmp_path / "base.md",
            version="0.3.0",
            within_one=0.84,
            environment={"ctx_size": 32768},
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md",
            version="0.3.1",
            within_one=0.85,
            environment={"ctx_size": 16384},
        )
        assert main(["--scorecard", str(cand), "--baseline-file", str(base)]) == 1

    def test_ctx_mismatch_with_allow_flag_skips_and_warns(self, tmp_path, capsys):
        # Differing ctx_size + --allow-ctx-mismatch → regression check skipped,
        # a warning is printed, gate exits 0 (candidate clears its own bars).
        base = _make_acceptance_card(
            tmp_path / "base.md",
            version="0.3.0",
            within_one=0.84,
            environment={"ctx_size": 32768},
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md",
            version="0.3.1",
            within_one=0.85,
            environment={"ctx_size": 16384},
        )
        result = main(
            [
                "--scorecard",
                str(cand),
                "--baseline-file",
                str(base),
                "--allow-ctx-mismatch",
            ]
        )
        assert result == 0
        captured = capsys.readouterr()
        assert "::warning::" in captured.out

    def test_allow_ctx_mismatch_does_not_bypass_absolute_bars(self, tmp_path):
        # --allow-ctx-mismatch only waives the ctx-comparison invalidity; the
        # absolute acceptance bar must still independently apply and can still
        # fail the gate even with the flag set.
        base = _make_acceptance_card(
            tmp_path / "base.md",
            version="0.3.0",
            within_one=0.84,
            environment={"ctx_size": 32768},
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md",
            version="0.3.1",
            within_one=0.85,
            environment={"ctx_size": 16384},
        )
        result = main(
            [
                "--scorecard",
                str(cand),
                "--baseline-file",
                str(base),
                "--allow-ctx-mismatch",
                "--min-aggregate",
                "95",
            ]
        )
        assert result == 1

    def test_baseline_lacks_ctx_transitional_pass(self, tmp_path):
        # Baseline predates ctx stamping (no environment at all); candidate has
        # ctx_size. Comparing an implicit-old card against an explicit-ctx
        # candidate is invalid, so the regression comparison is skipped — this
        # is the one-time transitional grace state and passes WITHOUT the
        # --allow-ctx-mismatch flag (distinct from the deliberate-mismatch case).
        base = _make_acceptance_card(
            tmp_path / "base.md", version="0.3.0", within_one=0.84
        )
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md",
            version="0.3.1",
            within_one=0.60,
            environment={"ctx_size": 16384},
        )
        assert main(["--scorecard", str(cand), "--baseline-file", str(base)]) == 0

    def test_candidate_lacks_ctx_with_require_flag_fails(self, tmp_path):
        # --require-ctx-match demands the candidate carry an explicit ctx_size;
        # a candidate with no environment.ctx_size at all fails hard.
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.1", within_one=0.85
        )
        assert main(["--scorecard", str(cand), "--require-ctx-match"]) == 1


# ---------------------------------------------------------------------------
# env_ctx_size — public helper (#2094): email_scorecard_refresh.yml's `pre`
# step imports this directly to read the committed card's ctx stamp before
# any eval spend, so it must be importable without the leading underscore.
# ---------------------------------------------------------------------------


class TestEnvCtxSizePublicHelper:
    def test_stamped_card_returns_ctx_size(self, tmp_path):
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md",
            version="0.3.1",
            within_one=0.85,
            environment={"ctx_size": 16384},
        )
        parsed = parse_scorecard(cand)
        assert env_ctx_size(parsed) == 16384

    def test_legacy_unstamped_card_returns_none(self, tmp_path):
        # The current real state of hub/agents/email/npm/SCORECARD.md
        # (#2094): committed before #1892's ctx envelope landed, so it carries
        # no recipe.environment.ctx_size at all.
        cand = _make_acceptance_card(
            tmp_path / "SCORECARD.md", version="0.3.0", within_one=0.834
        )
        parsed = parse_scorecard(cand)
        assert env_ctx_size(parsed) is None

    def test_environment_present_without_ctx_size_key_returns_none(self):
        # Defensive: an environment block that carries other keys but not
        # ctx_size must not be mistaken for a stamped card.
        parsed = {"recipe": {"environment": {"gpu": "stx-halo"}}}
        assert env_ctx_size(parsed) is None

    def test_non_dict_environment_returns_none(self):
        parsed = {"recipe": {"environment": None}}
        assert env_ctx_size(parsed) is None
