# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The auto-label job must survive a spent installation rate limit and say so when it can't.

Every job in the repo shares one GITHUB_TOKEN rate limit, so under a deep queue
``actions/labeler`` fails on PRs that did nothing wrong (#4107). The job retries
with backoff, and a final failure names the rate limit as an infrastructure
fault rather than looking like a problem with the PR.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "auto-label.yml"
)
BASH = shutil.which("bash")


@pytest.fixture(scope="module")
def steps() -> list[dict]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"]["label"]["steps"]


def _labeler_steps(steps):
    return [s for s in steps if str(s.get("uses", "")).startswith("actions/labeler")]


def test_labeler_is_retried_and_each_attempt_tolerates_failure(steps):
    attempts = _labeler_steps(steps)
    assert len(attempts) >= 2, "labeler runs once; a rate-limited call is never retried"
    for a in attempts:
        assert a.get("continue-on-error") is True, a.get("id")
        assert a["with"]["repo-token"] == "${{ secrets.GITHUB_TOKEN }}"
    for prev, nxt in zip(attempts, attempts[1:]):
        assert nxt["if"] == f"steps.{prev['id']}.outcome == 'failure'"


def test_every_retry_is_preceded_by_a_backoff(steps):
    ids = [s.get("id") for s in steps]
    for a in _labeler_steps(steps)[1:]:
        before = steps[ids.index(a["id"]) - 1]
        assert "sleep" in before.get("run", ""), f"{a['id']} retries without waiting"
        assert before["if"] == a["if"]


def test_final_failure_is_reported_and_fails_the_job(steps):
    last = _labeler_steps(steps)[-1]
    report = steps[-1]
    assert report["if"] == f"steps.{last['id']}.outcome == 'failure'"
    assert "exit 1" in report["run"]


def _fake_bin(tmp_path: Path, remaining: int, reset: int, gh_fails=False) -> dict:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "slept"
    scripts = {
        # Emits what `--jq` would for the query the step asked for.
        "gh": (
            "#!/usr/bin/env bash\n"
            + ('echo "API rate limit exceeded" >&2; exit 1\n' if gh_fails else "")
            + 'case "$*" in\n'
            f'  *remaining*reset*) echo "{remaining} {reset}" ;;\n'
            f"  *remaining*) echo {remaining} ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n"
        ),
        "sleep": f'#!/usr/bin/env bash\necho "$1" > "{log.as_posix()}"\n',
    }
    for name, body in scripts.items():
        p = bindir / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    return {"env": env, "log": log}


def _run(script: str, env: dict, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-e", "-c", script],
        env={**env, **extra},
        capture_output=True,
        text=True,
        timeout=30,
    )


needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


@needs_bash
@pytest.mark.parametrize(
    "remaining, reset_in, max_wait, expected",
    [
        (0, 90, 600, 95),  # spent: wait until the reset, plus a margin
        (0, 3000, 600, 600),  # reset far off: capped
        (0, -30, 600, 1),  # reset already passed: never a zero/negative sleep
        (412, 3000, 600, 30),  # not the rate limit: a short fixed backoff
        (412, 3000, 10, 10),  # the cap also bounds the fixed backoff
    ],
)
def test_backoff_waits_for_the_reset_within_its_cap(
    steps, tmp_path, remaining, reset_in, max_wait, expected
):
    backoff = next(s for s in steps if "sleep" in s.get("run", ""))
    fake = _fake_bin(tmp_path, remaining, int(time.time()) + reset_in)
    proc = _run(backoff["run"], fake["env"], MAX_WAIT=str(max_wait))
    assert proc.returncode == 0, proc.stderr
    slept = int(fake["log"].read_text().strip())
    assert abs(slept - expected) <= 2, (slept, expected)


@needs_bash
@pytest.mark.parametrize(
    "remaining, title",
    [(0, "Auto label (infrastructure)"), (1, "Auto label::")],
)
def test_report_names_the_rate_limit_only_when_it_is_spent(
    steps, tmp_path, remaining, title
):
    fake = _fake_bin(tmp_path, remaining, int(time.time()) + 60)
    proc = _run(steps[-1]["run"], fake["env"])
    assert proc.returncode == 1
    assert f"::error title={title}" in proc.stdout
    if remaining:
        assert "infrastructure" not in proc.stdout


@needs_bash
def test_a_failed_rate_limit_lookup_still_backs_off_and_retries(steps, tmp_path):
    backoff = next(s for s in steps if "sleep" in s.get("run", ""))
    fake = _fake_bin(tmp_path, 0, int(time.time()) + 60, gh_fails=True)
    proc = _run(backoff["run"], fake["env"], MAX_WAIT="600")
    assert proc.returncode == 0, proc.stderr
    assert int(fake["log"].read_text().strip()) == 30


@needs_bash
def test_a_failed_rate_limit_lookup_still_fails_the_job_loudly(steps, tmp_path):
    fake = _fake_bin(tmp_path, 0, int(time.time()) + 60, gh_fails=True)
    proc = _run(steps[-1]["run"], fake["env"])
    assert proc.returncode == 1
    assert "::error title=Auto label::" in proc.stdout
    assert "cause is unknown" in proc.stdout
