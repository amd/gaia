# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end scoring of ``triage_logs`` on generated logs with planted incidents.

Eight invented scenarios (see ``log_triage_scenarios``): a fault burst amid a
heavy FATAL baseline, a slow memory leak, one failing host against a
cluster-wide outage, an access-log 5xx spike from one upstream, a new
exception after a deploy in JSON lines, three files with different clock
conventions, and two negatives where nothing should be flagged. Each run is
scored for anomaly-template precision and recall, burst start/end within a
tolerance, and anomalous-entity precision and recall.
"""

from __future__ import annotations

import pytest

from tests.unit.log_triage_scenarios import SCENARIOS, run, score

MIN_PRECISION = 0.9
MIN_RECALL = 0.9
BURST_TOLERANCE_S = 60.0


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_planted_incidents_are_found_and_nothing_else(name, tmp_path):
    scenario = SCENARIOS[name](tmp_path)
    result = run(scenario)
    s = score(scenario, result, tolerance_s=BURST_TOLERANCE_S)
    print(s.row())
    assert result.checks["ok"], result.checks["failed"]
    if scenario.negative:
        assert s.reported == 0, f"flagged on a clean log: {s.false_templates}"
        assert not result.bursts, "a burst on a clean log"
        assert not result.entities["flagged"]
        return
    assert s.template_precision >= MIN_PRECISION, s.false_templates
    assert s.template_recall >= MIN_RECALL, s.missed_kinds
    if scenario.score_bursts:
        assert s.bursts_found >= MIN_RECALL, (
            [(b["start"], b["end"]) for b in result.bursts],
            scenario.bursts,
        )
        assert s.burst_precision >= MIN_PRECISION
    assert s.entity_precision >= MIN_PRECISION
    assert s.entity_recall >= MIN_RECALL


def test_single_host_and_cluster_wide_failures_report_their_blast_radius(tmp_path):
    scenario = SCENARIOS["single_vs_cluster"](tmp_path)
    result = run(scenario)
    radius = {}
    for t in result.anomalies:
        if "EXT" in t.key or "aborted" in t.key:
            radius.setdefault("single", set()).update(t.hosts)
        if "unreachable" in t.key:
            radius.setdefault("cluster", set()).update(t.hosts)
    assert radius["single"] == {"web-07"}
    assert len(radius["cluster"]) == 20


def test_the_compact_result_fits_a_small_context(tmp_path):
    import json

    for name in ("hardware_fault_burst", "access_5xx_spike", "multi_file_timezones"):
        sub = tmp_path / name
        sub.mkdir()
        result = run(SCENARIOS[name](sub))
        size = len(json.dumps(result.compact(), separators=(",", ":"), default=str))
        assert size < 12_000, f"{name}: {size} chars"


def test_skewed_clock_is_reported_and_the_burst_still_spans_all_files(tmp_path):
    scenario = SCENARIOS["multi_file_timezones"](tmp_path)
    result = run(scenario)
    assert result.utc
    worker = [c for c in result.clock if c["file"] == "worker.log"]
    assert worker and 38 <= worker[0]["median_offset_s"] <= 46
    assert len(result.bursts) == 1 and len(result.bursts[0]["hosts"]) == 3
