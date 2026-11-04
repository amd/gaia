# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The release-testing skill's scripts: planning, the report's finish gate, evidence PNGs."""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "skills"
    / "testing-agent-ui-release"
    / "scripts"
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"skill_{name}", SCRIPTS / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scope = _load("release_scope")
report = _load("test_report")


# ── release_scope ────────────────────────────────────────────────────────


def test_surfaces_ignore_tests_docs_and_ci():
    files = [
        "src/gaia/apps/webui/src/components/ChatView.tsx",
        "src/gaia/apps/webui/src/components/__tests__/ChatView.test.tsx",
        "docs/guides/agent-ui.mdx",
        ".github/workflows/x.yml",
        "src/gaia/ui/routers/chat.py",
    ]
    assert scope.surfaces_for(files) == ["agent-ui", "ui-backend"]
    assert scope.surfaces_for(["tests/unit/test_x.py", "README.md"]) == []


def test_electron_shell_wins_over_the_webui_folder():
    assert scope.surfaces_for(["src/gaia/apps/webui/main.cjs"]) == ["electron-shell"]


@pytest.mark.parametrize(
    "kind, surfaces, expected",
    [
        ("fix", ["agent-ui"], "P1"),
        ("feat", ["agent"], "P1"),
        ("refactor", ["ui-backend"], "P2"),
        ("fix", ["tui"], "P3"),
        ("fix", [], "skip"),
    ],
)
def test_priority(kind, surfaces, expected):
    assert scope.classify(kind, surfaces)[0] == expected


def test_test_plan_keeps_app_steps_and_drops_dev_commands():
    body = (
        "## Why\nstuff\n\n## Test plan\n"
        "- [x] `python -m pytest tests/unit/test_x.py`\n"
        "- [ ] `cd tui && go test ./...`\n"
        "- [x] python util/lint.py --all\n"
        "- [ ] Pick Gemma NPU in the Agent UI, restart, it reopens on the NPU model\n"
        "\n## Notes\n- not a step\n"
    )
    assert scope.test_plan(body) == [
        "Pick Gemma NPU in the Agent UI, restart, it reopens on the NPU model"
    ]
    assert scope.test_plan("no plan here") == []


# ── test_report: the finish gate ─────────────────────────────────────────


def _run(tmp_path, cmd, **kw):
    getattr(report, f"cmd_{cmd}")(SimpleNamespace(run=tmp_path, **kw))


def _init(tmp_path):
    _run(
        tmp_path,
        "init",
        build="rc",
        machine=None,
        scope=None,
        include="P1,P2",
        force=False,
    )
    _run(
        tmp_path,
        "add",
        title="Model picker",
        area="models",
        priority="P1",
        expect=None,
        source=None,
    )


def _set(tmp_path, item, status, note="seen", evidence=None):
    _run(tmp_path, "set", item=item, status=status, note=note, evidence=evidence)


def _link(tmp_path, f, status=None, note=None, issue=None, pr=None):
    _run(tmp_path, "link", finding=f, status=status, note=note, issue=issue, pr=pr)


def _problems(tmp_path):
    return report.problems(report.load(tmp_path))


def test_gate_holds_until_a_retest_round_passes_clean(tmp_path):
    _init(tmp_path)
    assert any("still planned" in p for p in _problems(tmp_path))

    _run(
        tmp_path,
        "find",
        title="Never switches",
        severity="high",
        area=None,
        items="T1",
        repro="pick it",
        expected="switches",
        actual="stays",
        evidence=None,
    )
    _link(tmp_path, "F1", issue="https://github.com/amd/gaia/issues/1")
    _link(tmp_path, "F1", pr="https://github.com/amd/gaia/pull/2", status="fixed")
    left = _problems(tmp_path)
    assert any("F1 is fixed" in p for p in left)
    assert any("another retest round" in p for p in left)

    _run(tmp_path, "round", label="retest")
    _run(tmp_path, "retest")
    rep = report.load(tmp_path)
    assert [i["id"] for i in rep["items"]] == ["T1", "R2-F1"]
    assert rep["items"][1]["area"] == "models"

    _set(tmp_path, "R2-F1", "passed")
    _link(tmp_path, "F1", status="verified", note="R2-F1 passed")
    assert _problems(tmp_path) == []
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert md.startswith("# Agent UI release test: rc")
    assert "**Finished.**" in md
    assert "failed, fix verified (F1)" in md


def test_verified_without_a_passing_retest_is_not_finished(tmp_path):
    _init(tmp_path)
    _run(
        tmp_path,
        "find",
        title="Bug",
        severity="low",
        area=None,
        items="T1",
        repro="",
        expected="",
        actual="",
        evidence=None,
    )
    _run(tmp_path, "round", label="retest")
    _link(tmp_path, "F1", status="verified", note="looked fine")
    assert any("no retest item passed" in p for p in _problems(tmp_path))


