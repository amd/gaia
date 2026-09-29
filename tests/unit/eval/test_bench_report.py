# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The harness x model table: means over repeats, honest ranges, labelled dollars."""

import json
from pathlib import Path

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import metering, report

JUDGED = {
    "instruction_compliance": 5,
    "work_quality": 5,
    "reasoning": 4,
    "fabrication_free": 5,
    "one_line": "",
}


def _task(i, passed=True, seconds=30.0, steps=5, tokens=(1000, 100), web=()):
    return {
        "id": f"t{i}",
        "check": "mechanical",
        "passed": passed,
        "verified": True,
        "why": "",
        "error": "",
        "error_kind": "",
        "wall_seconds": seconds,
        "steps": steps,
        "tool_calls": steps,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "judge": dict(JUDGED),
        "web_uses": list(web),
        "timed_out": False,
    }


def _run(root: Path, model, harness, usd, source, seconds=30.0, passed=True):
    root.mkdir(parents=True, exist_ok=True)
    tasks = [_task(1, passed=passed, seconds=seconds), _task(2, seconds=seconds)]
    card = {
        "suite": "everyday",
        "model": model,
        "harness": harness,
        "tasks": tasks,
        "cost": {"source": source, "usd": usd, "tokens": 2200},
    }
    (root / "scorecard.json").write_text(json.dumps(card))
    return root


@pytest.mark.parametrize(
    "seconds, text",
    [
        (45, "45s"),
        (59.6, "1:00"),
        (61, "1:01"),
        (456, "7:36"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
    ],
)
def test_time_reads_as_seconds_minutes_or_hours(seconds, text):
    assert report.fmt_time(seconds) == text


def test_a_range_whose_ends_print_the_same_is_hidden():
    assert report.cell_text((4.90, 4.899, 4.901), report.FORMATS["quality"]) == (
        "4.90",
        "",
    )
    assert report.cell_text((4.9, 4.8, 5.0), report.FORMATS["quality"]) == (
        "4.90",
        "4.80–5.00",
    )
    assert report.cell_text((100, 99.6, 100.4), report.fmt_time)[1] == ""


def test_an_api_equivalent_cost_is_labelled_in_both_formats(tmp_path):
    runs = [
        _run(
            tmp_path / "opus",
            "claude-opus-5",
            "claude-code",
            3.89,
            metering.API_EQUIVALENT,
        ),
        _run(
            tmp_path / "glm", "fireworks.glm-5p3-flash", "gaia", 0.09, metering.METERED
        ),
    ]
    rows = report.collect(runs)
    md = report.render_markdown(rows, "t")
    page = report.render_html(rows, "t")
    rows_md = [line for line in md.splitlines() if line.startswith("| `")]
    opus_line = next(line for line in rows_md if "claude-opus-5" in line)
    assert "$3.89<br><sub>API-equivalent</sub>" in opus_line
    glm_line = next(line for line in rows_md if "glm-5p3-flash" in line)
    assert "API-equivalent" not in glm_line.split("|")[12]
    assert '$3.89<span class="src">API-equivalent</span>' in page
    for text in (md, page):
        assert "not money spent" in text and "out-of-pocket" in text


def test_a_row_mixing_cost_sources_is_not_averaged(tmp_path):
    runs = [
        _run(tmp_path / "r1", "m", "gaia", 0.10, metering.METERED),
        _run(tmp_path / "r2", "m", "gaia", 0.20, metering.HARNESS_COUNTS),
    ]
    (row,) = report.collect(runs)
    assert report._cost(row) == ("n/a", "", "mixed cost sources")


def test_repeats_become_one_row_with_a_mean_and_range(tmp_path):
    out = tmp_path / "run"
    _run(out / "r1", "m", "gaia", 0.10, metering.METERED, seconds=300, passed=True)
    _run(out / "r2", "m", "gaia", 0.30, metering.METERED, seconds=600, passed=False)
    (row,) = report.collect([out])
    assert len(row.runs) == 2
    cells = dict(zip([k for _, k in report.COLUMNS], report._cells(row, row)))
    assert cells["passed"][:2] == ("1.5/2", "1–2")
    assert cells["seconds"][:2] == ("15:00", "10:00–20:00")
    assert cells["cost"] == ("$0.200", "$0.100–$0.300", "")


def test_the_report_is_written_and_png_is_skipped_without_chrome(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(report, "find_chrome", lambda: None)
    run = _run(tmp_path / "a", "m", "gaia", 0.1, metering.METERED)
    written = report.write_report([run], tmp_path / "out", "Title")
    assert written["png"] is None
    assert "PNG skipped" in capsys.readouterr().err
    page = written["html"].read_text()
    assert (
        page.startswith("<!doctype html>") and "--bg:#000" in page and "#d4af37" in page
    )
    assert "<script" not in page and "http" not in page.split("<body>")[1]


def test_an_unjudged_run_reports_no_quality_rather_than_zero(tmp_path):
    run = _run(tmp_path / "a", "m", "gaia", 0.1, metering.METERED)
    card = json.loads((run / "scorecard.json").read_text())
    for task in card["tasks"]:
        task["judge"] = {}
    (run / "scorecard.json").write_text(json.dumps(card))
    (row,) = report.collect([run])
    assert report.spread(row, "quality") is None


def test_a_directory_without_runs_is_named(tmp_path):
    with pytest.raises(FileNotFoundError, match="r1, r2"):
        ft.run_dirs(tmp_path)
