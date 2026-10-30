# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/check_release_ci.py: a release only publishes a commit CI passed.

Most commits on main have no passing run of their own (the next merge cancels
it, path filters skip others), so the gate dispatches what is missing against
the tag and waits. These tests pin that it never treats "no evidence" as green,
never dispatches the same workflow twice, and ignores the stale runs that made
it dispatch.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_release_ci as crc  # noqa: E402

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def run(conclusion, status="completed", minutes=0, run_id=1):
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://example/run/{run_id}",
        "created_at": (T0 + timedelta(minutes=minutes))
        .isoformat()
        .replace("+00:00", "Z"),
    }


class Clock:
    def __init__(self):
        self.t = T0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


def test_required_workflows_accept_a_dispatch():
    for wf in crc.REQUIRED_WORKFLOWS:
        text = (REPO_ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
        assert "workflow_dispatch:" in text, wf


@pytest.mark.parametrize(
    "runs, state",
    [
        ([run("success")], "success"),
        # One success on the commit is enough, whatever else ran.
        ([run("cancelled", minutes=5, run_id=2), run("success")], "success"),
        ([run(None, status="in_progress")], "pending"),
        ([run("failure")], "failed"),
        ([run("cancelled")], "failed"),
        ([], "missing"),
    ],
)
def test_judge(runs, state):
    assert crc.judge("lint.yml", runs, "amd/gaia", "v1.0.0-rc1").state == state


def test_already_green_needs_no_dispatch():
    started = []
    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        ("a.yml", "b.yml"),
        lambda w: [run("success")],
        started.append,
        wait_minutes=10,
    )
    assert started == []
    assert {v.state for v in verdicts} == {"success"}


def test_missing_and_cancelled_are_dispatched_once_then_waited_for():
    clock = Clock()
    started: list[str] = []
    polls = {"n": 0}

    def get_runs(w):
        stale = [run("cancelled", minutes=-60)] if w == "cancelled.yml" else []
        if w not in started:
            return stale
        polls["n"] += 1
        mine = clock.now() - T0
        created = int(mine.total_seconds() // 60)
        if polls["n"] <= 2:
            return stale  # the dispatched run has not appeared yet
        if polls["n"] <= 4:
            return stale + [run(None, status="in_progress", minutes=created, run_id=9)]
        return stale + [run("success", minutes=created, run_id=9)]

    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        ("missing.yml", "cancelled.yml"),
        get_runs,
        started.append,
        wait_minutes=30,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert sorted(started) == ["cancelled.yml", "missing.yml"]
    assert {v.state for v in verdicts} == {"success"}


def test_a_dispatched_run_that_fails_stops_the_release():
    clock = Clock()
    started: list[str] = []

    def get_runs(w):
        if w not in started:
            return []
        return [run("failure", minutes=int((clock.now() - T0).total_seconds() // 60))]

    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        ("a.yml",),
        get_runs,
        started.append,
        wait_minutes=30,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert started == ["a.yml"]
    (v,) = verdicts
    assert v.state == "failed"
    ok, text = crc.report(verdicts, "abc123")
    assert not ok and "gh run rerun" in text


def test_the_stale_failure_does_not_count_after_dispatch():
    # The old failed run is why we dispatched; it must not end the wait.
    clock = Clock()
    started: list[str] = []
    old_failure = run("failure", minutes=-30)

    def get_runs(w):
        return [old_failure]

    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        ("a.yml",),
        get_runs,
        started.append,
        wait_minutes=5,
        poll_seconds=60,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert started == ["a.yml"]
    (v,) = verdicts
    # Timed out waiting for our run, never "failed" on the stale one.
    assert v.state == "pending"
    assert not crc.report(verdicts, "abc123")[0]


def test_in_progress_on_main_is_waited_for_not_dispatched():
    clock = Clock()
    started: list[str] = []
    states = iter(["in_progress", "in_progress", "done"])

    def get_runs(w):
        s = next(states)
        return [run("success")] if s == "done" else [run(None, status=s)]

    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        ("a.yml",),
        get_runs,
        started.append,
        wait_minutes=30,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert started == []
    assert verdicts[0].state == "success"


def test_main_requires_a_token(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert crc.main(["--repo", "amd/gaia", "--sha", "abc", "--tag", "v1.0.0"]) == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("code, hinted", [(403, True), (404, False), (502, False)])
def test_only_a_403_blames_permissions(monkeypatch, code, hinted):
    import io
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b"body"))

    monkeypatch.setattr(crc.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as exc:
        crc.fetch_runs("amd/gaia", "lint.yml", "abc", "tok")
    assert f"HTTP {code}" in str(exc.value)
    assert ("actions: write" in str(exc.value)) is hinted
