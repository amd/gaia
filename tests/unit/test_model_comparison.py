# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One table comparing models on the same scenarios.

The table exists to answer "which model should run this work", so the ways it
can lie matter more than the ways it can be ugly: a model whose tokens were
never counted must not look free, a model with no published rate must not get
a guessed one, and two models must never be costed under different rate cards.
"""

from __future__ import annotations

import json

import pytest

from gaia.eval.model_comparison import (
    ModelRun,
    compare,
    from_scorecard,
    load_scorecard,
    render_markdown,
)
from gaia.eval.quality_metrics import compute_cost


def scorecard(
    model,
    *,
    passed=3,
    total=4,
    score=7.5,
    steps=6,
    tools=4,
    inp=100_000,
    cached=60_000,
    out=5_000,
    seconds=30.0,
):
    """A scorecard shaped like build_scorecard's output."""
    scenarios = [
        {
            "status": "PASS" if i < passed else "FAIL",
            "overall_score": score,
            "category": "demo",
            "performance_summary": {
                "steps": steps,
                "tool_calls": tools,
                "total_input_tokens": inp // total,
                "total_output_tokens": out // total,
                "total_cached_tokens": cached // total,
                "pipeline_latency_s": seconds / total,
            },
        }
        for i in range(total)
    ]
    return {
        "run_id": f"run-{model}",
        "config": {"model": model},
        "summary": {"total_scenarios": total, "passed": passed, "avg_score": score},
        "scenarios": scenarios,
        "performance": {"avg_tokens_per_second": 42.0, "avg_time_to_first_token": 1.5},
    }


class TestFlattening:
    def test_reads_the_metrics_the_experiment_asks_about(self):
        run = from_scorecard(scorecard("fireworks.glm-5p3"))
        assert run.model == "fireworks.glm-5p3"
        assert (run.passed, run.scenarios) == (3, 4)
        assert run.pass_rate == 0.75
        assert run.avg_score == 7.5
        assert run.steps == 24 and run.tool_calls == 16
        assert run.input_tokens == 100_000 and run.output_tokens == 5_000
        assert run.cached_tokens == 60_000
        assert run.cached_share == 0.6
        assert run.tokens_per_second == 42.0
        assert run.wall_seconds == pytest.approx(30.0)

    def test_a_run_with_no_token_counts_reports_none_not_zero(self):
        card = scorecard("fireworks.glm-5p3")
        for s in card["scenarios"]:
            s.pop("performance_summary")
        run = from_scorecard(card)
        assert run.input_tokens is None
        assert run.usd is None, "an unmeasured run must not be priced as free"

    def test_the_row_is_labelled_even_without_a_configured_model(self):
        card = scorecard("x")
        card["config"] = {}
        assert from_scorecard(card).model == "run-x"


class TestPricing:
    def test_priced_from_the_published_card_including_cache(self):
        run = from_scorecard(scorecard("fireworks.glm-5p3"))
        expected = compute_cost(
            100_000, 5_000, model="fireworks.glm-5p3", cached_input_tokens=60_000
        )
        assert run.usd == pytest.approx(expected)

    def test_caching_is_not_silently_ignored(self):
        """Ignoring the cache share would overstate this run by ~40%."""
        cached = from_scorecard(scorecard("fireworks.glm-5p3")).usd
        uncached = from_scorecard(scorecard("fireworks.glm-5p3", cached=0)).usd
        assert cached < uncached

    def test_an_unpriced_model_gets_no_dollars(self):
        run = from_scorecard(scorecard("some-local-gguf"))
        assert run.input_tokens == 100_000
        assert run.usd is None

    def test_cost_per_pass_is_the_comparison_that_matters(self):
        run = from_scorecard(scorecard("fireworks.glm-5p3", passed=2, total=4))
        assert run.usd_per_pass == pytest.approx(run.usd / 2)

    def test_no_passes_means_no_unit_price(self):
        run = from_scorecard(scorecard("fireworks.glm-5p3", passed=0, total=4))
        assert run.usd_per_pass is None


class TestRendering:
    def test_one_row_per_model_best_first(self):
        table = render_markdown(
            [
                from_scorecard(scorecard("fireworks.glm-5p3", passed=2)),
                from_scorecard(scorecard("fireworks.glm-5p3-flash", passed=4)),
            ]
        )
        lines = [ln for ln in table.splitlines() if ln.startswith("|")]
        assert "flash" in lines[2], "the model that did the work best is not first"

    def test_an_unmeasured_metric_reads_as_absent_not_as_zero(self):
        run = ModelRun(model="m", scenarios=1, passed=1)
        table = render_markdown([run])
        assert "—" in table
        assert "$0.0000" not in table

    def test_unpriced_models_are_named_rather_than_left_looking_free(self):
        table = render_markdown([from_scorecard(scorecard("some-local-gguf"))])
        assert "No published rate for some-local-gguf" in table

    def test_empty_input_says_so(self):
        assert render_markdown([]) == "No runs to compare."


class TestLoading:
    def test_a_missing_file_names_the_path(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="no scorecard at"):
            load_scorecard(tmp_path / "nope.json")

    def test_a_non_scorecard_json_is_rejected_with_a_hint(self, tmp_path):
        p = tmp_path / "other.json"
        p.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
        with pytest.raises(ValueError, match="does not look like an eval scorecard"):
            load_scorecard(p)

    def test_malformed_json_names_the_file(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_scorecard(p)

    def test_compare_reads_files_and_renders(self, tmp_path):
        paths = []
        for name in ("fireworks.glm-5p3", "fireworks.glm-5p3-flash"):
            p = tmp_path / f"{name.replace('/', '_')}.json"
            p.write_text(json.dumps(scorecard(name)), encoding="utf-8")
            paths.append((name, p))
        table = compare(paths)
        assert "glm-5p3-flash" in table and "glm-5p3 " in table + " "
