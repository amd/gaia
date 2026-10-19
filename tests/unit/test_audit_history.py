# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tonight's audit scope comes from what earlier runs finished, not the clock.

Both daily audits failed 5 of 10 nights in October on the Claude weekly limit.
The static audit then looked back a fixed window, so each failed night's commits
were never reviewed; the walkthrough re-walked every guide on Monday regardless.
These tests pin the carry-over: a failed turn's work is picked up by the next.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit" / "audit_history.py"

# 2026-10-19 is a Monday.
MONDAY = datetime(2026, 10, 19, 10, 47, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def hist():
    spec = importlib.util.spec_from_file_location("audit_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(day: int, conclusion: str = "success", hour: int = 10, **extra):
    return {
        "created_at": f"2026-10-{day:02d}T{hour:02d}:47:00Z",
        "conclusion": conclusion,
        **extra,
    }


# ----------------------------------------------------------------------
# window
# ----------------------------------------------------------------------

DEEP_EVERY = timedelta(days=8)


def rot_run(
    day: int, conclusion: str = "success", mode: str = "normal", month: int = 10
):
    """A rotation-dispatched code-audit run, its lens jobs named for its mode."""
    return {
        "created_at": f"2026-{month:02d}-{day:02d}T10:20:00Z",
        "conclusion": conclusion,
        "event": "workflow_dispatch",
        "displayTitle": "Claude Code Audit — rotation night 20745",
        "jobs": [{"name": f"audit (docs, {mode})", "conclusion": conclusion}],
    }


def test_a_failed_turn_is_covered_by_the_next_one(hist):
    """The 16th died on the usage limit, so the 19th reviews from the 13th."""
    history = [rot_run(16, "failure"), rot_run(13, mode="deep"), rot_run(10)]
    out = hist.resolve_window(MONDAY, "auto", "", history, DEEP_EVERY)
    assert out["mode"] == "normal"
    assert out["since"].startswith("2026-10-13T10:20")


def test_a_clean_streak_reviews_since_the_previous_turn(hist):
    history = [rot_run(16), rot_run(13, mode="deep")]
    out = hist.resolve_window(MONDAY, "auto", "", history, DEEP_EVERY)
    assert out["since"].startswith("2026-10-16")


def test_deep_runs_when_the_last_deep_sweep_is_over_the_interval(hist):
    """Every third code-audit night: deep on the 10th, normal 13th/16th, deep 19th."""
    history = [rot_run(16), rot_run(13), rot_run(10, mode="deep")]
    out = hist.resolve_window(MONDAY, "auto", "", history, DEEP_EVERY)
    assert out["mode"] == "deep"
    assert "9 days ago" in out["reason"]


def test_a_failed_deep_sweep_is_retried_on_the_next_turn(hist):
    history = [
        rot_run(16, "failure", mode="deep"),
        rot_run(13),
        rot_run(7, mode="deep"),
    ]
    later = datetime(2026, 10, 19, 10, 20, tzinfo=timezone.utc)
    assert hist.resolve_window(later, "auto", "", history, DEEP_EVERY)["mode"] == "deep"


def test_a_recent_deep_sweep_keeps_the_next_turns_normal(hist):
    history = [rot_run(16, mode="deep")]
    out = hist.resolve_window(MONDAY, "auto", "", history, DEEP_EVERY)
    assert out["mode"] == "normal"


def test_a_legacy_sunday_cron_run_counts_as_a_deep_sweep(hist):
    """Continuity across the switch: the old cron resolved Sundays to deep."""
    legacy = {
        "created_at": "2026-10-18T10:42:00Z",
        "conclusion": "success",
        "event": "schedule",
        "displayTitle": "Claude Code Audit",
    }
    out = hist.resolve_window(MONDAY, "auto", "", [legacy], DEEP_EVERY)
    assert out["mode"] == "normal"
    assert out["since"].startswith("2026-10-18T10:42")


def test_no_deep_sweep_on_record_runs_one(hist):
    out = hist.resolve_window(MONDAY, "auto", "", [rot_run(16)], DEEP_EVERY)
    assert out["mode"] == "deep"


def test_without_a_deep_interval_history_alone_decides(hist):
    """The security audit passes no interval: it only needs the since-window."""
    out = hist.resolve_window(MONDAY, "auto", "", [rot_run(16)], None)
    assert out["mode"] == "normal"


def test_a_long_outage_becomes_a_deep_sweep(hist):
    out = hist.resolve_window(MONDAY, "auto", "", [rot_run(9)], None)
    assert out["mode"] == "deep"
    assert "10 days ago" in out["reason"]


def test_no_history_at_all_is_a_deep_sweep_not_a_guess(hist):
    out = hist.resolve_window(MONDAY, "auto", "", [], None)
    assert out["mode"] == "deep"


def test_an_explicit_window_overrides_the_history(hist):
    out = hist.resolve_window(MONDAY, "auto", "3", [rot_run(16)], DEEP_EVERY)
    assert out["mode"] == "normal"
    assert out["since"].startswith("2026-10-16T10:47")


def test_explicit_normal_mode_never_turns_deep(hist):
    out = hist.resolve_window(MONDAY, "normal", "", [rot_run(16)], DEEP_EVERY)
    assert out["mode"] == "normal"


def test_an_unknown_mode_is_refused(hist):
    with pytest.raises(SystemExit):
        hist.resolve_window(MONDAY, "fast", "", [], None)


def test_cancelled_and_skipped_runs_do_not_advance_the_window(hist):
    history = [rot_run(18, "cancelled"), rot_run(17, "skipped"), rot_run(16)]
    out = hist.resolve_window(MONDAY, "auto", "", history, None)
    assert out["since"].startswith("2026-10-16")


@pytest.mark.parametrize(
    "event, title, counts",
    [
        ("schedule", "Claude Code Audit", True),
        ("workflow_dispatch", "Claude Code Audit — rotation night 20745", True),
        ("workflow_dispatch", "Claude Code Audit", False),
        ("push", "Claude Code Audit", False),
    ],
)
def test_only_rotation_and_legacy_cron_runs_are_history(hist, event, title, counts):
    """A hand-dispatched run may be a dry run that filed nothing."""
    run = {"event": event, "displayTitle": title}
    assert hist.counts_as_history(run) is counts


def test_window_cli_prints_github_output_lines(hist, tmp_path, capsys):
    history = tmp_path / "h.json"
    history.write_text(json.dumps([rot_run(16, mode="deep")]), encoding="utf-8")
    hist.main(
        [
            "window",
            "--repo",
            "amd/gaia",
            "--workflow",
            "claude-nightly-audit.yml",
            "--deep-every-days",
            "8",
            "--history-file",
            str(history),
            "--now",
            MONDAY.isoformat(),
        ]
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "mode=normal"
    assert lines[1].startswith("since=2026-10-16")
    assert lines[2].startswith("reason=")


def test_a_gh_failure_stops_the_run_instead_of_guessing(hist, monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "HTTP 403"

    monkeypatch.setattr(hist.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(SystemExit) as excinfo:
        hist.fetch_history("amd/gaia", "x.yml", with_jobs=False)
    assert "Refusing to guess" in str(excinfo.value)


def test_fetch_history_drops_hand_dispatched_runs(hist, monkeypatch):
    runs = [
        {
            "databaseId": 1,
            "createdAt": "2026-10-18T10:20:00Z",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "displayTitle": "X — rotation night 20744",
            "headSha": "a1",
        },
        {
            "databaseId": 2,
            "createdAt": "2026-10-17T15:00:00Z",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "displayTitle": "X",
            "headSha": "b2",
        },
    ]
    calls = []

    def fake_gh(args):
        calls.append(args)
        return json.dumps(runs)

    monkeypatch.setattr(hist, "_gh", fake_gh)
    history = hist.fetch_history("amd/gaia", "x.yml", with_jobs=False)
    assert [h["created_at"] for h in history] == ["2026-10-18T10:20:00Z"]
    assert calls[0][calls[0].index("--branch") + 1] == "main"


# ----------------------------------------------------------------------
# rotate
# ----------------------------------------------------------------------

DOCS = [
    {"path": f"docs/guides/g{i}.mdx", "slug": f"docs-guides-g{i}"} for i in range(1, 8)
]


def walk_run(day: int, walked: list[int], synthesized: bool = True, failed=()):
    jobs = [
        {
            "name": f"walkthrough (docs/guides/g{i}.mdx, docs-guides-g{i})",
            "conclusion": "success",
        }
        for i in walked
    ] + [
        {
            "name": f"walkthrough (docs/guides/g{i}.mdx, docs-guides-g{i})",
            "conclusion": "failure",
        }
        for i in failed
    ]
    jobs.append(
        {"name": "synthesize", "conclusion": "success" if synthesized else "failure"}
    )
    return run(day, "success" if synthesized else "failure", jobs=jobs)


def _paths(chosen):
    return [d["path"].split("/")[-1] for d in chosen]


def test_never_walked_guides_go_first(hist):
    walked = hist.last_walked([walk_run(18, [1, 2, 3])])
    assert _paths(hist.select_docs(DOCS, walked, 3)) == ["g4.mdx", "g5.mdx", "g6.mdx"]


def test_the_least_recently_walked_guides_go_next(hist):
    history = [walk_run(18, [4, 5, 6, 7]), walk_run(17, [1, 2]), walk_run(16, [3])]
    walked = hist.last_walked(history)
    assert _paths(hist.select_docs(DOCS, walked, 3)) == ["g3.mdx", "g1.mdx", "g2.mdx"]


def test_a_night_stopped_by_the_usage_limit_puts_its_guides_back_in_line(hist):
    """Synthesis refused the partial sweep, so nothing from that night was filed."""
    history = [
        walk_run(18, [1, 2], synthesized=False, failed=[3, 4, 5]),
        walk_run(11, [1, 2, 3, 4, 5, 6, 7]),
    ]
    walked = hist.last_walked(history)
    assert walked["docs/guides/g1.mdx"].day == 11
    chosen = _paths(hist.select_docs(DOCS, walked, 5))
    assert chosen == ["g1.mdx", "g2.mdx", "g3.mdx", "g4.mdx", "g5.mdx"]


def test_a_failed_guide_in_a_filed_run_is_not_counted_as_walked(hist):
    walked = hist.last_walked([walk_run(18, [1], failed=[2])])
    assert "docs/guides/g2.mdx" not in walked


def test_five_a_night_covers_thirty_guides_within_a_week(hist):
    """The cadence promise: a daily rotation still reaches every guide each week,
    and the seventh night starts over with the guides walked first."""
    docs = [
        {"path": f"docs/guides/d{i:02d}.mdx", "slug": f"d{i:02d}"} for i in range(30)
    ]
    history: list = []
    nights = []
    for day in range(12, 19):
        chosen = hist.select_docs(docs, hist.last_walked(history), 5)
        nights.append({d["path"] for d in chosen})
        jobs = [
            {"name": f"walkthrough ({d['path']}, {d['slug']})", "conclusion": "success"}
            for d in chosen
        ] + [{"name": "synthesize", "conclusion": "success"}]
        history.insert(0, run(day, jobs=jobs))
    assert set().union(*nights[:6]) == {d["path"] for d in docs}
    assert nights[6] == nights[0]


def test_a_count_below_one_is_refused(hist):
    with pytest.raises(SystemExit):
        hist.select_docs(DOCS, {}, 0)


def test_rotate_cli_writes_the_chosen_docs_and_a_summary(hist, tmp_path, capsys):
    history = tmp_path / "h.json"
    history.write_text(json.dumps([walk_run(18, [1, 2, 3, 4, 5, 6])]), encoding="utf-8")
    summary = tmp_path / "summary.md"
    hist.main(
        [
            "rotate",
            "--repo",
            "amd/gaia",
            "--workflow",
            "claude-weekly-doc-walkthrough.yml",
            "--docs-json",
            json.dumps(DOCS),
            "--count",
            "2",
            "--summary",
            str(summary),
            "--history-file",
            str(history),
            "--now",
            MONDAY.isoformat(),
        ]
    )
    chosen = json.loads(capsys.readouterr().out)
    assert _paths(chosen) == ["g7.mdx", "g1.mdx"]
    text = summary.read_text(encoding="utf-8")
    assert "2 of 7 guides tonight" in text
    assert "| `docs/guides/g7.mdx` | never |" in text


# ----------------------------------------------------------------------
# The exact window: the last successful run's commit
# ----------------------------------------------------------------------


def test_the_base_commit_is_the_one_the_last_successful_run_audited(hist):
    history = [
        dict(rot_run(18, "failure"), head_sha="c3"),
        dict(rot_run(16), head_sha="b2"),
        dict(rot_run(13), head_sha="a1"),
    ]
    assert hist.last_success_sha(history) == "b2"


def test_no_successful_run_means_no_base_commit(hist):
    assert hist.last_success_sha([dict(rot_run(18, "failure"), head_sha="c3")]) == ""


def test_window_cli_prints_the_base_commit(hist, tmp_path, capsys):
    history = tmp_path / "h.json"
    history.write_text(
        json.dumps([dict(rot_run(16, mode="deep"), head_sha="abc123")]),
        encoding="utf-8",
    )
    hist.main(
        [
            "window",
            "--repo",
            "amd/gaia",
            "--workflow",
            "claude-nightly-audit.yml",
            "--history-file",
            str(history),
            "--now",
            MONDAY.isoformat(),
        ]
    )
    assert "base_sha=abc123" in capsys.readouterr().out.splitlines()


# ----------------------------------------------------------------------
# A guide a recent merge changed jumps the queue
# ----------------------------------------------------------------------


def test_a_recently_changed_guide_is_walked_first(hist):
    walked = hist.last_walked([walk_run(18, [1, 2, 3, 4, 5, 6, 7])])
    chosen = hist.select_docs(DOCS, walked, 2, frozenset({"docs/guides/g6.mdx"}))
    assert _paths(chosen)[0] == "g6.mdx"


def test_changed_paths_that_are_not_guides_change_nothing(hist):
    walked = hist.last_walked([walk_run(18, [1, 2, 3])])
    plain = hist.select_docs(DOCS, walked, 3)
    with_code = hist.select_docs(DOCS, walked, 3, frozenset({"src/gaia/cli.py"}))
    assert plain == with_code


def test_rotate_cli_reads_changed_paths(hist, tmp_path, capsys):
    history = tmp_path / "h.json"
    history.write_text(
        json.dumps([walk_run(18, [1, 2, 3, 4, 5, 6, 7])]), encoding="utf-8"
    )
    changed = tmp_path / "changed.txt"
    changed.write_text("src/x.py\ndocs/guides/g5.mdx\n", encoding="utf-8")
    summary = tmp_path / "s.md"
    hist.main(
        [
            "rotate",
            "--repo",
            "amd/gaia",
            "--workflow",
            "claude-weekly-doc-walkthrough.yml",
            "--docs-json",
            json.dumps(DOCS),
            "--count",
            "1",
            "--summary",
            str(summary),
            "--changed-paths",
            str(changed),
            "--history-file",
            str(history),
            "--now",
            MONDAY.isoformat(),
        ]
    )
    assert _paths(json.loads(capsys.readouterr().out)) == ["g5.mdx"]
    assert "| `docs/guides/g5.mdx` | 2026-10-18 | yes |" in summary.read_text(
        encoding="utf-8"
    )
