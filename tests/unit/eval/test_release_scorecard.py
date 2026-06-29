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
        assert payload.metrics[0]["value"] == pytest.approx(
            expected_mean
        ), f"Expected metric value {expected_mean}, got {payload.metrics[0]['value']}"

    def test_build_payload_weighted_by_total_emails(self, tmp_path):
        # Unequal scenario sizes: the aggregate must be the per-email accuracy
        # (weighted by total_emails), not an unweighted scenario mean.
        mod = self._load_gen_scorecard()

        benchmark_dir = tmp_path / "benchmark"
        benchmark_dir.mkdir()
        scorecard = {
            "run_id": "weighted-fixture",
            "scenarios": [
                {
                    "category": "m",
                    "status": "PASS",
                    "total_emails": 90,
                    "quality": {"category_accuracy": 0.90},
                },
                {
                    "category": "m",
                    "status": "PASS",
                    "total_emails": 10,
                    "quality": {"category_accuracy": 0.00},
                },
            ],
        }
        (benchmark_dir / "email_benchmark_scorecard.json").write_text(
            json.dumps(scorecard)
        )
        gt_path = tmp_path / "ground_truth.json"
        gt_path.write_text(json.dumps({"a": {"label": "x"}, "b": {"label": "y"}}))

        payload = mod.build_payload(benchmark_dir, gt_path)

        # Weighted: (0.90*90 + 0.00*10) / 100 = 0.81  (unweighted would be 0.45)
        assert payload.metrics[0]["value"] == pytest.approx(0.81)
        assert payload.test_cases_run == 100

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
