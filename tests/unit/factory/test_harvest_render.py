# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end render tests for the three harvest entry points.

``scan`` -> ``report`` -> ``savings`` is the documented pipeline, and the two
renderers shipped crashing on any corpus that happened to contain no shell
commands or no repeated reads: every share was computed as ``100 * n / d`` with
``d`` taken straight from the corpus. A single-session corpus reproduces it, so
these run the real entry points rather than the aggregation helpers.
"""

import json

import pytest

from gaia.factory.harvest import report as report_mod
from gaia.factory.harvest import savings as savings_mod
from gaia.factory.harvest import scan as scan_mod


def _assistant(use_id, tool, args, model="claude-sonnet-5"):
    return {
        "type": "assistant",
        "timestamp": "2026-08-01T10:00:00Z",
        "message": {
            "role": "assistant",
            "id": f"msg_{use_id}",
            "model": model,
            "content": [
                {"type": "tool_use", "id": use_id, "name": tool, "input": args}
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 500,
            },
        },
    }


def _result(use_id, text="ok"):
    return {
        "type": "user",
        "timestamp": "2026-08-01T10:00:01Z",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": use_id, "content": text}
            ],
        },
    }


def _write_corpus(root, records):
    project = root / "projects" / "proj-a"
    project.mkdir(parents=True)
    with (project / "sess-0001.jsonl").open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return root / "projects"


NO_SHELL = [
    {
        "type": "user",
        "timestamp": "2026-08-01T10:00:00Z",
        "cwd": "/repo",
        "gitBranch": "main",
        "message": {"role": "user", "content": "Fix the failing test in foo.py"},
    },
    _assistant("t1", "Read", {"file_path": "/repo/foo.py"}),
    _result("t1", "file contents"),
    _assistant("t2", "Edit", {"file_path": "/repo/foo.py", "old_string": "a"}),
    _result("t2"),
]


WITH_SHELL = NO_SHELL + [
    _assistant("t3", "Bash", {"command": "cd src && pytest -q | head -20"}),
    _result("t3", "2 passed"),
]


def _harvest(tmp_path, monkeypatch, records):
    projects = _write_corpus(tmp_path, records)
    out = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv", ["scan", "--root", str(projects), "--out", str(out)]
    )
    scan_mod.main()
    return projects, out


@pytest.fixture
def harvested(tmp_path, monkeypatch):
    """Run ``scan`` on a shell-free single-session corpus; yield its cache dir."""
    return _harvest(tmp_path, monkeypatch, NO_SHELL)


class TestScan:
    def test_writes_the_three_documented_artifacts(self, harvested):
        _, out = harvested
        for name in ("traces.jsonl", "intents.jsonl", "stats.json"):
            assert (out / name).exists(), f"{name} was not written"

    def test_an_empty_corpus_names_the_directory_it_searched(
        self, tmp_path, monkeypatch
    ):
        empty = tmp_path / "projects"
        empty.mkdir()
        monkeypatch.setattr(
            "sys.argv",
            ["scan", "--root", str(empty), "--out", str(tmp_path / "out")],
        )
        with pytest.raises(SystemExit) as exc:
            scan_mod.main()
        assert str(empty) in str(exc.value) and "--root" in str(exc.value)


class TestRenderers:
    """A corpus with no shell commands must render, not divide by zero."""

    def test_report_renders(self, harvested, monkeypatch, capsys):
        _, out = harvested
        monkeypatch.setattr("sys.argv", ["report", "--cache", str(out)])
        report_mod.main()
        out_text = capsys.readouterr().out
        assert "## " in out_text
        assert "no shell commands" in out_text

    def test_report_renders_shell_shares_when_the_corpus_has_them(
        self, tmp_path, monkeypatch, capsys
    ):
        _, out = _harvest(tmp_path, monkeypatch, WITH_SHELL)
        monkeypatch.setattr("sys.argv", ["report", "--cache", str(out)])
        report_mod.main()
        out_text = capsys.readouterr().out
        assert "rationing space it cannot see" in out_text
        assert "100.0% of shell commands start with `cd`" in out_text

    def test_savings_renders(self, harvested, monkeypatch, capsys):
        projects, out = harvested
        monkeypatch.setattr(
            "sys.argv",
            ["savings", "--cache", str(out), "--projects", str(projects)],
        )
        savings_mod.main()
        assert "Attributed savings" in capsys.readouterr().out


class TestUndefinedShares:
    """An undefined share renders as an em-dash, never as a fabricated 0%."""

    def test_report_pct_marks_a_zero_denominator(self):
        assert report_mod._pct(0, 0) == "—"
        assert report_mod._pct(1, 4) == "25.0%"
        assert report_mod._pct(1, 4, places=0) == "25%"

    def test_savings_pct_marks_a_zero_denominator(self):
        assert savings_mod._pct(0, 0) == "—"
        assert savings_mod._inflation(5, 0) == "—"
        assert savings_mod._inflation(150, 100) == "50%"

    def test_warm_cold_table_says_why_it_cannot_measure(self):
        out = savings_mod.warm_cold_table([{"series": [1, 2]}])
        assert "not measurable" in out
