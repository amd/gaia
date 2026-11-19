# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TDD tests for gaia.eval.release_scorecard — written before implementation exists."""

import datetime

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
        card_path.write_text(render_scorecard(src), encoding="utf-8")

        result = carry_forward(card_path, "0.2.4")
        assert result.inherited_from == "0.2.3"

    def test_carry_forward_copies_metrics_verbatim(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "SCORECARD.md"
        card_path.write_text(render_scorecard(src), encoding="utf-8")

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
        card_path.write_text(render_scorecard(src), encoding="utf-8")

        result = carry_forward(card_path, "0.2.4")
        assert result.breakdown == src.breakdown
        assert result.environment == src.environment

    def test_carry_forward_reads_version_from_front_matter(self, tmp_path):
        # The new carry_forward reads agent.version from front matter, NOT filename.
        src = _make_payload(version="0.2.3", accuracy=0.75)
        # Use a different filename to confirm it's not read from stem
        card_path = tmp_path / "SCORECARD.md"
        card_path.write_text(render_scorecard(src), encoding="utf-8")

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
        card_path.write_text(render_scorecard(src), encoding="utf-8")

        with pytest.raises(ValueError, match="re-run"):
            carry_forward(card_path, "0.3.0")

    def test_major_bump_raises_value_error(self, tmp_path):
        src = _make_payload(version="0.2.3", accuracy=0.75)
        card_path = tmp_path / "SCORECARD.md"
        card_path.write_text(render_scorecard(src), encoding="utf-8")

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
        text = scorecard_path.read_text(encoding="utf-8")
        parsed = parse_scorecard(text)
        errors = validate_scorecard(parsed)
        assert errors == [], f"Second-agent scorecard should be valid, got: {errors}"

        # Gate should pass (no baseline → presence-only)
        result = gate_main(["--scorecard", str(scorecard_path)])
        assert result == 0, "Gate should pass for a valid second-agent SCORECARD.md"


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
        card.write_text(render_scorecard(src), encoding="utf-8")
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
        card.write_text(render_scorecard(src), encoding="utf-8")
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
