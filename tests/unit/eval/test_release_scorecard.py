# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TDD tests for gaia.eval.release_scorecard — written before implementation exists."""

import datetime
import importlib.util
import json
from pathlib import Path

import pytest

from gaia.eval.release_scorecard import (
    REQUIRED_FIELDS,
    ResultPayload,
    carry_forward,
    compute_aggregate,
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
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
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
            assert section in REQUIRED_FIELDS, f"'{section}' must be in REQUIRED_FIELDS"

    def test_missing_nested_aggregate_value_flagged(self):
        payload = _make_payload()
        parsed = parse_scorecard(render_scorecard(payload))
        # Complete card stays valid
        assert validate_scorecard(parsed) == []
        # Removing a nested required field flags it
        del parsed["aggregate"]["value"]
        errors = validate_scorecard(parsed)
        assert errors, "Expected missing 'aggregate.value' to be flagged"
        assert any("aggregate.value" in e for e in errors), errors

    def test_missing_nested_agent_version_flagged(self):
        payload = _make_payload()
        parsed = parse_scorecard(render_scorecard(payload))
        del parsed["agent"]["version"]
        errors = validate_scorecard(parsed)
        assert errors, "Expected missing 'agent.version' to be flagged"
        assert any("agent.version" in e for e in errors), errors

    def test_missing_nested_dataset_size_flagged(self):
        payload = _make_payload()
        parsed = parse_scorecard(render_scorecard(payload))
        del parsed["recipe"]["dataset"]["size"]
        errors = validate_scorecard(parsed)
        assert any("recipe.dataset.size" in e for e in errors), errors

    def test_empty_metrics_list_flagged(self):
        payload = _make_payload()
        parsed = parse_scorecard(render_scorecard(payload))
        parsed["results"]["metrics"] = []
        errors = validate_scorecard(parsed)
        assert any("metrics" in e for e in errors), errors

    def test_non_dict_section_flagged_not_crash(self):
        payload = _make_payload()
        parsed = parse_scorecard(render_scorecard(payload))
        parsed["agent"] = "not-a-dict"
        errors = validate_scorecard(parsed)
        assert errors, "Expected a non-dict 'agent' section to be flagged"

    @pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_aggregate_value_flagged(self, bad_value):
        # A NaN/inf aggregate.value silently passes the gate's `<` regression
        # check, so validation must reject it.
        payload = _make_payload()
        parsed = parse_scorecard(render_scorecard(payload))
        parsed["aggregate"]["value"] = bad_value
        errors = validate_scorecard(parsed)
        assert any("aggregate.value" in e for e in errors), errors

    def test_non_numeric_aggregate_value_flagged(self):
        payload = _make_payload()
        parsed = parse_scorecard(render_scorecard(payload))
        parsed["aggregate"]["value"] = "46.0"
        errors = validate_scorecard(parsed)
        assert any("aggregate.value" in e for e in errors), errors


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
        assert (
            closing
        ), "Rendered scorecard must contain a closing '---' after the first"

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

    def test_body_contains_reproduction_section(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        assert "## Reproduction" in text

    def test_reproduction_section_includes_custom_command(self):
        payload = _make_payload()
        payload.reproduction_command = "gaia eval benchmark --limit 25"
        text = render_scorecard(payload)
        assert "gaia eval benchmark --limit 25" in text

    def test_reproduction_section_generic_when_no_command(self):
        payload = _make_payload()
        # No reproduction_command (default None)
        text = render_scorecard(payload)
        assert "## Reproduction" in text
        assert "eval-scorecard" in text


# ---------------------------------------------------------------------------
# 4. Two counts distinct as separate fields
# ---------------------------------------------------------------------------


class TestDistinctCountFields:
    def test_test_cases_run_and_dataset_size_both_present(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        assert "results" in parsed, "'results' section missing from parsed scorecard"
        assert (
            "test_cases_run" in parsed["results"]
        ), "'results.test_cases_run' must be a distinct field"
        assert "recipe" in parsed, "'recipe' section missing from parsed scorecard"
        assert "dataset" in parsed["recipe"], "'recipe.dataset' sub-section missing"
        assert (
            "size" in parsed["recipe"]["dataset"]
        ), "'recipe.dataset.size' must be a distinct field"


# ---------------------------------------------------------------------------
# 5. Loose coupling — no harness/agent modules imported
# ---------------------------------------------------------------------------


class TestLooseCoupling:
    def test_no_benchmark_or_agent_modules_imported(self):
        # Importing release_scorecard must not pull in the eval harness or any
        # agent package. Run in a fresh subprocess and baseline sys.modules
        # BEFORE the import, so we measure only what the import itself adds —
        # not pytest plugins or editable-install path finders that the
        # interpreter registers at startup regardless of any import.
        import subprocess
        import sys as _sys

        code = (
            "import sys; "
            "before=set(sys.modules); "
            "import gaia.eval.release_scorecard; "
            "added=set(sys.modules)-before; "
            "bad=[m for m in added if 'gaia.eval.benchmark' in m "
            "or 'gaia.eval.quality_metrics' in m or 'gaia_agent_email' in m]; "
            "assert not bad, bad"
        )
        r = subprocess.run(
            [_sys.executable, "-c", code], capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr


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
# 7. Versioning — patch carry-forward (SCORECARD.md is a single file)
# ---------------------------------------------------------------------------


class TestCarryForwardPatch:
    def test_carry_forward_sets_inherited_from(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "SCORECARD.md"
        card_path.write_text(render_scorecard(src))

        result = carry_forward(card_path, "0.2.4")
        assert result.inherited_from == "0.2.3"

    def test_carry_forward_copies_metrics_verbatim(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "SCORECARD.md"
        card_path.write_text(render_scorecard(src))

        result = carry_forward(card_path, "0.2.4")
        assert result.metrics == src.metrics

    def test_carry_forward_preserves_breakdown_and_environment(self, tmp_path):
        # "Carried forward verbatim" must include the optional blocks — a patch
        # release should not silently shed the breakdown or run environment.
        src = _make_payload(version="0.2.3", accuracy=0.75)
        src.breakdown = {
            "per_category": [
                {"category": "fyi", "total": 6, "correct": 4, "accuracy": 0.6667}
            ],
            "top_confusions": [
                {"expected": "fyi", "predicted": "needs_response", "count": 2}
            ],
        }
        src.environment = {
            "gaia_commit": "abc1234",
            "lemonade_version": "10.7.0",
            "model": "Gemma-4-E4B-it-GGUF",
            "hardware": "AMD Ryzen AI MAX+ (Strix Halo)",
        }
        card_path = tmp_path / "SCORECARD.md"
        card_path.write_text(render_scorecard(src))

        result = carry_forward(card_path, "0.2.4")
        assert result.breakdown == src.breakdown
        assert result.environment == src.environment

    def test_carry_forward_reads_version_from_front_matter(self, tmp_path):
        # The new carry_forward reads agent.version from front matter, NOT filename.
        src = _make_payload(version="0.2.3", accuracy=0.75)
        # Use a different filename to confirm it's not read from stem
        card_path = tmp_path / "SCORECARD.md"
        card_path.write_text(render_scorecard(src))

        result = carry_forward(card_path, "0.2.4")
        assert result.agent_version == "0.2.4"
        assert result.inherited_from == "0.2.3"


# ---------------------------------------------------------------------------
# 8. Versioning — minor bump refuses
# ---------------------------------------------------------------------------


class TestCarryForwardMinorBumpRefuses:
    def test_minor_bump_raises_value_error(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "SCORECARD.md"
        card_path.write_text(render_scorecard(src))

        with pytest.raises(ValueError, match="re-run"):
            carry_forward(card_path, "0.3.0")

    def test_major_bump_raises_value_error(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "SCORECARD.md"
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
# 10. Gate integration: second-agent generalization (no fabricated artifacts)
# ---------------------------------------------------------------------------


class TestSecondAgentGeneralization:
    """Prove the generator + gate work for an agent OTHER than email-triage."""

    def test_second_agent_scorecard_validates_and_gate_passes(self, tmp_path):
        from gaia.eval.scorecard_gate import main as gate_main

        # Build a ResultPayload for a different agent
        metrics = [{"name": "accuracy", "value": 0.75, "weight": 1.0}]
        payload = ResultPayload(
            agent_name="Hello World Agent",
            agent_version="0.1.0",
            dataset_reference="tests/fixtures/hello/ground_truth.json",
            dataset_description="Hello world evaluation dataset",
            dataset_size=50,
            methodology="exact match accuracy",
            config={"model": "Gemma-4-E4B-it-GGUF", "limit": 20},
            test_cases_run=20,
            metrics=metrics,
            aggregate_name="weighted_accuracy",
            generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            inherited_from=None,
            reproduction_command="gaia eval agent --category hello",
        )

        scorecard_path = tmp_path / "SCORECARD.md"
        from gaia.eval.release_scorecard import write_scorecard

        write_scorecard(payload, scorecard_path)

        # Validate the written scorecard
        text = scorecard_path.read_text()
        parsed = parse_scorecard(text)
        errors = validate_scorecard(parsed)
        assert errors == [], f"Second-agent scorecard should be valid, got: {errors}"

        # Gate should pass (no baseline → presence-only)
        result = gate_main(["--scorecard", str(scorecard_path)])
        assert result == 0, "Gate should pass for a valid second-agent SCORECARD.md"


# ---------------------------------------------------------------------------
# Adapter tests: TestEmailAdapter
# ---------------------------------------------------------------------------


class TestEmailAdapter:
    """Tests for hub/agents/email/python/packaging/gen_scorecard.py adapter."""

    def _load_gen_scorecard(self):
        adapter_path = (
            Path(__file__).parents[3]
            / "hub"
            / "agents"
            / "email"
            / "python"
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

        # The gated aggregate is within-one-bucket = mean across runs (#1437):
        # (0.8333 + 0.9167) / 2 = 0.875. Exact category_accuracy is a secondary.
        assert payload.metrics[0]["name"] == "within_one_bucket_accuracy"
        assert payload.metrics[0]["weight"] == 1.0
        expected_mean = round((0.8333 + 0.9167) / 2, 4)
        assert payload.metrics[0]["value"] == pytest.approx(expected_mean)

    def test_secondaries_are_displayed_weight_zero(self, tmp_path):
        # urgent-vs-not, urgent-recall, category_accuracy are shown but excluded
        # from the aggregate (weight 0), so aggregate == 100 × within-one (#1862).
        mod = self._load_gen_scorecard()
        from gaia.eval.release_scorecard import compute_aggregate

        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        (benchmark_dir / "email_benchmark_scorecard.json").write_text(
            EMAIL_BENCHMARK_FIXTURE.read_text()
        )
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps({"_meta": {}, "a": {"label": "x"}}))

        payload = mod.build_payload(benchmark_dir, gt_path)
        names = {m["name"]: m["weight"] for m in payload.metrics}
        assert names["within_one_bucket_accuracy"] == 1.0
        assert names["urgent_vs_not_accuracy"] == 0.0
        assert names["urgent_recall"] == 0.0
        assert names["category_accuracy"] == 0.0
        _components, agg = compute_aggregate(payload.metrics)
        assert agg == pytest.approx(round(100 * 0.875, 2))

    def test_quality_json_preferred_and_variance_recorded(self, tmp_path):
        # When the harness wrote quality.json (the aggregate + variance/CI #1894),
        # the adapter reads it and records the variance + n_runs in config.
        mod = self._load_gen_scorecard()

        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        (benchmark_dir / "email_benchmark_scorecard.json").write_text(
            EMAIL_BENCHMARK_FIXTURE.read_text()
        )
        variance = {
            "n_runs": 2,
            "within_one_bucket_accuracy": {
                "n": 2,
                "mean": 0.84,
                "stdev": 0.02,
                "ci95_low": 0.81,
                "ci95_high": 0.87,
            },
        }
        quality = {
            "within_one_bucket_accuracy": 0.84,
            "urgent_vs_not_accuracy": 0.88,
            "urgent_recall": 0.72,
            "category_accuracy": 0.46,
            "acceptance_variance": variance,
        }
        (benchmark_dir / "quality.json").write_text(json.dumps(quality))
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps({"_meta": {}, "a": {"label": "x"}}))

        payload = mod.build_payload(benchmark_dir, gt_path)
        # quality.json wins over per-scenario means.
        assert payload.metrics[0]["value"] == pytest.approx(0.84)
        assert payload.config["acceptance_variance"] == variance
        assert payload.config["n_runs"] == 2

    def test_old_output_without_acceptance_metric_raises(self, tmp_path):
        # A benchmark run predating the acceptance metric (only category_accuracy,
        # no quality.json) must fail loud — never silently default to exact-only.
        mod = self._load_gen_scorecard()

        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        legacy = {
            "run_id": "legacy",
            "scenarios": [
                {
                    "category": "m",
                    "status": "PASS",
                    "total_emails": 10,
                    "quality": {"category_accuracy": 0.42},
                }
            ],
        }
        (benchmark_dir / "email_benchmark_scorecard.json").write_text(
            json.dumps(legacy)
        )
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps({"_meta": {}, "a": {"label": "x"}}))

        with pytest.raises(ValueError, match="acceptance metric"):
            mod.build_payload(benchmark_dir, gt_path)

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
        # Two experiments over the SAME 12-email corpus → per-run count is 12,
        # NOT 24 (summing runs would conflate experiments with cases). n_runs=2.
        assert payload.test_cases_run == 12
        assert payload.config["n_runs"] == 2

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
                {
                    "category": "Gemma-4-E4B-it-GGUF",
                    "status": "PASS",
                    "total_emails": 0,
                },
                {
                    "category": "Gemma-4-E4B-it-GGUF",
                    "status": "PASS",
                    "total_emails": 0,
                },
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

    def test_build_payload_includes_reproduction_command(self, tmp_path):
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

        payload = mod.build_payload(benchmark_dir, gt_path, limit=25)
        assert payload.reproduction_command is not None
        assert "gaia eval benchmark" in payload.reproduction_command
        assert "gen_scorecard.py" in payload.reproduction_command
        assert "PYTHON_KEYRING_BACKEND" in payload.reproduction_command
        # The corpus is generated (not committed), so the recipe MUST build it
        # from the seed first — a fresh checkout fails otherwise.
        assert "generate_mbox.py" in payload.reproduction_command
        assert 'uv pip install -e ".[dev,eval,api]"' in payload.reproduction_command
        # Points readers to the standalone eval guide for background/examples.
        assert "EVALUATION.md" in payload.reproduction_command

    def _bench_and_gt(self, tmp_path):
        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        (benchmark_dir / "email_benchmark_scorecard.json").write_text(
            EMAIL_BENCHMARK_FIXTURE.read_text()
        )
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps({"_meta": {}, "a": {"label": "x"}}))
        return benchmark_dir, gt_path

    def test_drafting_report_folds_reported_metric(self, tmp_path):
        # A judged drafting report adds draft_approval_rate as a REPORTED metric
        # (weight 0) without changing the aggregate (still 100 x within_one).
        from gaia.eval.release_scorecard import compute_aggregate

        mod = self._load_gen_scorecard()
        bench, gt = self._bench_and_gt(tmp_path)
        report = tmp_path / "drafting_gate_report.json"
        report.write_text(
            json.dumps({"summary": {"drafting": {"draft_approval_rate": 0.73}}})
        )

        base = mod.build_payload(bench, gt)
        withd = mod.build_payload(bench, gt, drafting_report=str(report))

        names = {m["name"]: m["weight"] for m in withd.metrics}
        assert names.get("draft_approval_rate") == 0.0
        draft = next(m for m in withd.metrics if m["name"] == "draft_approval_rate")
        assert draft["value"] == pytest.approx(0.73)
        # Aggregate unchanged — drafting is reported, not weighted.
        assert compute_aggregate(withd.metrics)[1] == compute_aggregate(base.metrics)[1]

    def test_drafting_report_skip_marker_fails_loud(self, tmp_path):
        # No silent skip (CLAUDE.md fail-loudly): the judged drafting eval now
        # hard-fails on a missing key instead of emitting a skip report, so a
        # legacy `skipped` marker must raise — never silently omit the metric.
        mod = self._load_gen_scorecard()
        bench, gt = self._bench_and_gt(tmp_path)
        report = tmp_path / "drafting_gate_report.json"
        report.write_text(json.dumps({"skipped": True, "reason": "no key"}))

        with pytest.raises(ValueError, match="skipped"):
            mod.build_payload(bench, gt, drafting_report=str(report))

    def test_drafting_report_malformed_fails_loud(self, tmp_path):
        # A non-skip report missing the rate is a hard error, never a silent omit.
        mod = self._load_gen_scorecard()
        bench, gt = self._bench_and_gt(tmp_path)
        report = tmp_path / "drafting_gate_report.json"
        report.write_text(json.dumps({"summary": {"drafting": {}}}))

        with pytest.raises(ValueError, match="draft_approval_rate"):
            mod.build_payload(bench, gt, drafting_report=str(report))

    def _load_eval_drafting_report(self):
        path = (
            Path(__file__).parents[3]
            / "hub"
            / "agents"
            / "email"
            / "python"
            / "packaging"
            / "eval_drafting_report.py"
        )
        spec = importlib.util.spec_from_file_location("eval_drafting_report", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_drafting_eval_missing_key_hard_fails(self, tmp_path, monkeypatch, capsys):
        # No silent skip (CLAUDE.md fail-loudly): a missing ANTHROPIC_API_KEY makes
        # the judged drafting eval exit 1 with an actionable error naming the key —
        # it must NOT write a skip report or return 0.
        mod = self._load_eval_drafting_report()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EMAIL_EVAL_MODEL", "test-model")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        rc = mod.main()

        assert rc == 1
        err = capsys.readouterr().err
        assert "ANTHROPIC_API_KEY" in err
        # Never emits a skip report the scorecard could silently omit.
        assert not (tmp_path / "eval-out" / "drafting_gate_report.json").exists()


# ---------------------------------------------------------------------------
# 11. Task 1: breakdown round-trip (release_scorecard core)
# ---------------------------------------------------------------------------


class TestBreakdownRoundTrip:
    """breakdown field: render→parse round-trip, absence, aggregate invariant."""

    def _make_breakdown(self):
        return {
            "per_category": [
                {"category": "fyi", "total": 50, "correct": 30, "accuracy": 0.6},
                {"category": "spam", "total": 20, "correct": 18, "accuracy": 0.9},
            ],
            "top_confusions": [
                {"expected": "fyi", "predicted": "needs_response", "count": 12},
                {"expected": "spam", "predicted": "fyi", "count": 2},
            ],
        }

    def test_breakdown_present_in_front_matter(self):
        payload = _make_payload()
        payload.breakdown = self._make_breakdown()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        assert (
            "breakdown" in parsed["results"]
        ), "'breakdown' must appear under results in front matter when set"
        bd = parsed["results"]["breakdown"]
        assert "per_category" in bd
        cats = [r["category"] for r in bd["per_category"]]
        assert cats == ["fyi", "spam"]

    def test_breakdown_absent_when_none(self):
        payload = _make_payload()
        # default breakdown is None
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        assert "breakdown" not in parsed.get(
            "results", {}
        ), "'breakdown' must not appear in front matter when payload.breakdown is None"

    def test_breakdown_body_section_rendered_when_present(self):
        payload = _make_payload()
        payload.breakdown = self._make_breakdown()
        text = render_scorecard(payload)
        assert "## Category breakdown" in text

    def test_breakdown_body_section_absent_when_none(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        assert "## Category breakdown" not in text

    def test_breakdown_body_table_has_correct_columns(self):
        payload = _make_payload()
        payload.breakdown = self._make_breakdown()
        text = render_scorecard(payload)
        assert "Category" in text
        assert "Total" in text
        assert "Correct" in text
        assert "Accuracy" in text

    def test_breakdown_body_contains_top_confusions(self):
        payload = _make_payload()
        payload.breakdown = self._make_breakdown()
        text = render_scorecard(payload)
        assert "fyi" in text
        assert "needs_response" in text
        assert "12" in text

    def test_breakdown_validate_still_passes(self):
        payload = _make_payload()
        payload.breakdown = self._make_breakdown()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        errors = validate_scorecard(parsed)
        assert errors == [], f"validate_scorecard failed with breakdown set: {errors}"

    def test_breakdown_not_in_required_fields(self):
        assert (
            "breakdown" not in REQUIRED_FIELDS
        ), "'breakdown' must remain optional (not in REQUIRED_FIELDS)"

    def test_aggregate_value_unchanged_with_breakdown(self):
        """Breakdown must not affect aggregate.value — descriptive only."""
        base = _make_payload(accuracy=0.46)
        base_text = render_scorecard(base)
        base_parsed = parse_scorecard(base_text)
        base_agg = base_parsed["aggregate"]["value"]

        with_bd = _make_payload(accuracy=0.46)
        with_bd.breakdown = {
            "per_category": [
                {"category": "fyi", "total": 10, "correct": 5, "accuracy": 0.5}
            ],
            "top_confusions": [],
        }
        bd_text = render_scorecard(with_bd)
        bd_parsed = parse_scorecard(bd_text)
        bd_agg = bd_parsed["aggregate"]["value"]

        assert (
            base_agg == bd_agg
        ), f"Aggregate changed when breakdown was added: {base_agg} → {bd_agg}"


# ---------------------------------------------------------------------------
# 12. Task 2: environment round-trip (release_scorecard core)
# ---------------------------------------------------------------------------


class TestPerformanceRoundTrip:
    """performance field: render→parse round-trip, absence, validate invariant."""

    def _make_perf(self):
        return {
            "ttft_s": 8.541,
            "throughput_tps": 12.1,
            "pipeline_s": 1894.0,
            "peak_memory_gb": 6.2,
            "emails_per_run": 20,
        }

    def test_performance_present_in_front_matter(self):
        payload = _make_payload()
        payload.performance = self._make_perf()
        parsed = parse_scorecard(render_scorecard(payload))
        assert "performance" in parsed.get(
            "results", {}
        ), "'performance' must appear under results in front matter when set"
        assert parsed["results"]["performance"]["throughput_tps"] == 12.1

    def test_performance_absent_when_none(self):
        parsed = parse_scorecard(render_scorecard(_make_payload()))
        assert "performance" not in parsed.get("results", {})

    def test_performance_body_section_rendered_when_present(self):
        payload = _make_payload()
        payload.performance = self._make_perf()
        text = render_scorecard(payload)
        assert "## Performance" in text
        assert "throughput_tps" in text and "8.541" in text

    def test_performance_body_section_absent_when_none(self):
        assert "## Performance" not in render_scorecard(_make_payload())

    def test_performance_not_in_required_fields(self):
        assert "performance" not in REQUIRED_FIELDS

    def test_performance_validate_still_passes(self):
        payload = _make_payload()
        payload.performance = self._make_perf()
        parsed = parse_scorecard(render_scorecard(payload))
        assert validate_scorecard(parsed) == []

    def test_performance_does_not_affect_aggregate(self):
        base = parse_scorecard(render_scorecard(_make_payload(accuracy=0.46)))
        withp = _make_payload(accuracy=0.46)
        withp.performance = self._make_perf()
        withp_parsed = parse_scorecard(render_scorecard(withp))
        assert base["aggregate"]["value"] == withp_parsed["aggregate"]["value"]

    def test_carry_forward_preserves_performance(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        src.performance = self._make_perf()
        card = tmp_path / "SCORECARD.md"
        card.write_text(render_scorecard(src))
        result = carry_forward(card, "0.2.4")
        assert result.performance is not None
        assert result.performance["throughput_tps"] == 12.1


class TestCapabilityQualityRoundTrip:
    """capability_quality: render→parse round-trip, absence, validate invariant."""

    def _make_capq(self):
        return {
            "spam": {"precision": 0.92, "recall": 0.88, "f1": 0.9},
            "action_items": {"precision": 0.8, "recall": 0.75, "f1": 0.77},
            "briefing": {
                "approval": 0.95,
                "must_include_recall": 0.9,
                "faithful": 1.0,
                "hallucination_free": 1.0,
            },
        }

    def test_capq_present_in_front_matter(self):
        payload = _make_payload()
        payload.capability_quality = self._make_capq()
        parsed = parse_scorecard(render_scorecard(payload))
        assert "capability_quality" in parsed.get("results", {})
        assert parsed["results"]["capability_quality"]["spam"]["f1"] == 0.9

    def test_capq_absent_when_none(self):
        parsed = parse_scorecard(render_scorecard(_make_payload()))
        assert "capability_quality" not in parsed.get("results", {})

    def test_capq_body_section_rendered_when_present(self):
        payload = _make_payload()
        payload.capability_quality = self._make_capq()
        text = render_scorecard(payload)
        assert "## Capability quality" in text
        assert "spam" in text and "action_items" in text and "briefing" in text
        assert "0.9200" in text  # spam precision rendered with 4dp

    def test_capq_body_section_absent_when_none(self):
        assert "## Capability quality" not in render_scorecard(_make_payload())

    def test_capq_not_in_required_fields(self):
        assert "capability_quality" not in REQUIRED_FIELDS

    def test_capq_validate_still_passes(self):
        payload = _make_payload()
        payload.capability_quality = self._make_capq()
        parsed = parse_scorecard(render_scorecard(payload))
        assert validate_scorecard(parsed) == []

    def test_capq_does_not_affect_aggregate(self):
        base = parse_scorecard(render_scorecard(_make_payload(accuracy=0.46)))
        withq = _make_payload(accuracy=0.46)
        withq.capability_quality = self._make_capq()
        withq_parsed = parse_scorecard(render_scorecard(withq))
        assert base["aggregate"]["value"] == withq_parsed["aggregate"]["value"]

    def test_carry_forward_preserves_capq(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        src.capability_quality = self._make_capq()
        card = tmp_path / "SCORECARD.md"
        card.write_text(render_scorecard(src))
        result = carry_forward(card, "0.2.4")
        assert result.capability_quality is not None
        assert result.capability_quality["briefing"]["approval"] == 0.95


class TestEnvironmentRoundTrip:
    """environment field: render→parse round-trip, absence, validate invariant."""

    def _make_env(self):
        return {
            "gaia_commit": "abc1234",
            "lemonade_version": "10.8.0",
            "model": "Gemma-4-E4B-it-GGUF",
            "hardware": "AMD Ryzen AI MAX+ (Strix Halo)",
        }

    def test_environment_present_in_front_matter(self):
        payload = _make_payload()
        payload.environment = self._make_env()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        assert "environment" in parsed.get(
            "recipe", {}
        ), "'environment' must appear under recipe in front matter when set"
        env = parsed["recipe"]["environment"]
        assert env["gaia_commit"] == "abc1234"
        assert env["model"] == "Gemma-4-E4B-it-GGUF"

    def test_environment_absent_when_none(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        assert "environment" not in parsed.get(
            "recipe", {}
        ), "'environment' must not appear in front matter when payload.environment is None"

    def test_environment_body_section_rendered_when_present(self):
        payload = _make_payload()
        payload.environment = self._make_env()
        text = render_scorecard(payload)
        assert "## Environment" in text

    def test_environment_body_section_absent_when_none(self):
        payload = _make_payload()
        text = render_scorecard(payload)
        assert "## Environment" not in text

    def test_environment_body_table_has_field_value_columns(self):
        payload = _make_payload()
        payload.environment = self._make_env()
        text = render_scorecard(payload)
        assert "| Field" in text or "| field" in text.lower()
        assert "| Value" in text or "| value" in text.lower()

    def test_environment_validate_still_passes(self):
        payload = _make_payload()
        payload.environment = self._make_env()
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        errors = validate_scorecard(parsed)
        assert errors == [], f"validate_scorecard failed with environment set: {errors}"

    def test_environment_not_in_required_fields(self):
        assert (
            "environment" not in REQUIRED_FIELDS
        ), "'environment' must remain optional (not in REQUIRED_FIELDS)"

    def test_environment_round_trips_all_keys(self):
        env = self._make_env()
        env["temperature"] = 0.0
        payload = _make_payload()
        payload.environment = env
        parsed = parse_scorecard(render_scorecard(payload))
        recovered = parsed["recipe"]["environment"]
        assert recovered["temperature"] == 0.0
        assert recovered["hardware"] == "AMD Ryzen AI MAX+ (Strix Halo)"


# ---------------------------------------------------------------------------
# 13. Task 1 adapter: breakdown computation (gen_scorecard.py)
# ---------------------------------------------------------------------------


_RICHER_SCORECARD = {
    "run_id": "breakdown-fixture",
    "scenarios": [
        {
            "category": "Gemma-4-E4B-it-GGUF",
            "status": "PASS",
            "total_emails": 10,
            "quality": {
                "category_accuracy": 0.6,
                "within_one_bucket_accuracy": 0.7,
                "urgent_vs_not_accuracy": 0.8,
                "urgent_recall": 0.75,
                "categorization": {
                    "axis": "needs_attention",
                    "rows": [
                        # 6 correct fyi, 2 correct spam, 2 wrong (fyi→needs_response)
                        {
                            "id": "e1",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "e2",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "e3",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "e4",
                            "predicted": "needs_response",
                            "expected": "fyi",
                            "category_correct": False,
                        },
                        {
                            "id": "e5",
                            "predicted": "needs_response",
                            "expected": "fyi",
                            "category_correct": False,
                        },
                        {
                            "id": "e6",
                            "predicted": "spam",
                            "expected": "spam",
                            "category_correct": True,
                        },
                        {
                            "id": "e7",
                            "predicted": "spam",
                            "expected": "spam",
                            "category_correct": True,
                        },
                        {
                            "id": "e8",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "e9",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "e10",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                    ],
                    "false_positives": [],
                    "false_negatives": ["e4", "e5"],
                    "summary": {},
                },
            },
        },
        {
            "category": "Gemma-4-E4B-it-GGUF",
            "status": "PASS",
            "total_emails": 20,
            "quality": {
                "category_accuracy": 0.75,
                "within_one_bucket_accuracy": 0.8,
                "urgent_vs_not_accuracy": 0.85,
                "urgent_recall": 0.8,
                "categorization": {
                    "axis": "needs_attention",
                    "rows": [
                        # 15 correct: 10 fyi correct, 5 spam correct; 5 wrong: spam→fyi
                        {
                            "id": "f1",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f2",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f3",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f4",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f5",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f6",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f7",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f8",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f9",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f10",
                            "predicted": "fyi",
                            "expected": "fyi",
                            "category_correct": True,
                        },
                        {
                            "id": "f11",
                            "predicted": "spam",
                            "expected": "spam",
                            "category_correct": True,
                        },
                        {
                            "id": "f12",
                            "predicted": "spam",
                            "expected": "spam",
                            "category_correct": True,
                        },
                        {
                            "id": "f13",
                            "predicted": "spam",
                            "expected": "spam",
                            "category_correct": True,
                        },
                        {
                            "id": "f14",
                            "predicted": "spam",
                            "expected": "spam",
                            "category_correct": True,
                        },
                        {
                            "id": "f15",
                            "predicted": "spam",
                            "expected": "spam",
                            "category_correct": True,
                        },
                        {
                            "id": "f16",
                            "predicted": "fyi",
                            "expected": "spam",
                            "category_correct": False,
                        },
                        {
                            "id": "f17",
                            "predicted": "fyi",
                            "expected": "spam",
                            "category_correct": False,
                        },
                        {
                            "id": "f18",
                            "predicted": "fyi",
                            "expected": "spam",
                            "category_correct": False,
                        },
                        {
                            "id": "f19",
                            "predicted": "fyi",
                            "expected": "spam",
                            "category_correct": False,
                        },
                        {
                            "id": "f20",
                            "predicted": "fyi",
                            "expected": "spam",
                            "category_correct": False,
                        },
                    ],
                    "false_positives": [],
                    "false_negatives": [],
                    "summary": {},
                },
            },
        },
    ],
}


class TestBreakdownAdapter:
    """Adapter tests for build_payload breakdown computation."""

    def _load_gen_scorecard(self):
        adapter_path = (
            Path(__file__).parents[3]
            / "hub"
            / "agents"
            / "email"
            / "python"
            / "packaging"
            / "gen_scorecard.py"
        )
        spec = importlib.util.spec_from_file_location("gen_scorecard", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _make_benchmark_dir(self, tmp_path, scorecard_data):
        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        (benchmark_dir / "scorecard.json").write_text(json.dumps(scorecard_data))
        return benchmark_dir

    def _make_gt(self, tmp_path):
        gt = {"a": {"label": "x"}, "b": {"label": "y"}}
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps(gt))
        return gt_path

    def test_performance_extracted_from_summary(self, tmp_path):
        """build_payload folds performance_summary into a versioned perf block."""
        mod = self._load_gen_scorecard()
        scorecard = {
            "run_id": "perf",
            "scenarios": [
                {
                    "category": "Gemma-4-E4B-it-GGUF",
                    "status": "PASS",
                    "total_emails": 20,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                    "performance_summary": {
                        "avg_time_to_first_token": 8.5,
                        "avg_tokens_per_second": 12.1,
                        "pipeline_latency_s": 760.0,
                        "peak_memory_gb": 6.2,
                        "total_emails": 20,
                    },
                }
            ],
        }
        bd = self._make_benchmark_dir(tmp_path, scorecard)
        payload = mod.build_payload(bd, self._make_gt(tmp_path))
        assert payload.performance is not None
        assert payload.performance["ttft_s"] == 8.5
        assert payload.performance["throughput_tps"] == 12.1
        assert payload.performance["pipeline_s"] == 760.0
        assert payload.performance["peak_memory_gb"] == 6.2
        assert payload.performance["emails_per_run"] == 20
        # It reaches the rendered card so it shows on the hub.
        assert "## Performance" in render_scorecard(payload)

    def test_performance_drops_unmeasured_memory(self, tmp_path):
        """peak_memory_gb=0.0 (runner /stats omits it) is dropped, not shown as 0."""
        mod = self._load_gen_scorecard()
        scorecard = {
            "run_id": "nomem",
            "scenarios": [
                {
                    "category": "m",
                    "status": "PASS",
                    "total_emails": 20,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                    "performance_summary": {
                        "avg_time_to_first_token": 8.5,
                        "avg_tokens_per_second": 12.1,
                        "pipeline_latency_s": 760.0,
                        "peak_memory_gb": 0.0,
                        "total_emails": 20,
                    },
                }
            ],
        }
        bd = self._make_benchmark_dir(tmp_path, scorecard)
        payload = mod.build_payload(bd, self._make_gt(tmp_path))
        assert payload.performance is not None
        assert "peak_memory_gb" not in payload.performance
        assert payload.performance["throughput_tps"] == 12.1

    def test_performance_folds_triage_token_metrics(self, tmp_path):
        """Increment 2: tokens_per_triage / llm_classified_count / token totals
        are meaned into payload.performance alongside the existing perf keys."""
        mod = self._load_gen_scorecard()
        scorecard = {
            "run_id": "tokens",
            "scenarios": [
                {
                    "category": "Gemma-4-E4B-it-GGUF",
                    "status": "PASS",
                    "total_emails": 20,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                    "performance_summary": {
                        "avg_time_to_first_token": 8.5,
                        "avg_tokens_per_second": 12.1,
                        "pipeline_latency_s": 760.0,
                        "peak_memory_gb": 6.2,
                        "total_emails": 20,
                        "tokens_per_triage": 1450.0,
                        "llm_classified_count": 4,
                        "total_input_tokens": 6000,
                        "total_output_tokens": 1000,
                    },
                }
            ],
        }
        bd = self._make_benchmark_dir(tmp_path, scorecard)
        payload = mod.build_payload(bd, self._make_gt(tmp_path))
        assert payload.performance is not None
        assert payload.performance["tokens_per_triage"] == 1450.0
        assert payload.performance["llm_classified_count"] == 4
        assert payload.performance["total_input_tokens"] == 6000.0
        assert payload.performance["total_output_tokens"] == 1000.0

    def test_performance_drops_unmeasured_triage_token_metrics(self, tmp_path):
        """No triage-token keys in performance_summary -> none of the four new
        keys appear in payload.performance (drop-if-absent, mirrors the
        existing peak_memory_gb=0.0 drop test)."""
        mod = self._load_gen_scorecard()
        scorecard = {
            "run_id": "no-tokens",
            "scenarios": [
                {
                    "category": "m",
                    "status": "PASS",
                    "total_emails": 20,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                    "performance_summary": {
                        "avg_time_to_first_token": 8.5,
                        "avg_tokens_per_second": 12.1,
                        "pipeline_latency_s": 760.0,
                        "peak_memory_gb": 6.2,
                        "total_emails": 20,
                    },
                }
            ],
        }
        bd = self._make_benchmark_dir(tmp_path, scorecard)
        payload = mod.build_payload(bd, self._make_gt(tmp_path))
        assert payload.performance is not None
        assert "tokens_per_triage" not in payload.performance
        assert "llm_classified_count" not in payload.performance
        assert "total_input_tokens" not in payload.performance
        assert "total_output_tokens" not in payload.performance
        assert payload.performance["throughput_tps"] == 12.1

    def test_performance_means_tokens_per_triage_across_scenarios(self, tmp_path):
        """Two judged scenarios with different tokens_per_triage -> the mean."""
        mod = self._load_gen_scorecard()

        def _scenario(tpt):
            return {
                "category": "Gemma-4-E4B-it-GGUF",
                "status": "PASS",
                "total_emails": 20,
                "quality": {
                    "category_accuracy": 0.5,
                    "within_one_bucket_accuracy": 0.8,
                },
                "performance_summary": {
                    "avg_time_to_first_token": 8.5,
                    "avg_tokens_per_second": 12.1,
                    "pipeline_latency_s": 760.0,
                    "peak_memory_gb": 6.2,
                    "total_emails": 20,
                    "tokens_per_triage": tpt,
                    "llm_classified_count": 4,
                },
            }

        scorecard = {
            "run_id": "tokens-mean",
            "scenarios": [_scenario(1400.0), _scenario(1500.0)],
        }
        bd = self._make_benchmark_dir(tmp_path, scorecard)
        payload = mod.build_payload(bd, self._make_gt(tmp_path))
        assert payload.performance["tokens_per_triage"] == 1450.0

    def test_performance_none_when_no_summary(self, tmp_path):
        """No performance_summary in any scenario -> perf block omitted."""
        mod = self._load_gen_scorecard()
        scorecard = {
            "run_id": "noperf",
            "scenarios": [
                {
                    "category": "m",
                    "status": "PASS",
                    "total_emails": 10,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                }
            ],
        }
        bd = self._make_benchmark_dir(tmp_path, scorecard)
        payload = mod.build_payload(bd, self._make_gt(tmp_path))
        assert payload.performance is None

    def _scorecard_with_spam(self, precision, recall, f1):
        return {
            "run_id": "spam",
            "scenarios": [
                {
                    "category": "Gemma-4-E4B-it-GGUF",
                    "status": "PASS",
                    "total_emails": 20,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                        "spam": {
                            "precision": precision,
                            "recall": recall,
                            "f1": f1,
                        },
                    },
                }
            ],
        }

    def test_spam_folded_into_capability_quality(self, tmp_path):
        """is_spam P/R/F1 from the benchmark quality block reaches the card."""
        mod = self._load_gen_scorecard()
        bd = self._make_benchmark_dir(
            tmp_path, self._scorecard_with_spam(0.9, 0.8, 0.85)
        )
        payload = mod.build_payload(bd, self._make_gt(tmp_path))
        assert payload.capability_quality is not None
        assert payload.capability_quality["spam"] == {
            "precision": 0.9,
            "recall": 0.8,
            "f1": 0.85,
        }
        assert "## Capability quality" in render_scorecard(payload)

    def test_capability_quality_none_without_spam_or_reports(self, tmp_path):
        """No spam block and no report args -> capability_quality omitted."""
        mod = self._load_gen_scorecard()
        scorecard = {
            "run_id": "nocapq",
            "scenarios": [
                {
                    "category": "m",
                    "status": "PASS",
                    "total_emails": 10,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                }
            ],
        }
        bd = self._make_benchmark_dir(tmp_path, scorecard)
        payload = mod.build_payload(bd, self._make_gt(tmp_path))
        assert payload.capability_quality is None

    def test_action_item_and_briefing_reports_folded(self, tmp_path):
        """--action-item-report and --briefing-report metrics reach the card."""
        mod = self._load_gen_scorecard()
        ai = tmp_path / "ai.json"
        ai.write_text(
            json.dumps(
                {
                    "summary": {
                        "extraction": {"precision": 0.8, "recall": 0.7, "f1": 0.75}
                    }
                }
            )
        )
        br = tmp_path / "br.json"
        br.write_text(
            json.dumps(
                {
                    "summary": {
                        "briefing": {
                            "briefing_approval_rate": 0.95,
                            "must_include_recall_mean": 0.9,
                            "faithful_rate": 1.0,
                            "hallucination_free_rate": 1.0,
                        }
                    }
                }
            )
        )
        bd = self._make_benchmark_dir(
            tmp_path, self._scorecard_with_spam(0.9, 0.8, 0.85)
        )
        payload = mod.build_payload(
            bd,
            self._make_gt(tmp_path),
            action_item_report=str(ai),
            briefing_report=str(br),
        )
        capq = payload.capability_quality
        assert capq["action_items"] == {"precision": 0.8, "recall": 0.7, "f1": 0.75}
        assert capq["briefing"]["approval"] == 0.95
        assert capq["briefing"]["hallucination_free"] == 1.0

    def test_report_fails_loud_on_skipped(self, tmp_path):
        """A report marked skipped raises rather than silently omitting the metric."""
        mod = self._load_gen_scorecard()
        rep = tmp_path / "skipped.json"
        rep.write_text(json.dumps({"skipped": True}))
        with pytest.raises(ValueError, match="skipped"):
            mod._load_report_metrics(rep, "extraction", {"f1": "f1"})

    def test_report_fails_loud_on_missing_key(self, tmp_path):
        """A judged report missing a mapped source key raises (no silent omit)."""
        mod = self._load_gen_scorecard()
        rep = tmp_path / "partial.json"
        rep.write_text(json.dumps({"summary": {"extraction": {"precision": 0.8}}}))
        with pytest.raises(ValueError, match="summary.extraction.f1"):
            mod._load_report_metrics(rep, "extraction", {"f1": "f1"})

    def test_per_category_accuracy_fyi(self, tmp_path):
        """fyi: 6+10=16 total, 6+10=16 correct in scenario1+2 combined."""
        mod = self._load_gen_scorecard()
        bd = tmp_path / "bdir"
        bd.mkdir()
        (bd / "scorecard.json").write_text(json.dumps(_RICHER_SCORECARD))
        gt_path = self._make_gt(tmp_path)
        payload = mod.build_payload(bd, gt_path)

        assert payload.breakdown is not None
        cats = {r["category"]: r for r in payload.breakdown["per_category"]}
        assert "fyi" in cats
        # scenario1: 8 fyi total (e1-e3,e8-e10,e4,e5 → 3 correct from the True rows + 3=6 fyi→correct)
        # Actually counting: e1,e2,e3,e8,e9,e10 = 6 correct fyi; e4,e5 = 2 wrong (expected fyi)
        # scenario1 fyi: total=8 (e1-e5,e8-e10), correct=6
        # scenario2 fyi: total=10 (f1-f10), correct=10
        # combined fyi: total=18, correct=16
        assert cats["fyi"]["total"] == 18
        assert cats["fyi"]["correct"] == 16
        assert cats["fyi"]["accuracy"] == pytest.approx(round(16 / 18, 4))

    def test_per_category_accuracy_spam(self, tmp_path):
        """spam: scenario1 has 2 correct spam; scenario2 has 5 correct + 5 wrong."""
        mod = self._load_gen_scorecard()
        bd = tmp_path / "bdir"
        bd.mkdir()
        (bd / "scorecard.json").write_text(json.dumps(_RICHER_SCORECARD))
        gt_path = self._make_gt(tmp_path)
        payload = mod.build_payload(bd, gt_path)

        cats = {r["category"]: r for r in payload.breakdown["per_category"]}
        # scenario1 spam: e6,e7 = 2 total, 2 correct
        # scenario2 spam: f11-f20 = 10 total (5 correct spam, 5 wrong→predicted fyi)
        # combined spam: total=12, correct=7
        assert cats["spam"]["total"] == 12
        assert cats["spam"]["correct"] == 7
        assert cats["spam"]["accuracy"] == pytest.approx(round(7 / 12, 4))

    def test_top_confusions_populated(self, tmp_path):
        """Top confusions must include the (fyi, needs_response) pair from scenario1."""
        mod = self._load_gen_scorecard()
        bd = tmp_path / "bdir"
        bd.mkdir()
        (bd / "scorecard.json").write_text(json.dumps(_RICHER_SCORECARD))
        gt_path = self._make_gt(tmp_path)
        payload = mod.build_payload(bd, gt_path)

        confusions = payload.breakdown["top_confusions"]
        # (expected=fyi, predicted=needs_response): 2 in scenario1
        fyi_to_needs = next(
            (
                c
                for c in confusions
                if c["expected"] == "fyi" and c["predicted"] == "needs_response"
            ),
            None,
        )
        assert (
            fyi_to_needs is not None
        ), "Expected fyi→needs_response confusion not found"
        assert fyi_to_needs["count"] == 2

    def test_headline_accuracy_unchanged_with_breakdown(self, tmp_path):
        """Headline aggregate = within-one-bucket mean across runs, breakdown-independent."""
        mod = self._load_gen_scorecard()
        bd = tmp_path / "bdir"
        bd.mkdir()
        (bd / "scorecard.json").write_text(json.dumps(_RICHER_SCORECARD))
        gt_path = self._make_gt(tmp_path)
        payload = mod.build_payload(bd, gt_path)

        # Gated aggregate is within-one mean across runs: (0.7 + 0.8) / 2 = 0.75.
        assert payload.metrics[0]["name"] == "within_one_bucket_accuracy"
        assert payload.metrics[0]["value"] == pytest.approx(0.75)

    def test_breakdown_totals_reconcile_with_scored_rows(self, tmp_path):
        """Per-category totals must sum to every judged email-row scored — the
        breakdown accounts for all rows across runs (= test_cases_run × n_runs for
        equal-size runs), no more, no fewer."""
        mod = self._load_gen_scorecard()
        bd = tmp_path / "bdir"
        bd.mkdir()
        (bd / "scorecard.json").write_text(json.dumps(_RICHER_SCORECARD))
        gt_path = self._make_gt(tmp_path)
        payload = mod.build_payload(bd, gt_path)

        total = sum(r["total"] for r in payload.breakdown["per_category"])
        scored_rows = sum(
            int(s.get("total_emails", 0))
            for s in _RICHER_SCORECARD["scenarios"]
            if isinstance(s.get("quality"), dict)
        )
        assert total == scored_rows

    def test_breakdown_none_when_no_categorization_rows(self, tmp_path):
        """If no judged scenario has categorization.rows, breakdown must be None."""
        mod = self._load_gen_scorecard()
        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        # Minimal scorecard without categorization
        scorecard = {
            "run_id": "no-rows",
            "scenarios": [
                {
                    "category": "m",
                    "status": "PASS",
                    "total_emails": 10,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                }
            ],
        }
        (benchmark_dir / "scorecard.json").write_text(json.dumps(scorecard))
        gt_path = self._make_gt(tmp_path)
        payload = mod.build_payload(benchmark_dir, gt_path)

        assert (
            payload.breakdown is None
        ), "breakdown must be None when no scenario carries categorization.rows"

    def test_per_category_sorted_alphabetically(self, tmp_path):
        """per_category entries must be sorted by category name."""
        mod = self._load_gen_scorecard()
        bd = tmp_path / "bdir"
        bd.mkdir()
        (bd / "scorecard.json").write_text(json.dumps(_RICHER_SCORECARD))
        gt_path = self._make_gt(tmp_path)
        payload = mod.build_payload(bd, gt_path)

        names = [r["category"] for r in payload.breakdown["per_category"]]
        assert names == sorted(names), f"per_category not sorted: {names}"


# ---------------------------------------------------------------------------
# 14. Task 2 adapter: environment embedding (gen_scorecard.py)
# ---------------------------------------------------------------------------


class TestEnvironmentAdapter:
    """Adapter tests: build_payload embeds a passed-in environment dict verbatim."""

    def _load_gen_scorecard(self):
        adapter_path = (
            Path(__file__).parents[3]
            / "hub"
            / "agents"
            / "email"
            / "python"
            / "packaging"
            / "gen_scorecard.py"
        )
        spec = importlib.util.spec_from_file_location("gen_scorecard", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _make_benchmark_dir(self, tmp_path):
        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        scorecard = {
            "run_id": "env-fixture",
            "scenarios": [
                {
                    "category": "Gemma-4-E4B-it-GGUF",
                    "status": "PASS",
                    "total_emails": 10,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                }
            ],
        }
        (benchmark_dir / "scorecard.json").write_text(json.dumps(scorecard))
        gt = {"a": {"label": "x"}}
        gt_path = tmp_path / "gt.json"
        gt_path.write_text(json.dumps(gt))
        return benchmark_dir, gt_path

    def test_environment_embedded_verbatim(self, tmp_path):
        mod = self._load_gen_scorecard()
        bd, gt = self._make_benchmark_dir(tmp_path)
        env = {
            "gaia_commit": "deadbeef",
            "lemonade_version": "10.9.0",
            "model": "Gemma-4-E4B-it-GGUF",
            "hardware": "AMD Ryzen AI MAX+ (Strix Halo)",
        }
        payload = mod.build_payload(bd, gt, environment=env)
        assert payload.environment == env

    def test_environment_none_when_not_passed(self, tmp_path):
        mod = self._load_gen_scorecard()
        bd, gt = self._make_benchmark_dir(tmp_path)
        payload = mod.build_payload(bd, gt)
        assert payload.environment is None

    def test_environment_round_trips_through_scorecard(self, tmp_path):
        mod = self._load_gen_scorecard()
        bd, gt = self._make_benchmark_dir(tmp_path)
        env = {
            "gaia_commit": "abc1234",
            "lemonade_version": "10.8.0",
            "model": "Gemma-4-E4B-it-GGUF",
            "hardware": "AMD Ryzen AI MAX+ (Strix Halo)",
            "temperature": 0.0,
        }
        payload = mod.build_payload(bd, gt, environment=env)
        text = render_scorecard(payload)
        parsed = parse_scorecard(text)
        recovered = parsed["recipe"]["environment"]
        assert recovered["gaia_commit"] == "abc1234"
        assert recovered["temperature"] == 0.0


# ---------------------------------------------------------------------------
# 15. Task: ctx_size pre-read (main() reads quality.json, fails loud if absent)
# ---------------------------------------------------------------------------


class TestGenScorecardCtxSizePreRead:
    """main() must pre-read ctx_size from quality.json (via the existing
    _load_quality_aggregate helper) and fail loud when it is unavailable —
    unlike the soft/optional `_model` pre-read from scorecard.json.
    """

    def _load_gen_scorecard(self):
        adapter_path = (
            Path(__file__).parents[3]
            / "hub"
            / "agents"
            / "email"
            / "python"
            / "packaging"
            / "gen_scorecard.py"
        )
        spec = importlib.util.spec_from_file_location("gen_scorecard", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _make_benchmark_dir(self, tmp_path):
        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        scorecard = {
            "run_id": "ctx-fixture",
            "scenarios": [
                {
                    "category": "Gemma-4-E4B-it-GGUF",
                    "status": "PASS",
                    "total_emails": 10,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                }
            ],
        }
        (benchmark_dir / "scorecard.json").write_text(json.dumps(scorecard))
        gt = {"a": {"label": "x"}}
        gt_path = tmp_path / "gt.json"
        gt_path.write_text(json.dumps(gt))
        return benchmark_dir, gt_path

    def _main_argv(self, benchmark_dir, gt_path, output_dir):
        # --lemonade-version is passed explicitly so main() never attempts a
        # live health-endpoint query in this unit test.
        return [
            "--benchmark-dir",
            str(benchmark_dir),
            "--ground-truth",
            str(gt_path),
            "--output-dir",
            str(output_dir),
            "--lemonade-version",
            "10.9.0",
        ]

    def test_main_emits_ctx_size_in_environment_from_quality_json(self, tmp_path):
        mod = self._load_gen_scorecard()
        benchmark_dir, gt_path = self._make_benchmark_dir(tmp_path)
        (benchmark_dir / "quality.json").write_text(
            json.dumps(
                {
                    "ctx_size": 16384,
                    "within_one_bucket_accuracy": 0.8,
                }
            )
        )
        output_dir = tmp_path / "out"

        rc = mod.main(self._main_argv(benchmark_dir, gt_path, output_dir))

        assert rc == 0
        card_path = output_dir / "SCORECARD.md"
        parsed = parse_scorecard(card_path.read_text(encoding="utf-8"))
        assert parsed["recipe"]["environment"]["ctx_size"] == 16384

    def test_main_fails_loud_when_quality_json_missing(self, tmp_path):
        mod = self._load_gen_scorecard()
        benchmark_dir, gt_path = self._make_benchmark_dir(tmp_path)
        # No quality.json written at all.
        output_dir = tmp_path / "out"

        rc = mod.main(self._main_argv(benchmark_dir, gt_path, output_dir))

        assert rc != 0

    def test_main_fails_loud_when_quality_json_lacks_ctx_size(self, tmp_path):
        mod = self._load_gen_scorecard()
        benchmark_dir, gt_path = self._make_benchmark_dir(tmp_path)
        (benchmark_dir / "quality.json").write_text(
            json.dumps({"within_one_bucket_accuracy": 0.8})
        )
        output_dir = tmp_path / "out"

        rc = mod.main(self._main_argv(benchmark_dir, gt_path, output_dir))

        assert rc != 0

    def test_build_payload_still_gains_no_new_file_reading_for_ctx_size(self, tmp_path):
        # Regression guard -- should already pass; proves build_payload never
        # gains ctx-file-reading. environment is embedded verbatim regardless
        # of what main() does to assemble it.
        mod = self._load_gen_scorecard()
        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        scorecard = {
            "run_id": "ctx-verbatim",
            "scenarios": [
                {
                    "category": "Gemma-4-E4B-it-GGUF",
                    "status": "PASS",
                    "total_emails": 10,
                    "quality": {
                        "category_accuracy": 0.5,
                        "within_one_bucket_accuracy": 0.8,
                    },
                }
            ],
        }
        (benchmark_dir / "scorecard.json").write_text(json.dumps(scorecard))
        gt = {"a": {"label": "x"}}
        gt_path = tmp_path / "gt.json"
        gt_path.write_text(json.dumps(gt))

        env = {
            "gaia_commit": "deadbeef",
            "lemonade_version": "10.9.0",
            "model": "Gemma-4-E4B-it-GGUF",
            "hardware": "AMD Ryzen AI MAX+ (Strix Halo)",
            "ctx_size": 16384,
        }
        payload = mod.build_payload(benchmark_dir, gt_path, environment=env)

        assert payload.environment["ctx_size"] == 16384
