# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`--iterations` and the stability class it exists to expose.

The flag shipped declared-but-unwired: `args.iterations` was read nowhere, so
`--iterations 5` silently ran once and reported n=1 numbers as if they were N.
These pin the wiring and the one thing a single run cannot express — whether a
scenario is reliably green, reliably red, or FLAKY.
"""

import pytest

from gaia.eval.runner import AgentEvalRunner, summarize_attempts


def _attempt(status, score=None):
    return {"scenario_id": "s", "status": status, "overall_score": score, "turns": []}


class TestStabilityClass:
    def test_all_pass_is_stable_pass(self):
        out = summarize_attempts([_attempt("PASS", 9.0)] * 3)
        assert out["stability"]["stability"] == "stable-pass"
        assert out["stability"]["pass_rate"] == 1.0

    def test_mixed_is_flaky_and_reports_the_failure(self):
        """The case n=1 hides: reporting a pass here would be a false green."""
        out = summarize_attempts(
            [_attempt("PASS", 9.0), _attempt("FAIL", 4.0), _attempt("PASS", 8.0)]
        )
        assert out["stability"]["stability"] == "flaky"
        assert out["stability"]["pass_count"] == 2
        assert out["stability"]["pass_rate"] == pytest.approx(0.6667, abs=1e-3)
        # Worst judged attempt represents the scenario — never the lucky run.
        assert out["status"] == "FAIL"

    def test_no_pass_is_stable_fail(self):
        out = summarize_attempts([_attempt("FAIL", 3.0)] * 2)
        assert out["stability"]["stability"] == "stable-fail"
        assert out["stability"]["pass_rate"] == 0.0

    def test_score_spread_is_reported(self):
        out = summarize_attempts(
            [_attempt("PASS", 9.0), _attempt("PASS", 7.0), _attempt("PASS", 8.0)]
        )
        s = out["stability"]
        assert (s["score_min"], s["score_max"], s["score_avg"]) == (7.0, 9.0, 8.0)
        assert s["score_stdev"] == pytest.approx(1.0)

    def test_infra_failures_excluded_from_the_rate(self):
        """An INFRA_ERROR says nothing about quality — it must not dilute it."""
        out = summarize_attempts(
            [_attempt("PASS", 9.0), _attempt("INFRA_ERROR"), _attempt("PASS", 9.0)]
        )
        assert out["stability"]["judged"] == 2
        assert out["stability"]["pass_rate"] == 1.0
        assert out["stability"]["runs"] == 3

    def test_single_attempt_is_passed_through_unchanged(self):
        one = _attempt("PASS", 9.0)
        assert summarize_attempts([one]) is one

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="at least one attempt"):
            summarize_attempts([])


class TestRunnerWiring:
    def test_iterations_is_stored(self):
        assert AgentEvalRunner(iterations=5).iterations == 5

    def test_default_is_one(self):
        assert AgentEvalRunner().iterations == 1

    def test_none_falls_back_to_one(self):
        assert AgentEvalRunner(iterations=None).iterations == 1

    def test_zero_or_negative_raises(self):
        with pytest.raises(ValueError, match="iterations must be >= 1"):
            AgentEvalRunner(iterations=0)


class TestPerAttemptEvidence:
    """With --iterations, every attempt needs its own trace. Writing them all to
    <sid>.json leaves only the last one on disk — so a `flaky` verdict could
    never be investigated, and resume would reload a single attempt in place of
    the summarized result and silently change the scorecard."""

    def test_attempt_index_produces_a_distinct_trace_name(self):
        import inspect

        from gaia.eval import runner

        src = inspect.getsource(runner.run_scenario_subprocess)
        assert "attempt}.json" in src, "per-attempt traces must not share a path"
        assert (
            "attempt=None"
            in inspect.signature(runner.run_scenario_subprocess).parameters.__str__()
            or "attempt" in inspect.signature(runner.run_scenario_subprocess).parameters
        )

    def test_summarized_result_is_what_resume_reloads(self):
        """<sid>.json must hold the summarized result (with stability), because
        that is the file the resume path reads back."""
        import inspect

        from gaia.eval.runner import AgentEvalRunner

        # The iterations loop lives in _run_locked, which run() delegates to.
        src = inspect.getsource(AgentEvalRunner._run_locked)
        assert 'f"{sid}.json"' in src
        assert "summarize_attempts" in src
