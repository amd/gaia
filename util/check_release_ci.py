#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse to release a commit that CI has not passed.

A release tag starts publish.yml, but the test workflows do not run on a tag:
they ran (or did not) when the commit landed on main, and main has no required
status checks. This checks, through the Actions API, that every required check
has a successful run on the tagged commit.

What is required depends on the tag. This file is the one place that says so:

- a release candidate (vX.Y.Z-rcN) needs only RC_CRITICAL. RC_ADVISORY is
  reported in the job summary as warnings and is never waited for or
  dispatched, so one flaky macOS test cannot hold a candidate back. publish.yml
  builds and smoke-tests the candidate's artifacts itself (RC_IN_RUN_CRITICAL),
  so the hour-long dry run is advisory too;
- a final (vX.Y.Z) needs FINAL_REQUIRED: every job of every workflow, plus the
  dry run.

A check names a workflow and, optionally, the jobs in it that count. Most
commits on main have no run of their own: the next merge cancels it, and path
filters skip others. So a check with no passing and no in-progress run is
dispatched once against the tag, which tests the exact commit, and the gate
waits for it (up to --wait-minutes). A check that is then red stops the release.

Usage::

    python util/check_release_ci.py --repo amd/gaia --sha <sha> --tag v0.25.1-rc1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from release_tag import parse_tag


@dataclass(frozen=True)
class Check:
    workflow: str
    # The jobs that must pass, by their display name. Empty: the whole run.
    jobs: tuple[str, ...] = ()
    # False: the gate never starts it, and a missing run fails with `remedy`.
    dispatch: bool = True
    # Appended to the `gh workflow run` command the report prints.
    remedy_args: str = ""
    why: str = ""

    @property
    def label(self) -> str:
        return (
            f"{self.workflow} ({', '.join(self.jobs)})" if self.jobs else self.workflow
        )


_DRY_RUN_WHY = (
    "builds the wheel, sdist, npm package, desktop, terminal and Windows "
    "installers as the candidate and its final twin, installs each on clean "
    "runners, and checks checksums, version stamps, the lock, the twin-diff "
    "and the manifest"
)

# Each workflow must accept workflow_dispatch. The slowest check sets how long
# a release waits, so this stays to what a broken candidate would ship.
RC_CRITICAL = (
    Check("lint.yml", why="formatting, imports, version fields, security scans"),
    Check(
        "test_unit.yml",
        jobs=("Unit Tests (Linux, py3.12)", "Import Every Module (Linux, py3.10)"),
        why=(
            "the full unit suite on the OS the release is built on, and every "
            "module imports on the oldest Python the wheel declares"
        ),
    ),
)

# Named, so a push run that started only the Windows row cannot pass for both.
_NPU_JOBS = ("NPU agent chat (Windows, cold)", "NPU agent chat (Linux, cold)")
_NPU_WHY = (
    "one Agent UI turn on the NPU default model, through GAIA's own Lemonade "
    "and the gaia-agent sidecar, on Windows and Linux Strix Halo (#6632)"
)

# Shown on an RC, never waited for or dispatched.
RC_ADVISORY = (
    Check(
        "test_unit.yml",
        jobs=(
            "Unit Tests (macOS, py3.12)",
            "Unit Tests (Windows, py3.12)",
            "Offline Integration Tests",
        ),
    ),
    Check("test_gaia_cli_linux.yml"),
    Check("release_train_dryrun.yml", why=_DRY_RUN_WHY),
    Check("test_npu_agent_chat.yml", jobs=_NPU_JOBS, why=_NPU_WHY),
)

# The dry run takes an RC name, so the gate never starts one for a final: run
# it on the candidate's commit before tagging the final there.
FINAL_REQUIRED = (
    Check("lint.yml"),
    Check("test_unit.yml"),
    Check("test_gaia_cli_linux.yml"),
    Check(
        "release_train_dryrun.yml",
        dispatch=False,
        remedy_args="-f rc_tag=<the candidate tagged on this commit>",
        why=_DRY_RUN_WHY,
    ),
    Check("test_npu_agent_chat.yml", jobs=_NPU_JOBS, why=_NPU_WHY),
)

# Jobs in publish.yml itself that approve-publish requires on a candidate.
# Listed here so the whole RC gate reads in one place; a test pins it to the
# workflow. The Windows setup is not one: a candidate ships without it and
# says so (windows-setup-missing turns the run red).
RC_IN_RUN_CRITICAL = (
    "build-pypi",
    "build-npm",
    "build-desktop-installers",
    "rc-flagship",
    "tui-installers-complete",
    "ci-green",
)

PENDING_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}


