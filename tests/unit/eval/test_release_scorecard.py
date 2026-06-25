# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TDD tests for gaia.eval.release_scorecard — written before implementation exists."""

import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gaia.eval.release_scorecard import (
    REQUIRED_FIELDS,
    ResultPayload,
    carry_forward,
    compute_aggregate,
    latest_version_below,
    parse_scorecard,
    render_scorecard,
    validate_scorecard,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "eval"
EMAIL_BENCHMARK_FIXTURE = FIXTURE_DIR / "email_benchmark_scorecard.json"


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


# ---------------------------------------------------------------------------
# 1. Schema / validator round-trip
# ---------------------------------------------------------------------------


class TestSchemaValidator:
    def test_valid_payload_passes_validation(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        errors = validate_scorecard(parsed)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_missing_required_fields_each_flagged(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)

        # Each required top-level field, when removed, should produce a non-empty error list.
        for field in REQUIRED_FIELDS:
            mutated = {k: v for k, v in parsed.items() if k != field}
            errors = validate_scorecard(mutated)
            assert errors, (
                f"Expected validate_scorecard to flag missing '{field}' "
                f"but got empty error list"
            )

    def test_required_top_level_keys_include_expected_sections(self):
        # schema_version, agent, recipe, results, aggregate must be required
        for section in ("schema_version", "agent", "recipe", "results", "aggregate"):
            assert section in REQUIRED_FIELDS, (
                f"'{section}' must be in REQUIRED_FIELDS"
            )


# ---------------------------------------------------------------------------
# 2. Aggregate computation
# ---------------------------------------------------------------------------


class TestComputeAggregate:
    def test_single_metric(self):
        _, value = compute_aggregate([{"name": "acc", "value": 0.5, "weight": 1.0}])
        assert value == 50.0

    def test_multiple_metrics_weighted(self):
        metrics = [
            {"name": "a", "value": 0.4167, "weight": 1.0},
            {"name": "b", "value": 0.5, "weight": 2.0},
        ]
        _, value = compute_aggregate(metrics)
        expected = round(100 * (0.4167 + 2 * 0.5) / (1 + 2), 2)
        assert value == expected

    def test_empty_metrics_raises(self):
        with pytest.raises(ValueError):
            compute_aggregate([])

    def test_zero_weight_raises(self):
        with pytest.raises(ValueError):
            compute_aggregate([{"name": "x", "value": 0.5, "weight": 0.0}])

    def test_recompute_from_components_matches_aggregate_value(self):
        metrics = [
            {"name": "cat_acc", "value": 0.4167, "weight": 1.0},
            {"name": "send_acc", "value": 0.75, "weight": 2.0},
        ]
        payload = _make_payload()
        # Build payload with these 2 metrics directly
        components, agg_value = compute_aggregate(metrics)
        recomputed = round(
            100
            * sum(c["weight"] * c["value"] for c in components)
            / sum(c["weight"] for c in components),
            2,
        )
        assert recomputed == agg_value


# ---------------------------------------------------------------------------
# 3. Generator round-trip
# ---------------------------------------------------------------------------


class TestGeneratorRoundTrip:
    def test_rendered_text_starts_with_dashes(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        lines = text.splitlines()
        assert lines[0] == "---", f"First line must be '---', got: {lines[0]!r}"

    def test_rendered_text_contains_closing_dashes(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        lines = text.splitlines()
        # Find second occurrence of '---'
        closing = [i for i, l in enumerate(lines) if l == "---" and i > 0]
        assert closing, "Rendered scorecard must contain a closing '---' after the first"

    def test_body_after_front_matter_is_non_empty(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        lines = text.splitlines()
        closing_indices = [i for i, l in enumerate(lines) if l == "---"]
        assert len(closing_indices) >= 2, "Need at least two '---' lines"
        body = "\n".join(lines[closing_indices[1] + 1 :])
        assert body.strip(), "Body after front matter must be non-empty"

    def test_parse_recovers_all_required_fields(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        errors = validate_scorecard(parsed)
        assert errors == []


# ---------------------------------------------------------------------------
# 4. Two counts distinct as separate fields
# ---------------------------------------------------------------------------


class TestDistinctCountFields:
    def test_test_cases_run_and_dataset_size_both_present(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        assert "results" in parsed, "'results' section missing from parsed scorecard"
        assert "test_cases_run" in parsed["results"], (
            "'results.test_cases_run' must be a distinct field"
        )
        assert "recipe" in parsed, "'recipe' section missing from parsed scorecard"
        assert "dataset" in parsed["recipe"], (
            "'recipe.dataset' sub-section missing"
        )
        assert "size" in parsed["recipe"]["dataset"], (
            "'recipe.dataset.size' must be a distinct field"
        )


# ---------------------------------------------------------------------------
# 5. Loose coupling — no harness/agent modules imported
# ---------------------------------------------------------------------------


class TestLooseCoupling:
    def test_no_benchmark_or_agent_modules_imported(self):
        # Import is already done at top of file; check sys.modules
        contaminated = [
            m
            for m in sys.modules
            if "benchmark" in m or "gaia_agent_email" in m
        ]
        assert not contaminated, (
            f"release_scorecard import pulled in harness/agent modules: {contaminated}"
        )


# ---------------------------------------------------------------------------
# 6. Markdown structure (duplicate guard on render)
# ---------------------------------------------------------------------------


class TestMarkdownStructure:
    def test_first_line_is_dashes(self):
        text = render_scorecard(_make_payload())
        assert text.splitlines()[0] == "---"

    def test_contains_closing_dashes(self):
        text = render_scorecard(_make_payload())
        count = text.count("\n---")
        assert count >= 1, "Must contain at least one closing '---' line"

    def test_body_non_empty(self):
        text = render_scorecard(_make_payload())
        parts = text.split("---")
        # parts[0] is empty, parts[1] is YAML, parts[2+] is body
        body = "---".join(parts[2:])
        assert body.strip(), "Markdown body after front matter must not be empty"


# ---------------------------------------------------------------------------
# 7. Versioning — patch carry-forward
# ---------------------------------------------------------------------------


class TestCarryForwardPatch:
    def test_carry_forward_sets_inherited_from(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "0.2.3.md"
        card_path.write_text(render_scorecard(src))

        result = carry_forward(card_path, "0.2.4")
        assert result.inherited_from == "0.2.3"

    def test_carry_forward_copies_metrics_verbatim(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "0.2.3.md"
        card_path.write_text(render_scorecard(src))

        result = carry_forward(card_path, "0.2.4")
        assert result.metrics == src.metrics


# ---------------------------------------------------------------------------
# 8. Versioning — minor bump refuses
# ---------------------------------------------------------------------------


class TestCarryForwardMinorBumpRefuses:
    def test_minor_bump_raises_value_error(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "0.2.3.md"
        card_path.write_text(render_scorecard(src))

        with pytest.raises(ValueError, match="re-run"):
            carry_forward(card_path, "0.3.0")

    def test_major_bump_raises_value_error(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "0.2.3.md"
        card_path.write_text(render_scorecard(src))

        with pytest.raises(ValueError, match="re-run"):
            carry_forward(card_path, "1.0.0")


# ---------------------------------------------------------------------------
# 9. Non-carry-forward card has inherited_from=None
# ---------------------------------------------------------------------------


class TestInheritedFromNone:
    def test_fresh_payload_has_null_inherited_from(self):
        payload = _make_payload()
        assert payload.inherited_from is None

    def test_rendered_parsed_inherited_from_null_or_absent(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        # Either key absent or value is None/null
        value = parsed.get("inherited_from", None)
        assert value is None


# ---------------------------------------------------------------------------
# 10. latest_version_below
# ---------------------------------------------------------------------------


class TestLatestVersionBelow:
    def _seed_dir(self, tmp_path):
        for name in ("0.1.0.md", "0.2.3.md", "0.10.0.md", "README.md", "not-a-version.md"):
            (tmp_path / name).write_text("# placeholder")
        return tmp_path

    def test_returns_closest_below(self, tmp_path):
        self._seed_dir(tmp_path)
        result = latest_version_below(tmp_path, "0.2.4")
        assert result == "0.2.3"

    def test_none_when_nothing_below(self, tmp_path):
        self._seed_dir(tmp_path)
        result = latest_version_below(tmp_path, "0.1.0")
        assert result is None

    def test_integer_comparison_not_string(self, tmp_path):
        self._seed_dir(tmp_path)
        result = latest_version_below(tmp_path, "0.10.1")
        assert result == "0.10.0"

    def test_non_version_files_silently_skipped(self, tmp_path):
        self._seed_dir(tmp_path)
        # Should not raise even with README.md and not-a-version.md present
        result = latest_version_below(tmp_path, "0.2.4")
        assert result == "0.2.3"


# ---------------------------------------------------------------------------
# Adapter tests: TestEmailAdapter
# ---------------------------------------------------------------------------


class TestEmailAdapter:
    """Tests for hub/agents/python/email/packaging/gen_scorecard.py adapter."""

    def _load_gen_scorecard(self):
        adapter_path = (
            Path(__file__).parents[3]
            / "hub"
            / "agents"
            / "python"
            / "email"
            / "packaging"
            / "gen_scorecard.py"
        )
        spec = importlib.util.spec_from_file_location("gen_scorecard", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_build_payload_mean_of_judged_scenarios(self, tmp_path):
        mod = self._load_gen_scorecard()

        # Copy fixture to a benchmark dir
        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        scorecard_dest = benchmark_dir / "email_benchmark_scorecard.json"
        scorecard_dest.write_text(EMAIL_BENCHMARK_FIXTURE.read_text())

        # Fake ground_truth.json with 3 keys (2 labeled + 1 _meta → dataset_size=2)
        ground_truth = {
            "_meta": {"count": 3},
            "email1": {"label": "spam"},
            "email2": {"label": "promo"},
        }
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps(ground_truth))

        payload = mod.build_payload(benchmark_dir, gt_path)

        expected_mean = round((0.4167 + 0.5000) / 2, 10)
        assert payload.metrics[0]["value"] == pytest.approx(expected_mean), (
            f"Expected metric value {expected_mean}, got {payload.metrics[0]['value']}"
        )

    def test_build_payload_test_cases_run(self, tmp_path):
        mod = self._load_gen_scorecard()

        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        scorecard_dest = benchmark_dir / "email_benchmark_scorecard.json"
        scorecard_dest.write_text(EMAIL_BENCHMARK_FIXTURE.read_text())

        ground_truth = {
            "_meta": {"count": 3},
            "email1": {"label": "spam"},
            "email2": {"label": "promo"},
        }
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps(ground_truth))

        payload = mod.build_payload(benchmark_dir, gt_path)
        # 12 + 12 = 24; third scenario skipped (no quality key)
        assert payload.test_cases_run == 24

    def test_build_payload_dataset_size(self, tmp_path):
        mod = self._load_gen_scorecard()

        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        scorecard_dest = benchmark_dir / "email_benchmark_scorecard.json"
        scorecard_dest.write_text(EMAIL_BENCHMARK_FIXTURE.read_text())

        ground_truth = {
            "_meta": {"count": 3},
            "email1": {"label": "spam"},
            "email2": {"label": "promo"},
        }
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps(ground_truth))

        payload = mod.build_payload(benchmark_dir, gt_path)
        # 3 keys - 1 _meta = 2
        assert payload.dataset_size == 2

    def test_all_no_quality_raises(self, tmp_path):
        mod = self._load_gen_scorecard()

        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        # Scorecard where no scenario has quality
        empty_scorecard = {
            "run_id": "no-quality",
            "scenarios": [
                {"category": "Gemma-4-E4B-it-GGUF", "status": "PASS", "total_emails": 0},
                {"category": "Gemma-4-E4B-it-GGUF", "status": "PASS", "total_emails": 0},
            ],
        }
        (benchmark_dir / "email_benchmark_scorecard.json").write_text(
            json.dumps(empty_scorecard)
        )

        ground_truth = {"_meta": {"count": 1}, "email1": {"label": "spam"}}
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps(ground_truth))

        with pytest.raises(ValueError):
            mod.build_payload(benchmark_dir, gt_path)
