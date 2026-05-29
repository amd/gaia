# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.statistics (pure stdlib, no Lemonade)."""

import json

import pytest

from gaia.eval.statistics import (
    bootstrap_ci,
    cliffs_delta,
    compare_runs,
    compare_runs_by_model,
    compute_variance,
    mann_whitney_u,
    to_dict,
)


class TestComputeVariance:
    def test_known_values(self):
        vs = compute_variance([10, 12, 14, 16, 18], metric_name="demo")
        assert vs.metric == "demo"
        assert vs.mean == 14.0
        assert vs.stdev == 3.16  # sample stdev sqrt(10) rounded
        assert vs.min_val == 10
        assert vs.max_val == 18
        assert vs.cv_pct == 22.59
        assert vs.median == 14.0
        assert vs.p25 == 12.0
        assert vs.p75 == 16.0
        assert vs.iqr == 4.0
        assert vs.n == 5

    def test_empty_returns_zeros(self):
        vs = compute_variance([], metric_name="empty")
        assert vs.mean == 0.0
        assert vs.stdev == 0.0
        assert vs.n == 0

    def test_single_value_zero_variance(self):
        vs = compute_variance([42.0], metric_name="one")
        assert vs.mean == 42.0
        assert vs.stdev == 0.0
        assert vs.cv_pct == 0.0
        assert vs.n == 1


class TestMannWhitneyU:
    def test_fully_separated_groups(self):
        u, p = mann_whitney_u([1, 2, 3], [10, 11, 12])
        assert u == 0.0  # no overlap → U statistic is 0
        assert p < 0.15  # normal approx for n=3 each

    def test_too_few_samples_is_nonsignificant(self):
        u, p = mann_whitney_u([1], [2])
        assert u == 0.0
        assert p == 1.0


class TestCliffsDelta:
    def test_a_entirely_below_b_is_minus_one(self):
        assert cliffs_delta([1, 2, 3], [10, 11, 12]) == -1.0

    def test_a_entirely_above_b_is_plus_one(self):
        assert cliffs_delta([10, 11, 12], [1, 2, 3]) == 1.0

    def test_identical_is_zero(self):
        # all ties → (0 greater + 9 ties)/9 - 1 = 0
        assert cliffs_delta([5, 5, 5], [5, 5, 5]) == 0.0


class TestBootstrapCI:
    def test_deterministic_and_reproducible(self):
        a = [1.0, 2.0, 3.0, 4.0]
        b = [10.0, 11.0, 12.0, 13.0]
        ci1 = bootstrap_ci(a, b)
        ci2 = bootstrap_ci(a, b)
        assert ci1 == ci2  # seeded → reproducible
        lower, upper = ci1
        assert lower <= upper
        assert upper < 0  # mean(a) - mean(b) is strongly negative

    def test_too_few_samples_returns_zero_interval(self):
        assert bootstrap_ci([1.0], [2.0]) == (0.0, 0.0)


def _run(run_id, model, dur_ms, tokens, cats):
    return {
        "run_id": run_id,
        "model": model,
        "total_duration_ms": dur_ms,
        "total_tokens": tokens,
        "total_input_tokens": int(tokens * 0.8),
        "total_output_tokens": int(tokens * 0.2),
        "total_emails": sum(cats.values()),
        "category_counts": cats,
        "avg_tokens_per_second": 50.0,
        "avg_time_to_first_token_ms": 100.0,
    }


class TestCompareRuns:
    def test_two_run_deltas(self):
        runs = [
            _run("a", "M", 1000, 500, {"urgent": 2, "low priority": 8}),
            _run("b", "M", 1200, 600, {"urgent": 3, "low priority": 7}),
        ]
        report = compare_runs(runs)
        assert report.runs_compared == 2
        assert len(report.run_deltas) == 1
        d = report.run_deltas[0]
        assert d.delta_duration_ms == 200
        assert d.delta_total_tokens == 100
        assert d.category_deltas == {"urgent": 1, "low priority": -1}
        assert report.variance_summaries  # non-empty

    def test_single_run_zero_variance(self):
        report = compare_runs([_run("solo", "M", 800, 400, {"urgent": 1})])
        assert report.runs_compared == 1
        assert report.run_deltas == []
        assert all(vs.stdev == 0.0 for vs in report.variance_summaries)

    def test_to_dict_is_json_serializable(self):
        runs = [
            _run("a", "M", 1000, 500, {"urgent": 2}),
            _run("b", "M", 1100, 520, {"urgent": 3}),
        ]
        out = to_dict(compare_runs(runs))
        # Must round-trip through json with no errors.
        json.dumps(out)
        assert out["runs_compared"] == 2


class TestCompareRunsByModel:
    def test_groups_by_model(self):
        runs = [
            _run("a1", "X", 1000, 500, {"urgent": 1}),
            _run("a2", "X", 1050, 510, {"urgent": 2}),
            _run("b1", "Y", 900, 450, {"urgent": 1}),
        ]
        by_model = compare_runs_by_model(runs)
        assert set(by_model.keys()) == {"X", "Y"}
        assert by_model["X"].runs_compared == 2
        assert by_model["Y"].runs_compared == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
