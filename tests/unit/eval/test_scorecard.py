# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.eval.scorecard``.

Covers build_scorecard aggregation, write_summary_md rendering,
write_junit_xml conversion, and edge cases (empty results, mixed statuses,
performance data, unrecognized statuses).
"""

import xml.etree.ElementTree as ET

import pytest

from gaia.eval.scorecard import build_scorecard, write_junit_xml, write_summary_md

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = {"model": "test-model", "budget": "1.00"}


def _result(
    scenario_id,
    status,
    overall_score,
    category="general",
    cost_usd=0.0,
    performance_summary=None,
    root_cause=None,
):
    r = {
        "scenario_id": scenario_id,
        "status": status,
        "overall_score": overall_score,
        "category": category,
        "turns": [],
        "cost_estimate": {"turns": 1, "estimated_usd": cost_usd},
    }
    if performance_summary is not None:
        r["performance_summary"] = performance_summary
    if root_cause is not None:
        r["root_cause"] = root_cause
    return r


# ---------------------------------------------------------------------------
# build_scorecard — basic counts
# ---------------------------------------------------------------------------


class TestBuildScorecardCounts:
    def test_all_pass(self):
        results = [_result("a", "PASS", 8.0), _result("b", "PASS", 9.0)]
        sc = build_scorecard("run-1", results, _MINIMAL_CONFIG)
        s = sc["summary"]
        assert s["total_scenarios"] == 2
        assert s["passed"] == 2
        assert s["failed"] == 0
        assert s["pass_rate"] == 1.0
        assert s["judged_pass_rate"] == 1.0

    def test_mixed_statuses(self):
        results = [
            _result("a", "PASS", 8.0),
            _result("b", "FAIL", 4.0),
            _result("c", "BLOCKED_BY_ARCHITECTURE", 3.0),
            _result("d", "TIMEOUT", None),
            _result("e", "BUDGET_EXCEEDED", None),
            _result("f", "INFRA_ERROR", None),
            _result("g", "SETUP_ERROR", None),
            _result("h", "SKIPPED_NO_DOCUMENT", None),
        ]
        sc = build_scorecard("run-2", results, _MINIMAL_CONFIG)
        s = sc["summary"]
        assert s["total_scenarios"] == 8
        assert s["passed"] == 1
        assert s["failed"] == 1
        assert s["blocked"] == 1
        assert s["timeout"] == 1
        assert s["budget_exceeded"] == 1
        assert s["infra_error"] == 2  # INFRA_ERROR + SETUP_ERROR
        assert s["skipped"] == 1
        assert s["errored"] == 0

    def test_errored_for_unknown_status(self):
        results = [_result("x", "SOMETHING_NEW", 5.0)]
        sc = build_scorecard("run-3", results, _MINIMAL_CONFIG)
        assert sc["summary"]["errored"] == 1
        assert "warnings" in sc

    def test_empty_results(self):
        sc = build_scorecard("run-empty", [], _MINIMAL_CONFIG)
        s = sc["summary"]
        assert s["total_scenarios"] == 0
        assert s["pass_rate"] == 0.0
        assert s["avg_score"] == 0.0


# ---------------------------------------------------------------------------
# build_scorecard — avg_score
# ---------------------------------------------------------------------------


class TestBuildScorecardScoring:
    def test_avg_score_excludes_infra(self):
        """TIMEOUT/BUDGET_EXCEEDED/INFRA_ERROR must NOT dilute avg_score."""
        results = [
            _result("a", "PASS", 8.0),
            _result("b", "TIMEOUT", None),
        ]
        sc = build_scorecard("run-s1", results, _MINIMAL_CONFIG)
        assert sc["summary"]["avg_score"] == 8.0

    def test_fail_scores_capped_at_5_99(self):
        """FAIL scenarios with score >= 6 should be capped at 5.99 for averaging."""
        results = [_result("a", "FAIL", 7.0)]
        sc = build_scorecard("run-cap", results, _MINIMAL_CONFIG)
        assert sc["summary"]["avg_score"] == 5.99

    def test_pass_scores_not_capped(self):
        results = [_result("a", "PASS", 9.5)]
        sc = build_scorecard("run-nocap", results, _MINIMAL_CONFIG)
        assert sc["summary"]["avg_score"] == 9.5

    def test_judged_pass_rate(self):
        """judged_pass_rate denominator is PASS + FAIL + BLOCKED only."""
        results = [
            _result("a", "PASS", 8.0),
            _result("b", "FAIL", 3.0),
            _result("c", "TIMEOUT", None),
        ]
        sc = build_scorecard("run-jpr", results, _MINIMAL_CONFIG)
        assert sc["summary"]["judged_pass_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# build_scorecard — by_category
# ---------------------------------------------------------------------------


class TestBuildScorecardCategory:
    def test_category_breakdown(self):
        results = [
            _result("a", "PASS", 9.0, category="rag"),
            _result("b", "FAIL", 4.0, category="rag"),
            _result("c", "PASS", 8.0, category="tool"),
        ]
        sc = build_scorecard("run-cat", results, _MINIMAL_CONFIG)
        by_cat = sc["summary"]["by_category"]
        assert "rag" in by_cat
        assert "tool" in by_cat
        assert by_cat["rag"]["passed"] == 1
        assert by_cat["rag"]["failed"] == 1
        assert by_cat["tool"]["passed"] == 1

    def test_category_avg_score_caps_fail(self):
        results = [_result("a", "FAIL", 7.5, category="q")]
        sc = build_scorecard("run-catcap", results, _MINIMAL_CONFIG)
        assert sc["summary"]["by_category"]["q"]["avg_score"] == 5.99


# ---------------------------------------------------------------------------
# build_scorecard — cost and performance
# ---------------------------------------------------------------------------


class TestBuildScorecardCostPerf:
    def test_cost_aggregation(self):
        results = [
            _result("a", "PASS", 8.0, cost_usd=0.12),
            _result("b", "PASS", 7.0, cost_usd=0.08),
        ]
        sc = build_scorecard("run-cost", results, _MINIMAL_CONFIG)
        assert sc["cost"]["estimated_total_usd"] == pytest.approx(0.20, abs=1e-4)

    def test_performance_aggregation(self):
        perf = {
            "avg_tokens_per_second": 40.0,
            "avg_time_to_first_token": 1.5,
            "total_input_tokens": 100,
            "total_output_tokens": 200,
            "flags": ["slow"],
        }
        results = [_result("a", "PASS", 8.0, performance_summary=perf)]
        sc = build_scorecard("run-perf", results, _MINIMAL_CONFIG)
        p = sc["performance"]
        assert p["avg_tokens_per_second"] == 40.0
        assert p["avg_time_to_first_token"] == 1.5
        assert p["total_input_tokens"] == 100
        assert p["total_output_tokens"] == 200
        assert "slow" in p["flags"]

    def test_no_performance_data(self):
        results = [_result("a", "PASS", 8.0)]
        sc = build_scorecard("run-noperf", results, _MINIMAL_CONFIG)
        assert sc["performance"]["avg_tokens_per_second"] is None
        assert sc["performance"]["scenarios_with_data"] == 0


# ---------------------------------------------------------------------------
# build_scorecard — metadata
# ---------------------------------------------------------------------------


class TestBuildScorecardMeta:
    def test_run_id_and_config_preserved(self):
        sc = build_scorecard("my-run", [_result("a", "PASS", 8.0)], _MINIMAL_CONFIG)
        assert sc["run_id"] == "my-run"
        assert sc["config"] == _MINIMAL_CONFIG
        assert "timestamp" in sc

    def test_scenarios_list_preserved(self):
        results = [_result("a", "PASS", 8.0)]
        sc = build_scorecard("r", results, _MINIMAL_CONFIG)
        assert sc["scenarios"] is results


# ---------------------------------------------------------------------------
# write_summary_md
# ---------------------------------------------------------------------------


class TestWriteSummaryMd:
    def test_contains_key_sections(self):
        results = [
            _result("a", "PASS", 8.0, category="rag"),
            _result("b", "FAIL", 3.0, category="rag", root_cause="bad prompt"),
        ]
        sc = build_scorecard("run-md", results, _MINIMAL_CONFIG)
        md = write_summary_md(sc)
        assert "# GAIA Agent Eval" in md
        assert "## Summary" in md
        assert "## By Category" in md
        assert "## Scenarios" in md
        assert "bad prompt" in md

    def test_performance_section_when_data_present(self):
        perf = {
            "avg_tokens_per_second": 50.0,
            "avg_time_to_first_token": 0.8,
            "total_input_tokens": 500,
            "total_output_tokens": 300,
            "flags": [],
        }
        results = [_result("a", "PASS", 8.0, performance_summary=perf)]
        sc = build_scorecard("run-mdperf", results, _MINIMAL_CONFIG)
        md = write_summary_md(sc)
        assert "## Performance" in md
        assert "50.0 tok/s" in md

    def test_no_performance_section_when_no_data(self):
        results = [_result("a", "PASS", 8.0)]
        sc = build_scorecard("run-nop", results, _MINIMAL_CONFIG)
        md = write_summary_md(sc)
        assert "## Performance" not in md


# ---------------------------------------------------------------------------
# write_junit_xml
# ---------------------------------------------------------------------------


class TestWriteJunitXml:
    def test_valid_xml(self):
        results = [
            _result("a", "PASS", 8.0, category="rag"),
            _result("b", "FAIL", 4.0, category="rag"),
        ]
        sc = build_scorecard("run-xml", results, _MINIMAL_CONFIG)
        xml_str = write_junit_xml(sc)
        root = ET.fromstring(xml_str)
        assert root.tag == "testsuites"

    def test_pass_has_no_failure_element(self):
        results = [_result("a", "PASS", 9.0, category="c1")]
        sc = build_scorecard("run-xp", results, _MINIMAL_CONFIG)
        xml_str = write_junit_xml(sc)
        root = ET.fromstring(xml_str)
        testcase = root.find(".//testcase[@name='a']")
        assert testcase is not None
        assert testcase.find("failure") is None
        assert testcase.find("error") is None

    def test_fail_has_failure_element(self):
        results = [_result("b", "FAIL", 3.0, category="c1")]
        sc = build_scorecard("run-xf", results, _MINIMAL_CONFIG)
        xml_str = write_junit_xml(sc)
        root = ET.fromstring(xml_str)
        testcase = root.find(".//testcase[@name='b']")
        assert testcase is not None
        failure = testcase.find("failure")
        assert failure is not None
        assert failure.get("type") == "FAIL"

    def test_timeout_has_error_element(self):
        results = [_result("t", "TIMEOUT", None, category="c1")]
        sc = build_scorecard("run-xt", results, _MINIMAL_CONFIG)
        xml_str = write_junit_xml(sc)
        root = ET.fromstring(xml_str)
        testcase = root.find(".//testcase[@name='t']")
        assert testcase is not None
        assert testcase.find("error") is not None

    def test_skipped_has_skipped_element(self):
        results = [_result("s", "SKIPPED_NO_DOCUMENT", None, category="c1")]
        sc = build_scorecard("run-xs", results, _MINIMAL_CONFIG)
        xml_str = write_junit_xml(sc)
        root = ET.fromstring(xml_str)
        testcase = root.find(".//testcase[@name='s']")
        assert testcase is not None
        assert testcase.find("skipped") is not None

    def test_category_testsuite(self):
        results = [
            _result("a", "PASS", 8.0, category="cat1"),
            _result("b", "PASS", 7.0, category="cat2"),
        ]
        sc = build_scorecard("run-xc", results, _MINIMAL_CONFIG)
        xml_str = write_junit_xml(sc)
        root = ET.fromstring(xml_str)
        suites = root.findall("testsuite")
        suite_names = {s.get("name") for s in suites}
        assert "cat1" in suite_names
        assert "cat2" in suite_names
