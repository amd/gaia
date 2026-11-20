#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse to release a final that CI has not passed; report CI on a candidate.

A release tag starts publish.yml, but the test workflows do not run on a tag:
they ran (or did not) when the commit landed on main, and main has no required
status checks. This reads, through the Actions API, each check's runs on the
tagged commit.

What happens depends on the tag. This file is the one place that says so:

- a release candidate (vX.Y.Z-rcN) is never held by tests. RC_REPORTED is read
  once, with no dispatch and no wait, each check's state (passed, failed, still
  running, no run) goes to the job summary and a warning annotation, and the
  script exits 0. Candidates exist to be tested; publish.yml still builds and
  smoke-tests the candidate's own artifacts (RC_IN_RUN_CRITICAL);
- a final (vX.Y.Z) needs FINAL_REQUIRED: every job of every workflow, plus the
  dry run. FINAL_ADVISORY is read once after that, never dispatched or waited
  for, and each check not green is a warning that does not hold the release.

A check names a workflow and, optionally, the jobs in it that count. Most
commits on main have no run of their own: the next merge cancels it, and path
filters skip others. So on a final, a check with no passing and no in-progress
run is dispatched once against the tag, which tests the exact commit, and the
gate waits for it (up to --wait-minutes). A check that is then red stops the
release.

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

from gh_retry import RateLimitWaitExceeded, wait_out_rate_limit
from github_release_retry import RateLimited, _is_rate_limit_error
from release_tag import parse_tag


@dataclass(frozen=True)
class Check:
    workflow: str
    # The jobs that must pass, by their display name. Empty: the whole run.
    jobs: tuple[str, ...] = ()
    # False: the gate never starts it, and a missing run fails with `remedy`.
    dispatch: bool = True
    # Appended to the `gh workflow run` command the report prints; `{tag}` is
    # replaced with the release tag.
    remedy_args: str = ""
    why: str = ""
    # Only runs of these events count. Empty: any event.
    events: tuple[str, ...] = ()

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

# Named, so a push run that skipped the Linux row cannot pass as the Linux row.
_NPU_WINDOWS = "NPU agent chat (Windows, cold)"
_NPU_LINUX = "NPU agent chat (Linux, cold)"
_NPU_WHY = (
    "one Agent UI turn on the NPU default model, through GAIA's own Lemonade "
    "and the gaia-agent sidecar, on Windows and Linux Strix Halo (#6632)"
)

# Which test_unit.yml runs count. Pull requests and the merge queue select tests
# on every system, and a push to main selects them on Windows and macOS. An RC
# report, which never dispatches, still reads push runs; a final needs the full
# suite everywhere, which only a dispatched or nightly run is.
UNIT_RC_EVENTS = ("push", "workflow_dispatch", "schedule")
UNIT_FULL_SUITE_EVENTS = ("workflow_dispatch", "schedule")

# Read once on an RC and reported; never dispatched, waited for, or blocking.
# test_unit.yml is split so the summary says whether the Linux suite passed.
# Each workflow must accept workflow_dispatch: the report prints that command.
RC_REPORTED = (
    Check("lint.yml", why="formatting, imports, version fields, security scans"),
    Check(
        "test_unit.yml",
        jobs=("Unit Tests (Linux, py3.12)", "Import Every Module (Linux, py3.10)"),
        why=(
            "the full unit suite on the OS the release is built on, and every "
            "module imports on the oldest Python the wheel declares"
        ),
        events=UNIT_RC_EVENTS,
    ),
    Check(
        "test_unit.yml",
        jobs=(
            "Unit Tests (macOS, py3.12)",
            "Unit Tests (Windows, py3.12)",
            "Offline Integration Tests",
        ),
        events=UNIT_RC_EVENTS,
    ),
    Check("test_gaia_cli_linux.yml"),
    Check("release_train_dryrun.yml", remedy_args="-f rc_tag={tag}", why=_DRY_RUN_WHY),
    Check("test_npu_agent_chat.yml", jobs=(_NPU_WINDOWS,), why=_NPU_WHY),
    Check("test_npu_agent_chat.yml", jobs=(_NPU_LINUX,), why=_NPU_WHY),
)