@dataclass(frozen=True)
class Verdict:
    check: Check
    state: str  # "success" | "pending" | "failed" | "missing"
    detail: str
    remedy: str = ""

    @property
    def workflow(self) -> str:
        return self.check.workflow


def _dispatch_command(check: Check, repo: str, tag: str) -> str:
    cmd = f"gh workflow run {check.workflow} --repo {repo} --ref {tag}"
    return f"{cmd} {check.remedy_args}" if check.remedy_args else cmd


def _missing(check: Check, repo: str, tag: str) -> Verdict:
    return Verdict(
        check, "missing", "no run on this commit", _dispatch_command(check, repo, tag)
    )


def _job_states(run: dict, get_jobs: Callable[[int], list[dict]]) -> dict[str, dict]:
    return {j["name"]: j for j in get_jobs(run["id"])}


def judge(
    check: Check,
    runs: list[dict],
    repo: str,
    tag: str,
    get_jobs: Callable[[int], list[dict]] | None = None,
) -> Verdict:
    """Decide one check's state from its workflow's runs on the commit."""
    if not runs:
        return _missing(check, repo, tag)
    if not check.jobs:
        passed = [r for r in runs if r.get("conclusion") == "success"]
        if passed:
            return Verdict(check, "success", passed[0]["html_url"])
        pending = [r for r in runs if r.get("status") in PENDING_STATUSES]
        if pending:
            return Verdict(check, "pending", pending[0]["html_url"])
        latest = max(runs, key=lambda r: r.get("created_at", ""))
        return Verdict(
            check,
            "failed",
            f"latest run {latest.get('conclusion')}: {latest['html_url']}",
            f"gh run rerun {latest['id']} --repo {repo} --failed",
        )

    if get_jobs is None:
        raise ValueError(f"{check.label} names jobs, so judge() needs get_jobs")
    newest_first = sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)
    by_run = [(r, _job_states(r, get_jobs)) for r in newest_first]
    for r, jobs in by_run:
        if all(jobs.get(n, {}).get("conclusion") == "success" for n in check.jobs):
            return Verdict(check, "success", r["html_url"])
    for r, jobs in by_run:
        if any(
            (n in jobs and jobs[n].get("status") in PENDING_STATUSES)
            or (n not in jobs and r.get("status") in PENDING_STATUSES)
            for n in check.jobs
        ):
            return Verdict(check, "pending", r["html_url"])
    latest, jobs = by_run[0]
    bad = []
    for n in check.jobs:
        if n not in jobs:
            bad.append(f"`{n}` is not a job of this run")
        elif jobs[n].get("conclusion") != "success":
            bad.append(f"`{n}` {jobs[n].get('conclusion')}")
    return Verdict(
        check,
        "failed",
        f"{'; '.join(bad)}: {latest['html_url']}",
        f"gh run rerun {latest['id']} --repo {repo} --failed",
    )


def _api(url: str, token: str, body: dict | None = None):
    req = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        hint = " The job needs `actions: write` permission." if e.code == 403 else ""
        raise RuntimeError(
            f"{req.get_method()} {url} returned HTTP {e.code}: "
            f"{e.read()[:300]!r}.{hint}"
        ) from e


def fetch_runs(repo: str, workflow: str, sha: str, token: str) -> list[dict]:
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs"
        f"?head_sha={sha}&per_page=100"
    )
    return _api(url, token)["workflow_runs"]


def fetch_jobs(repo: str, run_id: int, token: str) -> list[dict]:
    # filter=latest: a re-run's jobs, with the ones it carried over.
    url = (
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
        "?filter=latest&per_page=100"
    )
    return _api(url, token)["jobs"]


def dispatch(repo: str, check: Check, tag: str, token: str) -> None:
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/"
        f"{check.workflow}/dispatches"
    )
    _api(url, token, {"ref": tag})


