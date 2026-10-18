#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse to release a commit that CI has not passed.

A release tag starts publish.yml, but the test workflows do not run on a tag:
they ran (or did not) when the commit landed on main, and main has no required
status checks. This checks, through the Actions API, that each workflow in
REQUIRED_WORKFLOWS has a successful run on the tagged commit.

Most commits on main have no such run: the next merge cancels the previous
commit's runs, and path filters skip others. So a workflow with no passing and
no in-progress run is dispatched once against the tag, which tests the exact
commit, and the check waits for it (up to --wait-minutes). A run that then
fails stops the release.

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

# Each entry must accept workflow_dispatch. Keep the list short: the slowest one
# sets how long a release waits. test_gaia_cli_windows.yml (40-70 min) is left
# out so a candidate still ships about when its builds finish.
REQUIRED_WORKFLOWS = (
    "lint.yml",
    "test_unit.yml",
    "test_gaia_cli_linux.yml",
)

PENDING_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}


@dataclass(frozen=True)
class Verdict:
    workflow: str
    state: str  # "success" | "pending" | "failed" | "missing"
    detail: str
    remedy: str = ""


def judge(workflow: str, runs: list[dict], repo: str, tag: str) -> Verdict:
    """Decide one workflow's state from its runs on the commit."""
    passed = [r for r in runs if r.get("conclusion") == "success"]
    if passed:
        return Verdict(workflow, "success", passed[0]["html_url"])
    pending = [r for r in runs if r.get("status") in PENDING_STATUSES]
    if pending:
        return Verdict(workflow, "pending", pending[0]["html_url"])
    if not runs:
        return Verdict(
            workflow,
            "missing",
            "no run on this commit",
            f"gh workflow run {workflow} --repo {repo} --ref {tag}",
        )
    latest = max(runs, key=lambda r: r.get("created_at", ""))
    return Verdict(
        workflow,
        "failed",
        f"latest run {latest.get('conclusion')}: {latest['html_url']}",
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


def dispatch(repo: str, workflow: str, tag: str, token: str) -> None:
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    _api(url, token, {"ref": tag})


def _created(run: dict) -> datetime:
    return datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def check(
    repo: str,
    tag: str,
    workflows: tuple[str, ...],
    get_runs: Callable[[str], list[dict]],
    start_run: Callable[[str], None],
    wait_minutes: float,
    poll_seconds: float = 60,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = _utcnow,
) -> list[Verdict]:
    """Dispatch what never passed, then poll until done, red, or out of time."""
    deadline = now() + timedelta(minutes=wait_minutes)
    dispatched: dict[str, datetime] = {}
    while True:
        verdicts = []
        for w in workflows:
            runs = get_runs(w)
            if w in dispatched:
                # Only the run we started counts; older ones are why we started it.
                # The minute of slack covers clock skew against GitHub's timestamps.
                since = dispatched[w] - timedelta(minutes=1)
                runs = [r for r in runs if _created(r) >= since]
                if not runs:
                    verdicts.append(
                        Verdict(w, "pending", "dispatched; waiting for the run to start")
                    )
                    continue
            v = judge(w, runs, repo, tag)
            # A red run on main is dispatched once too: a flake gets one re-check.
            if v.state in ("failed", "missing") and w not in dispatched:
                print(f"{w}: {v.detail}; dispatching it on {tag}", flush=True)
                start_run(w)
                dispatched[w] = now()
                v = Verdict(w, "pending", f"dispatched on {tag}")
            verdicts.append(v)
        states = {v.state for v in verdicts}
        if "pending" not in states or "failed" in states or now() >= deadline:
            return verdicts
        waiting = ", ".join(v.workflow for v in verdicts if v.state == "pending")
        print(f"waiting for: {waiting}", flush=True)
        sleep(poll_seconds)


def report(verdicts: list[Verdict], sha: str) -> tuple[bool, str]:
    ok = all(v.state == "success" for v in verdicts)
    lines = [
        f"### CI on {sha[:12]}",
        "",
        "| Workflow | State | Detail |",
        "|---|---|---|",
    ]
    lines += [f"| `{v.workflow}` | {v.state} | {v.detail} |" for v in verdicts]
    blocked = [v for v in verdicts if v.state != "success"]
    if blocked:
        lines += ["", "Nothing was published. To unblock this release:", ""]
        for v in blocked:
            if v.state == "pending":
                lines.append(
                    f"- `{v.workflow}` did not finish in time; when it passes, "
                    "re-run this release's failed jobs."
                )
            else:
                lines.append(
                    f"- `{v.workflow}`: a real failure needs a fix on main and a "
                    f"new tag. If it was a flake, `{v.remedy}`, then re-run this "
                    "release's failed jobs once it passes."
                )
    return ok, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--wait-minutes", type=float, default=80)
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("ERROR: GITHUB_TOKEN is not set.", file=sys.stderr)
        return 1

    verdicts = check(
        args.repo,
        args.tag,
        REQUIRED_WORKFLOWS,
        lambda w: fetch_runs(args.repo, w, args.sha, token),
        lambda w: dispatch(args.repo, w, args.tag, token),
        args.wait_minutes,
    )
    ok, text = report(verdicts, args.sha)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    if not ok:
        print(
            f"::error::{args.tag} ({args.sha[:12]}) is not green on CI; see the "
            "job summary for what failed and how to unblock it."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