# The dry run takes an RC name, so the gate never starts one for a final: run
# it on the candidate's commit before tagging the final there.
FINAL_REQUIRED = (
    Check("lint.yml"),
    Check("test_unit.yml", events=UNIT_FULL_SUITE_EVENTS),
    Check("test_gaia_cli_linux.yml"),
    Check(
        "release_train_dryrun.yml",
        dispatch=False,
        remedy_args="-f rc_tag=<the candidate tagged on this commit>",
        why=_DRY_RUN_WHY,
    ),
    Check("test_npu_agent_chat.yml", jobs=(_NPU_WINDOWS,), why=_NPU_WHY),
)

# The Linux NPU row goes back into FINAL_REQUIRED once the Linux Strix Halo
# runners lock enough memory for the NPU model (#6764).
FINAL_ADVISORY = (Check("test_npu_agent_chat.yml", jobs=(_NPU_LINUX,), why=_NPU_WHY),)

# Jobs in publish.yml itself that approve-publish requires on a candidate: they
# build the candidate and smoke-test those artifacts. Listed here so the whole
# RC gate reads in one place; a test pins it to the workflow. ci-green is not
# one. Nor is the Windows setup: a candidate ships without it and says so
# (windows-setup-missing turns the run red).
RC_IN_RUN_CRITICAL = (
    "build-pypi",
    "build-npm",
    "build-desktop-installers",
    "rc-flagship",
    "tui-installers-complete",
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
    if not check.remedy_args:
        return cmd
    return f"{cmd} {check.remedy_args.replace('{tag}', tag)}"


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
    if check.events:
        runs = [r for r in runs if r.get("event") in check.events]
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


# The repository's API budget refills hourly; a call waits at most that long.
RATE_LIMIT_WAIT_SECONDS = 3600


def _rate_limited(e: urllib.error.HTTPError, body: bytes) -> RateLimited | None:
    """The rate limit a 403 or 429 reports, or None for any other refusal."""
    if e.code not in (403, 429):
        return None
    headers = {k.lower(): v for k, v in (e.headers or {}).items()}
    spent = headers.get("x-ratelimit-remaining") == "0"
    retry_after = headers.get("retry-after", "")
    text = body.decode("utf-8", "replace")
    if not spent and not retry_after and not _is_rate_limit_error(text):
        return None
    reset = headers.get("x-ratelimit-reset", "") if spent else ""
    return RateLimited(
        f"HTTP {e.code}: {text}",
        reset=int(reset) if reset.isdigit() else None,
        retry_after=int(retry_after) if retry_after.isdigit() else None,
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

    def attempt():
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read()[:300]
            limited = _rate_limited(e, detail)
            if limited is not None:
                raise limited from e
            hint = " The job needs `actions: write` permission." if e.code == 403 else ""
            raise RuntimeError(
                f"{req.get_method()} {url} returned HTTP {e.code}: {detail!r}.{hint}"
            ) from e

    return wait_out_rate_limit(
        attempt, RATE_LIMIT_WAIT_SECONDS, label=f"{req.get_method()} {url}"
    )


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


def snapshot(
    repo: str,
    tag: str,
    checks: tuple[Check, ...],
    get_runs: Callable[[str], list[dict]],
    get_jobs: Callable[[int], list[dict]] | None = None,
) -> list[Verdict]:
    """Each check's state right now: no dispatch, no wait."""
    return [judge(c, get_runs(c.workflow), repo, tag, get_jobs) for c in checks]


RC_STATE_WORDS = {
    "success": "passed",
    "failed": "failed",
    "pending": "still running",
    "missing": "no run on this commit",
}


def report_rc(verdicts: list[Verdict], sha: str, tag: str) -> tuple[str, list[str]]:
    """A candidate's CI as a job summary plus one warning per check not green."""
    lines = [
        f"### CI on {sha[:12]} ({tag}: reported, does not block a release candidate)",
        "",
        "| Check | State | Detail |",
        "|---|---|---|",
    ]
    lines += [
        f"| `{v.check.label}` | {RC_STATE_WORDS[v.state]} | {v.detail} |"
        for v in verdicts
    ]
    warnings = []
    not_green = [v for v in verdicts if v.state != "success"]
    if not_green:
        lines += [
            "",
            f"{tag} publishes anyway: tests never hold a release candidate. The "
            "final requires these unless marked advisory, so before tagging it:",
            "",
        ]
        for v in not_green:
            state = RC_STATE_WORDS[v.state]
            advisory = v.check in FINAL_ADVISORY
            lines.append(
                f"- `{v.check.label}` {state}."
                + (" Advisory on the final." if advisory else "")
                + (f" `{v.remedy}`" if v.remedy else "")
            )
            final = (
                "the final reports it as advisory"
                if advisory
                else "the final requires it"
            )
            warnings.append(
                f"{v.check.label} {state} on {sha[:12]} ({v.detail}). "
                f"{tag} publishes anyway; {final}."
            )
    return "\n".join(lines), warnings


def advisory_warnings(advisory: list[Verdict], sha: str) -> list[str]:
    """One warning per advisory check on a final that is not green."""
    return [
        f"{v.check.label} {RC_STATE_WORDS[v.state]} on {sha[:12]} ({v.detail}). "
        "Advisory on a final: it does not hold the release."
        for v in advisory
        if v.state != "success"
    ]


def report(
    verdicts: list[Verdict], sha: str, advisory: list[Verdict] | None = None
) -> tuple[bool, str]:
    """A final's gate: every required check must have passed; advisory never holds."""
    advisory = advisory or []
    ok = all(v.state == "success" for v in verdicts)
    lines = [
        f"### CI on {sha[:12]} (final release gate)",
        "",
        "| Check | Gate | State | Detail |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{v.check.label}` | required | {v.state} | {v.detail} |" for v in verdicts
    ]
    lines += [
        f"| `{v.check.label}` | advisory | {v.state} | {v.detail} |" for v in advisory
    ]
    not_green = [v for v in advisory if v.state != "success"]
    if not_green:
        lines += ["", "Advisory, so these do not hold the release:", ""]
        lines += [f"- `{v.check.label}` {RC_STATE_WORDS[v.state]}." for v in not_green]
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
    return ok, "\n".join(lines)


def _write_summary(text: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


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

    try:
        return _gate(args, is_rc, token, get_runs, get_jobs)
    except RateLimitWaitExceeded as e:
        print(f"::error::{e}")
        return 1


def _gate(args, is_rc: bool, token: str, get_runs, get_jobs) -> int:
    if is_rc:
        verdicts = snapshot(args.repo, args.tag, RC_REPORTED, get_runs, get_jobs)
        text, warnings = report_rc(verdicts, args.sha, args.tag)
        print(text)
        _write_summary(text)
        for w in warnings:
            print(f"::warning::{w}")
        return 0

    verdicts = check(
        args.repo,
        args.tag,
        FINAL_REQUIRED,
        get_runs,
        lambda c: dispatch(args.repo, c, args.tag, token),
        args.wait_minutes,
        get_jobs=get_jobs,
    )
    advisory = snapshot(args.repo, args.tag, FINAL_ADVISORY, get_runs, get_jobs)
    ok, text = report(verdicts, args.sha, advisory)
    print(text)
    _write_summary(text)
    for w in advisory_warnings(advisory, args.sha):
        print(f"::warning::{w}")
    if not ok:
        print(
            f"::error::{args.tag} ({args.sha[:12]}) is not green on its required "
            "checks; see the job summary for what failed and how to unblock it."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
