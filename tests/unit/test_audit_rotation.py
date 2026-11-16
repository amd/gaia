# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One proactive audit a night, in a rotation that never drifts.

Three audits every night spent the Claude weekly cap mid-week. The rotation keys
off days since the epoch, so it is identical on every runner and continuous over
month and year ends, and appending an audit is the only change a fourth needs.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "audit" / "audit_rotation.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


@pytest.fixture(scope="module")
def rot():
    spec = importlib.util.spec_from_file_location("audit_rotation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(rot, start: date, nights: int) -> list[str]:
    return [rot.pick(start + timedelta(days=i))[0] for i in range(nights)]


def test_each_audit_runs_exactly_once_every_n_nights(rot):
    n = len(rot.ROTATION)
    picks = _run(rot, date(2026, 10, 1), n * 40)
    for workflow, _ in rot.ROTATION:
        nights = [i for i, w in enumerate(picks) if w == workflow]
        assert len(nights) == 40
        assert {b - a for a, b in zip(nights, nights[1:])} == {n}


@pytest.mark.parametrize(
    "boundary",
    [date(2026, 12, 31), date(2027, 2, 28), date(2028, 2, 28), date(2026, 10, 31)],
)
def test_the_cycle_is_unbroken_across_month_and_year_ends(rot, boundary):
    """A day-of-month rule restarts on the 1st; this must not."""
    n = len(rot.ROTATION)
    picks = _run(rot, boundary - timedelta(days=n), 2 * n + 1)
    for i in range(n, len(picks)):
        assert picks[i] == picks[i - n]


def test_the_pick_depends_only_on_the_date(rot):
    assert rot.pick(date(2026, 10, 20)) == rot.pick(date(2026, 10, 20))
    assert (
        rot.pick(date(2026, 10, 20))[2] == (date(2026, 10, 20) - date(1970, 1, 1)).days
    )


def test_a_fourth_audit_only_needs_appending(rot, monkeypatch):
    four = rot.ROTATION + (("claude-new-audit.yml", "new audit"),)
    monkeypatch.setattr(rot, "ROTATION", four)
    picks = _run(rot, date(2026, 10, 1), 40)
    assert {w for w in picks} == {w for w, _ in four}
    nights = [i for i, w in enumerate(picks) if w == "claude-new-audit.yml"]
    assert {b - a for a, b in zip(nights, nights[1:])} == {4}


def test_every_rotated_workflow_exists_and_accepts_the_dispatch(rot):
    """The dispatcher passes rotation_night; a workflow without it rejects the run."""
    for workflow, _ in rot.ROTATION:
        path = WORKFLOWS / workflow
        assert path.is_file(), f"{workflow} is in the rotation but does not exist"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = data.get("on") or data.get(True)
        inputs = triggers["workflow_dispatch"]["inputs"]
        assert "rotation_night" in inputs, f"{workflow} has no rotation_night input"
        assert "schedule" not in triggers, (
            f"{workflow} still has its own cron; the rotation must be the only "
            "scheduler or it runs on nights that belong to another audit"
        )
        assert "rotation night" in data["run-name"], (
            f"{workflow}'s run-name must mark rotation runs; audit_history.py reads "
            "only those as history"
        )


def test_cli_prints_the_pick_and_writes_the_upcoming_nights(rot, tmp_path, capsys):
    summary = tmp_path / "s.md"
    rot.main(["--date", "2026-12-31", "--summary", str(summary)])
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert out["date"] == "2026-12-31"
    assert out["workflow"] == rot.pick(date(2026, 12, 31))[0]
    text = summary.read_text(encoding="utf-8")
    assert "| 2027-01-05 |" in text