def _created(run: dict) -> datetime:
    return datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def check(
    repo: str,
    tag: str,
    checks: tuple[Check, ...],
    get_runs: Callable[[str], list[dict]],
    start_run: Callable[[Check], None],
    wait_minutes: float,
    get_jobs: Callable[[int], list[dict]] | None = None,
    poll_seconds: float = 60,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = _utcnow,
) -> list[Verdict]:
    """Dispatch what never passed, then poll until done, red, or out of time."""
    if not checks:
        raise ValueError("no checks to require; refusing to call a release green")
    deadline = now() + timedelta(minutes=wait_minutes)
    dispatched: dict[str, datetime] = {}
    while True:
        verdicts = []
        for c in checks:
            runs = get_runs(c.workflow)
            if c.workflow in dispatched:
                # Only the run we started counts; older ones are why we started it.
                # The minute of slack covers clock skew against GitHub's timestamps.
                since = dispatched[c.workflow] - timedelta(minutes=1)
                runs = [r for r in runs if _created(r) >= since]
                if not runs:
                    verdicts.append(
                        Verdict(
                            c, "pending", "dispatched; waiting for the run to start"
                        )
                    )
                    continue
            v = judge(c, runs, repo, tag, get_jobs)
            # A red run on main is dispatched once too: a flake gets one re-check.
            if (
                v.state in ("failed", "missing")
                and c.dispatch
                and c.workflow not in dispatched
            ):
                print(f"{c.label}: {v.detail}; dispatching it on {tag}", flush=True)
                start_run(c)
                dispatched[c.workflow] = now()
                v = Verdict(c, "pending", f"dispatched on {tag}")
            verdicts.append(v)
        states = {v.state for v in verdicts}
        if (
            "pending" not in states
            or states & {"failed", "missing"}
            or now() >= deadline
        ):
            return verdicts
        waiting = ", ".join(v.check.label for v in verdicts if v.state == "pending")
        print(f"waiting for: {waiting}", flush=True)
        sleep(poll_seconds)


def advise(
    repo: str,
    tag: str,
    checks: tuple[Check, ...],
    get_runs: Callable[[str], list[dict]],
    get_jobs: Callable[[int], list[dict]] | None = None,
) -> list[Verdict]:
    """Each advisory check's state right now: no dispatch, no wait."""
    return [judge(c, get_runs(c.workflow), repo, tag, get_jobs) for c in checks]


def report(
    verdicts: list[Verdict],
    sha: str,
    is_rc: bool = False,
    advisory: list[Verdict] | None = None,
) -> tuple[bool, str]:
    ok = all(v.state == "success" for v in verdicts)
    kind = "release candidate" if is_rc else "final release"
    lines = [
        f"### CI on {sha[:12]} ({kind} gate)",
        "",
        "| Check | Gate | State | Detail |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{v.check.label}` | required | {v.state} | {v.detail} |" for v in verdicts
    ]
    lines += [
        f"| `{v.check.label}` | advisory | {v.state} | {v.detail} |"
        for v in advisory or []
    ]
    blocked = [v for v in verdicts if v.state != "success"]
    if blocked:
        lines += ["", "Nothing was published. To unblock this release:", ""]
        for v in blocked:
            if v.state == "pending":
                lines.append(
                    f"- `{v.check.label}` did not finish in time; when it passes, "
                    "re-run this release's failed jobs."
                )
            elif v.state == "missing":
                lines.append(
                    f"- `{v.check.label}` never ran on this commit and the gate does "
                    f"not start it. Run `{v.remedy}`, then re-run this release's "
                    "failed jobs once it passes."
                )
            else:
                lines.append(
                    f"- `{v.check.label}`: a real failure needs a fix on main and a "
                    f"new tag. If it was a flake, `{v.remedy}`, then re-run this "
                    "release's failed jobs once it passes."
                )
    warned = [v for v in advisory or [] if v.state != "success"]
    if warned:
        lines += [
            "",
            "Advisory checks are not green. They do not block a release candidate, "
            "but the final requires them, so fix them before tagging it.",
        ]
    return ok, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--wait-minutes", type=float, default=80)
    args = ap.parse_args(argv)

    try:
        is_rc = parse_tag(args.tag).is_rc
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("ERROR: GITHUB_TOKEN is not set.", file=sys.stderr)
        return 1

    def get_runs(workflow: str) -> list[dict]:
        return fetch_runs(args.repo, workflow, args.sha, token)

    def get_jobs(run_id: int) -> list[dict]:
        return fetch_jobs(args.repo, run_id, token)

    verdicts = check(
        args.repo,
        args.tag,
        RC_CRITICAL if is_rc else FINAL_REQUIRED,
        get_runs,
        lambda c: dispatch(args.repo, c, args.tag, token),
        args.wait_minutes,
        get_jobs=get_jobs,
    )
    advisory = (
        advise(args.repo, args.tag, RC_ADVISORY, get_runs, get_jobs) if is_rc else []
    )
    ok, text = report(verdicts, args.sha, is_rc, advisory)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    for v in advisory:
        if v.state != "success":
            print(
                f"::warning::advisory {v.check.label} is {v.state} on "
                f"{args.sha[:12]}: {v.detail}. It does not block {args.tag}; "
                "the final requires it."
            )
    if not ok:
        print(
            f"::error::{args.tag} ({args.sha[:12]}) is not green on its required "
            "checks; see the job summary for what failed and how to unblock it."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
