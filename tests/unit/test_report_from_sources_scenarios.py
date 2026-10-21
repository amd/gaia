# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end scoring of the report-from-sources tools on generated folders.

Six scenario types (tests/fixtures/report_from_sources_data.py), five seeds
each, with planted ground truth:

1. quarterly wide workbook (2-row merged header, units row) beside an older
   vintage of itself whose modified time is newer;
2. long-format monthly CSV (BOM, CRLF, Unicode names, shuffled rows, a gap);
3. messy sheet: banner and merged year headers, spacer columns, notes,
   footnote markers on values, duplicate "Total" rows under two sections;
4. fiscal-year columns, and a month column in five date formats;
5. ambiguous label searches that must be reported, not guessed;
6. negative: series that are not in the file, unrelated files that must not
   be grouped as one dataset.

Scored: newest-file choice and older-copy flags (precision/recall), exact
values and period labels of the requested periods, ambiguity and not-found
reports (precision/recall), and chart round-trip equality for .xlsx and .pptx.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict

import pytest

pytest.importorskip("openpyxl")
pytest.importorskip("docx")
pytest.importorskip("pptx")

_SKILL = Path(__file__).resolve().parents[2] / "hub" / "skills" / "report-from-sources"
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

from report_from_sources_lib.pptx_chart import add_native_chart  # noqa: E402
from report_from_sources_lib.series import (  # noqa: E402
    SeriesLookupError,
    extract_series,
    inventory_folder,
)
from report_from_sources_lib.xlsx_chart import write_workbook_chart  # noqa: E402

from tests.fixtures.report_from_sources_data import SCENARIOS  # noqa: E402

SEEDS = range(5)

#: Minimum scores. The target is exact: every well-formed lookup returns the
#: planted values, and every ambiguous or missing label is reported as such.
MINIMUM = {
    "newest_file_accuracy": 1.0,
    "older_flag_precision": 1.0,
    "older_flag_recall": 1.0,
    "value_exact_match": 1.0,
    "period_label_match": 1.0,
    "ambiguity_precision": 1.0,
    "ambiguity_recall": 1.0,
    "not_found_precision": 1.0,
    "not_found_recall": 1.0,
    "xlsx_chart_round_trip": 1.0,
    "pptx_chart_round_trip": 1.0,
}


def _ratio(num: int, den: int) -> float:
    return 1.0 if den == 0 else num / den


@pytest.fixture(scope="module")
def scores(tmp_path_factory) -> Dict[str, float]:
    root = tmp_path_factory.mktemp("rfs-scenarios")
    c: Dict[str, int] = defaultdict(int)
    per_type: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for make in SCENARIOS:
        for seed in SEEDS:
            sc = make(root, seed)
            inv = inventory_folder(sc.folder)
            newest = {f: d["newest"] for d in inv["datasets"] for f in d["files"]}
            for f, want in sc.newest.items():
                c["newest_total"] += 1
                c["newest_ok"] += newest.get(f) == want
            flagged = {
                d["file"]
                for d in inv["data_files"]
                if d["role"].startswith("older version")
            }
            planted = set(sc.older)
            c["older_tp"] += len(flagged & planted)
            c["older_fp"] += len(flagged - planted)
            c["older_fn"] += len(planted - flagged)
            for a, b in sc.distinct_datasets:
                if newest.get(a) == newest.get(b):
                    c["older_fp"] += 1  # two unrelated files grouped as one dataset
            for name in sc.narrative:
                assert name in {n["file"] for n in inv.get("narrative_sources", [])}

            for lk in sc.lookups:
                try:
                    result = extract_series(
                        sc.folder / lk.file,
                        lk.labels,
                        sheet=lk.sheet or None,
                        periods=lk.periods,
                        filters=lk.filters or None,
                    )
                    status = "success"
                except SeriesLookupError as e:
                    problems = e.payload.get("problems") or [{}]
                    status = problems[0].get("status", "error")
                    result = e.payload
                reported_amb = status == "ambiguous"
                planted_amb = lk.expect_status == "ambiguous"
                c["amb_tp"] += reported_amb and planted_amb
                c["amb_fp"] += reported_amb and not planted_amb
                c["amb_fn"] += planted_amb and not reported_amb
                reported_nf = status == "not_found"
                planted_nf = lk.expect_status == "not_found"
                c["nf_tp"] += reported_nf and planted_nf
                c["nf_fp"] += reported_nf and not planted_nf
                c["nf_fn"] += planted_nf and not reported_nf
                if planted_amb and reported_amb:
                    labels = {
                        cand["label"] for cand in result["problems"][0]["candidates"]
                    }
                    assert set(lk.expect_candidates) <= labels, (sc.name, labels)
                if lk.expect_status != "success":
                    continue
                per_type[sc.name]["lookups"] += 1
                c["series_total"] += len(lk.expect_values)
                c["period_total"] += 1
                if status != "success":
                    continue
                c["period_ok"] += result["periods"] == lk.expect_periods
                got = {e["matched_label"]: e["values"] for e in result["series"]}
                for label, values in lk.expect_values.items():
                    ok = (
                        got.get(label) == values
                        and result["periods"] == lk.expect_periods
                    )
                    c["series_ok"] += ok
                    per_type[sc.name]["series_ok"] += ok

                ci = result["chart_input"]
                c["chart_total"] += 1
                xlsx = write_workbook_chart(
                    root / f"{sc.name}_{seed}_{c['chart_total']}.xlsx",
                    ci["categories"],
                    ci["series"],
                    title=lk.labels[0],
                    source_note=ci["source_note"],
                )
                c["xlsx_ok"] += xlsx["verified"] and xlsx["read_back"] == ci["series"]
                deck = add_native_chart(
                    root / f"{sc.name}_{seed}.pptx",
                    "new",
                    "line",
                    ci["categories"],
                    ci["series"],
                    lk.labels[0],
                    "",
                    ci["source_note"],
                )
                c["pptx_ok"] += deck["verified"] and deck["read_back"] == ci["series"]

    out = {
        "newest_file_accuracy": _ratio(c["newest_ok"], c["newest_total"]),
        "older_flag_precision": _ratio(c["older_tp"], c["older_tp"] + c["older_fp"]),
        "older_flag_recall": _ratio(c["older_tp"], c["older_tp"] + c["older_fn"]),
        "value_exact_match": _ratio(c["series_ok"], c["series_total"]),
        "period_label_match": _ratio(c["period_ok"], c["period_total"]),
        "ambiguity_precision": _ratio(c["amb_tp"], c["amb_tp"] + c["amb_fp"]),
        "ambiguity_recall": _ratio(c["amb_tp"], c["amb_tp"] + c["amb_fn"]),
        "not_found_precision": _ratio(c["nf_tp"], c["nf_tp"] + c["nf_fp"]),
        "not_found_recall": _ratio(c["nf_tp"], c["nf_tp"] + c["nf_fn"]),
        "xlsx_chart_round_trip": _ratio(c["xlsx_ok"], c["chart_total"]),
        "pptx_chart_round_trip": _ratio(c["pptx_ok"], c["chart_total"]),
    }
    print("\nreport-from-sources scenario scores:")
    for key, value in out.items():
        print(f"  {key:24s} {value:.3f}")
    print(f"  counts: {dict(c)}")
    return out


@pytest.mark.parametrize("metric", sorted(MINIMUM))
def test_scenario_scores_meet_the_minimum(scores, metric):
    assert scores[metric] >= MINIMUM[metric], f"{metric} = {scores[metric]:.3f}"


def test_every_scenario_type_was_exercised():
    assert len(SCENARIOS) >= 6
