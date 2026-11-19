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


# ── The Python API the release scripts import ───────────────────────────────


def _run_command(results, resets, max_wait=3600):
    clock = _Clock()
    results, resets, kwargs = list(results), list(resets), []

    def runner(cmd, **kw):
        kwargs.append(kw)
        return results.pop(0)

    done = gh_retry.run_command(
        CMD,
        max_wait,
        timeout=300,
        runner=runner,
        budget_reset=lambda: resets.pop(0),
        sleep=clock.sleep,
        monotonic=lambda: clock.t,
        now=lambda: clock.t,
    )
    return done, kwargs, clock.sleeps


def test_run_command_waits_and_keeps_stdout_for_the_caller(capsys):
    done, kwargs, sleeps = _run_command(
        [_done(1, err="API rate limit exceeded"), _done(0, out="ok")], [None]
    )
    assert (done.returncode, done.stdout, sleeps) == (0, "ok", [15])
    assert all(kw["timeout"] == 300 for kw in kwargs)
    out, err = capsys.readouterr()
    assert out == ""
    assert "  | API rate limit exceeded" in err


def test_run_command_returns_a_real_failure_without_waiting():
    done, kwargs, sleeps = _run_command([_done(1, err="HTTP 404: Not Found")], [None])
    assert (done.returncode, len(kwargs), sleeps) == (1, 1, [])


def test_run_command_raises_naming_the_rate_limit_past_the_bound():
    with pytest.raises(gh_retry.RateLimitWaitExceeded) as exc:
        _run_command([_done(1, err=NOT_FOUND)], [1_000.0 + 3_000], max_wait=600)
    assert "rate limited" in str(exc.value)
    assert "API budget is spent" in str(exc.value)
    assert "release not found" in str(exc.value)


def test_wait_out_rate_limit_lets_other_errors_through_at_once():
    calls = []

    def boom():
        calls.append(1)
        raise ValueError("HTTP 404")

    with pytest.raises(ValueError):
        gh_retry.wait_out_rate_limit(boom, 3600, label="x", sleep=pytest.fail)
    assert calls == [1]


def test_wait_out_rate_limit_prefers_retry_after_to_the_reset():
    clock, results = _Clock(), [release_retry.RateLimited(reset=99_999, retry_after=7)]

    def op():
        if results:
            raise results.pop(0)
        return "ok"

    got = gh_retry.wait_out_rate_limit(
        op, 3600, label="x", sleep=clock.sleep, monotonic=lambda: clock.t
    )
    assert (got, clock.sleeps) == ("ok", [7])


# ── Every GitHub API call in the release path waits out the budget ─────────

RELEASE_WORKFLOWS = ("publish.yml", "release_train_dryrun.yml")
# Imports the shared helper, so the workflow need not wrap it.
SELF_WAITING = {
    "check_release_ci.py",
    "github_release_retry.py",
    "release_promote.py",
    "release_train.py",
}
_API_CALL = re.compile(r"""\[\s*"gh"|api\.github\.com""")


def _run_lines(workflow: str):
    data = yaml.safe_load((WORKFLOW.parent / workflow).read_text(encoding="utf-8"))
    for name, job in data["jobs"].items():
        for step in job.get("steps", []):
            run = str(step.get("run", ""))
            # Join continuations so the wrapper and its command read as one line.
            for line in run.replace("\\\n", " ").splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "echo")):
                    yield name, line


@pytest.mark.parametrize("workflow", RELEASE_WORKFLOWS)
def test_release_api_calls_go_through_the_shared_helper(workflow):
    unwrapped = []
    for job, line in _run_lines(workflow):
        wrapped = "util/gh_retry.py" in line
        if re.search(r"(^|[\s|;&(])gh (api|release|run|workflow)\b", line):
            if not wrapped:
                unwrapped.append(f"{job}: {line}")
        for script in re.findall(r"util/([a-z_]+\.py)", line):
            source = (REPO_ROOT / "util" / script).read_text(encoding="utf-8")
            if not _API_CALL.search(source) or script in SELF_WAITING or wrapped:
                continue
            # check_release_assets --dir reads files on disk, not the API.
            if "--dir" not in line:
                unwrapped.append(f"{job}: {line}")
    assert not unwrapped, "GitHub API calls that die on a spent budget:\n" + (
        "\n".join(unwrapped)
    )


@pytest.mark.parametrize("script", sorted(SELF_WAITING - {"github_release_retry.py"}))
def test_self_waiting_scripts_use_the_shared_helper(script):
    source = (REPO_ROOT / "util" / script).read_text(encoding="utf-8")
    assert "from gh_retry import" in source


@pytest.mark.parametrize(
    ("workflow", "job", "minutes"),
    [
        # The final's 80-minute CI wait plus an hour of rate-limit wait.
        ("publish.yml", "ci-green", 80 + 60),
        ("publish.yml", "verified-build-report", 60),
        ("release_train_dryrun.yml", "plan", 60),
    ],
)
def test_jobs_outlast_the_rate_limit_wait(workflow, job, minutes):
    data = yaml.safe_load((WORKFLOW.parent / workflow).read_text(encoding="utf-8"))
    assert data["jobs"][job]["timeout-minutes"] > minutes