def test_a_failed_retest_reopens_its_finding_without_a_new_one(tmp_path):
    _init(tmp_path)
    _run(
        tmp_path,
        "find",
        title="Bug",
        severity="high",
        area=None,
        items="T1",
        repro="",
        expected="",
        actual="",
        evidence=None,
    )
    _link(tmp_path, "F1", status="fixed")
    _run(tmp_path, "round", label="retest")
    _run(tmp_path, "retest")
    _set(tmp_path, "R2-F1", "failed", note="still broken")
    rep = report.load(tmp_path)
    assert rep["findings"][0]["status"] == "reopened"
    left = report.problems(rep)
    assert not any("no finding recorded" in p for p in left)
    assert any("F1 is reopened" in p for p in left)


def test_blocked_items_keep_the_pass_open(tmp_path):
    _init(tmp_path)
    _set(tmp_path, "T1", "blocked", note="no NPU here")
    assert any("still blocked" in p for p in _problems(tmp_path))


def test_failures_and_skips_must_say_why(tmp_path):
    _init(tmp_path)
    for status in ("failed", "not-tested", "blocked"):
        with pytest.raises(SystemExit):
            _set(tmp_path, "T1", status, note=None)
    with pytest.raises(SystemExit):
        _set(tmp_path, "T1", "passed", note=None, evidence=None)


def test_init_from_scope_marks_unreachable_changes_out_of_scope(tmp_path):
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(
        json.dumps(
            {
                "base": "v1",
                "target": "v2",
                "items": [
                    {
                        "id": "PR1",
                        "title": "fix(ui): a",
                        "surfaces": ["agent-ui"],
                        "priority": "P1",
                        "url": "u1",
                        "issues": [],
                        "test_plan": ["click it"],
                        "why": "w",
                    },
                    {
                        "id": "PR2",
                        "title": "fix(tui): b",
                        "surfaces": ["tui"],
                        "priority": "P3",
                        "url": "u2",
                        "issues": [],
                        "test_plan": [],
                        "why": "not reachable from the Agent UI (tui)",
                    },
                    {
                        "id": "PR3",
                        "title": "docs: c",
                        "surfaces": [],
                        "priority": "skip",
                        "url": "u3",
                        "issues": [],
                        "test_plan": [],
                        "why": "docs",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _run(
        tmp_path,
        "init",
        build="v2",
        machine=None,
        scope=scope_file,
        include="P1,P2",
        force=False,
    )
    rep = report.load(tmp_path)
    assert {i["id"]: i["status"] for i in rep["items"]} == {
        "PR1": "planned",
        "PR2": "out-of-scope",
    }
    assert rep["items"][0]["steps"] == ["click it"]


# ── capture_window: the PNG it writes is valid ──────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="capture_window loads Win32 DLLs")
def test_png_writer_round_trips_pixels(tmp_path):
    capture = _load("capture_window")
    bgra = bytes([0, 0, 255, 255, 0, 255, 0, 255])  # red, green
    out = tmp_path / "x.png"
    capture.write_png(out, 2, 1, bgra)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (2, 1)
    idat = data.index(b"IDAT")
    (length,) = struct.unpack(">I", data[idat - 4 : idat])
    assert (
        zlib.decompress(data[idat + 4 : idat + 4 + length])
        == b"\x00\xff\x00\x00\x00\xff\x00"
    )


# ── pc_evals: which permission prompts the benchmark driver allows ──────


evals = _load("pc_evals")
TASK = r"C:\Users\me\kw-eval\runs\gaia-agent-ui\assistant-1\as-res-convert-units"


def _prompt(question: str, target: str) -> str:
    return "\n".join([question, "", target, "", "Details", "Allow once", "Deny"])


@pytest.mark.parametrize(
    "question, target, allowed",
    [
        ("Let GAIA use this folder?", TASK, True),
        ("Allow writing file?", TASK + r"\summary.md", True),
        ("Allow running a shell command?", "python tools/convert_units.py 72", True),
        ("Allow running a shell command?", r"type C:\Users\me\.ssh\id_rsa", False),
        ("Allow running a shell command?", r"type ..\..\secrets.txt", False),
        ("Let GAIA use this folder?", r"C:\Users\me\Documents", False),
        ("Allow running a shell command?", r"type \\server\share\x.txt", False),
    ],
)
def test_benchmark_driver_allows_only_the_task_folder(question, target, allowed):
    assert evals.approve(_prompt(question, target), TASK) is allowed
