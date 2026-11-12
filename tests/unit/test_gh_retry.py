# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The release jobs after 'GitHub Release' wait out a spent API budget.

v0.26.0-rc19 and rc20 published their releases, then failed to attach the
terminal installers, check the release, and redeploy the website: the
repository's shared GITHUB_TOKEN budget was spent, and gh reported the existing
release as "release not found". util/gh_retry.py runs those calls again once the
budget resets, and fails naming the rate limit when the wait would pass its
bound.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import gh_retry  # noqa: E402
import github_release_retry as release_retry  # noqa: E402

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"
NOT_FOUND = "release not found"
CMD = ["gh", "release", "upload", "v0.26.0-rc20", "a.pkg", "--clobber"]


def _done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(CMD, code, out, err)


class _Clock:
    def __init__(self) -> None:
        self.t = 1_000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _run(results, resets, max_wait=3600):
    """Run gh_retry.run with scripted command results and budget readings."""
    clock = _Clock()
    results, resets, calls = list(results), list(resets), []

    def runner(cmd, **_):
        calls.append(cmd)
        return results.pop(0)

    code = gh_retry.run(
        CMD,
        max_wait,
        runner=runner,
        budget_reset=lambda: resets.pop(0),
        sleep=clock.sleep,
        monotonic=lambda: clock.t,
        now=lambda: clock.t,
    )
    assert not results, "the command ran fewer times than scripted"
    return code, calls, clock.sleeps


def test_success_runs_once():
    code, calls, sleeps = _run([_done(0)], [])
    assert (code, len(calls), sleeps) == (0, 1, [])


def test_masked_not_found_with_a_spent_budget_waits_for_the_reset():
    # rc20: the release existed, the budget was spent, gh said "release not found".
    code, calls, sleeps = _run([_done(1, err=NOT_FOUND), _done(0)], [1_000.0 + 600])
    assert code == 0
    assert calls == [CMD, CMD]
    assert sleeps == [600 + gh_retry._RESET_SLACK_SECONDS]


def test_a_real_not_found_fails_at_once():
    code, calls, sleeps = _run([_done(1, err=NOT_FOUND)], [None])
    assert (code, len(calls), sleeps) == (1, 1, [])


def test_other_failures_keep_their_exit_code_and_do_not_retry():
    code, calls, sleeps = _run([_done(4, err="HTTP 422: Validation Failed")], [None])
    assert (code, len(calls), sleeps) == (4, 1, [])


def test_a_secondary_limit_backs_off_without_a_spent_budget():
    secondary = _done(1, err="You have exceeded a secondary rate limit")
    code, _, sleeps = _run([secondary, secondary, _done(0)], [None, None])
    assert code == 0
    assert sleeps == [15, 30]


def test_fails_loudly_naming_the_limit_when_the_wait_passes_the_bound(capsys):
    code, calls, sleeps = _run(
        [_done(1, err=NOT_FOUND)], [1_000.0 + 3_000], max_wait=600
    )
    assert (code, len(calls), sleeps) == (1, 1, [])
    err = capsys.readouterr().err
    assert "::error::" in err
    assert "API budget is spent" in err
    assert "release not found" in err


def test_a_retried_attempts_errors_are_not_annotations(capsys):
    failed = _done(1, err="::error::could not read release v1: release not found")
    _run([failed, _done(0)], [1_000.0 + 10])
    out = capsys.readouterr().out
    assert not re.search(r"(?m)^::error::", out)
    assert "  | ::error::could not read release" in out


def test_main_strips_the_separator(monkeypatch):
    seen = []
    monkeypatch.setattr(
        gh_retry, "run", lambda cmd, wait: seen.append((cmd, wait)) or 0
    )
    assert gh_retry.main(["--max-wait-seconds", "60", "--", *CMD]) == 0
    assert seen == [(CMD, 60)]


def test_main_requires_a_command():
    with pytest.raises(SystemExit):
        gh_retry.main(["--max-wait-seconds", "60", "--"])


# ── The release helper: a masked not-found is a rate limit ──────────────────


def test_release_helper_treats_a_masked_not_found_as_a_rate_limit(monkeypatch):
    monkeypatch.setattr(
        release_retry.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", NOT_FOUND),
    )
    monkeypatch.setattr(release_retry, "core_budget_reset", lambda: 2_000_000_000)
    with pytest.raises(release_retry.RateLimited):
        release_retry._run(["release", "view", "v1", "--json", "assets"])


def test_release_helper_keeps_a_real_not_found(monkeypatch):
    monkeypatch.setattr(
        release_retry.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", NOT_FOUND),
    )
    monkeypatch.setattr(release_retry, "core_budget_reset", lambda: None)
    with pytest.raises(release_retry.ReleaseError, match=NOT_FOUND):
        release_retry._run(["release", "view", "v1", "--json", "assets"])


@pytest.mark.parametrize(
    ("stdout", "code", "expected"),
    [
        ('{"remaining": 0, "reset": 1794485457}', 0, 1794485457),
        ('{"remaining": 12, "reset": 1794485457}', 0, None),
        ("", 1, None),
        ("not json", 0, None),
    ],
)
def test_core_budget_reset(monkeypatch, stdout, code, expected):
    monkeypatch.setattr(
        release_retry.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, code, stdout, ""),
    )
    assert release_retry.core_budget_reset() == expected


# ── publish.yml: every API call after the release goes through the wrapper ──


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


@pytest.mark.parametrize(
    ("job", "call"),
    [
        ("github-release-installers", "gh release upload"),
        ("release-assets-complete", "python3 util/check_release_assets.py --repo"),
        ("redeploy-website", "gh workflow run deploy_website.yml"),
    ],
)
def test_post_release_api_calls_wait_out_the_budget(jobs, job, call):
    runs = "\n".join(str(s.get("run", "")) for s in jobs[job]["steps"])
    assert call in runs, f"{job} no longer runs {call!r}"
    wrapped = re.search(
        r"python3 util/gh_retry\.py --max-wait-seconds (\d+) -- \\\s*\n\s*"
        + re.escape(call),
        runs,
    )
    assert wrapped, f"{job} runs {call!r} without util/gh_retry.py"
    wait_minutes = int(wrapped.group(1)) / 60
    assert (
        jobs[job]["timeout-minutes"] > wait_minutes
    ), f"{job}'s timeout would cancel the wait before the budget resets"
    has_checkout = any(
        str(s.get("uses", "")).startswith("actions/checkout@")
        for s in jobs[job]["steps"]
    )
    assert has_checkout, f"{job} runs util/gh_retry.py with no checkout"
