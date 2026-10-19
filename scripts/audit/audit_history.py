#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Decide what tonight's proactive audit covers, from what earlier runs finished.

The audits used to scope themselves by the clock: "the last 1 day of commits",
"deep on Sunday", "every guide on Monday". When a run died on the Claude weekly
limit, the next run still looked back a fixed window, so the failed night's
commits were never audited and a failed deep sweep was skipped for a week.

Both decisions now come from the workflow's own run history:

* ``window`` — the static audit reviews everything merged since its last
  rotation run that SUCCEEDED, and runs a whole-codebase sweep whenever its last
  successful deep sweep is more than ``--deep-every-days`` old.
* ``rotate`` — the doc walkthrough walks the guides walked least recently, so a
  guide skipped by a failed night is first in line on the next one.

Only runs started by the audit rotation (or the legacy nightly cron) count as
history. A hand-dispatched run may be a dry run that filed nothing, and counting
it would mark work done that never reached the tracker.

stdlib only; runs on a bare GitHub-hosted runner before any venv exists.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: How many past runs to read. Covers months of rotation nights; a longer gap
#: resolves to a deep sweep anyway.
LOOKBACK_RUNS = 60

#: A diff older than this is reviewed as a whole-codebase sweep instead.
MAX_NORMAL_GAP = timedelta(days=7)

#: How the walkthrough names a matrix job: "walkthrough (<path>, <slug>)".
WALK_JOB_RE = re.compile(r"^walkthrough \(([^,]+),")

#: How the static audit names a lens job: "audit (<dimension>, <mode>)".
DEEP_JOB_RE = re.compile(r"^audit \([^,]+, deep\)$")

#: The `run-name` claude-audit-rotation.yml's dispatches carry.
ROTATION_TITLE = "rotation night"

SUNDAY = 7


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _gh(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", check=False
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "`gh` is not on PATH; it is preinstalled on GitHub-hosted runners and "
            "only needs GH_TOKEN with actions:read."
        ) from exc
    if completed.returncode != 0:
        raise SystemExit(
            f"`{' '.join(args[:5])} ...` failed with exit {completed.returncode}:\n"
            f"{completed.stderr.strip()}\n"
            "Refusing to guess tonight's audit scope without the run history — a "
            "guess is how a failed night's work gets skipped."
        )
    return completed.stdout


def counts_as_history(run: dict[str, Any]) -> bool:
    """A rotation dispatch, or a run of the nightly cron the rotation replaced."""
    return run.get("event") == "schedule" or ROTATION_TITLE in (
        run.get("displayTitle") or ""
    )


def was_deep(run: dict[str, Any]) -> bool:
    """A lens job named for deep mode, or a legacy Sunday cron run (auto -> deep)."""
    if any(DEEP_JOB_RE.match(j.get("name") or "") for j in run.get("jobs") or []):
        return True
    return (
        run.get("event") == "schedule"
        and parse_time(run["created_at"]).isoweekday() == SUNDAY
    )


def fetch_history(repo: str, workflow: str, with_jobs: bool) -> list[dict[str, Any]]:
    """Completed rotation runs on main, newest first, optionally with their jobs."""
    runs = json.loads(
        _gh(
            [
                "gh",
                "run",
                "list",
                "--repo",
                repo,
                "--workflow",
                workflow,
                "--branch",
                "main",
                "--status",
                "completed",
                "--limit",
                str(LOOKBACK_RUNS),
                "--json",
                "databaseId,createdAt,conclusion,event,displayTitle,headSha",
            ]
        )
    )
    history = []
    for run in runs:
        entry = {
            "created_at": run["createdAt"],
            "conclusion": run["conclusion"],
            "event": run["event"],
            "displayTitle": run["displayTitle"],
            "head_sha": run["headSha"],
        }
        if not counts_as_history(entry):
            continue
        if with_jobs:
            raw = _gh(
                [
                    "gh",
                    "api",
                    f"repos/{repo}/actions/runs/{run['databaseId']}/jobs",
                    "--paginate",
                    "--jq",
                    ".jobs[] | {name, conclusion}",
                ]
            )
            entry["jobs"] = [json.loads(line) for line in raw.splitlines() if line]
        history.append(entry)
    return history


# ----------------------------------------------------------------------
# window: what the static audit reviews tonight
# ----------------------------------------------------------------------


def resolve_window(
    now: datetime,
    mode: str,
    window_days: str,
    history: list[dict[str, Any]],
    deep_every: timedelta | None,
) -> dict[str, str]:
    """Return ``{"mode", "since", "reason"}``; ``since`` is empty for a deep sweep."""
    if mode not in ("auto", "normal", "deep"):
        raise SystemExit(f"Unknown audit mode {mode!r}; expected auto, normal or deep.")
    if mode == "deep":
        return {"mode": "deep", "since": "", "reason": "deep sweep requested"}
    if window_days.strip():
        days = int(window_days)
        return {
            "mode": "normal",
            "since": (now - timedelta(days=days)).isoformat(),
            "reason": f"window_days={days} requested",
        }

    succeeded = [r for r in history if r["conclusion"] == "success"]
    successes = sorted((parse_time(r["created_at"]) for r in succeeded), reverse=True)
    if mode == "auto" and deep_every is not None:
        deep_times = [parse_time(r["created_at"]) for r in succeeded if was_deep(r)]
        if not deep_times:
            return {
                "mode": "deep",
                "since": "",
                "reason": "no successful deep sweep on record",
            }
        age = now - max(deep_times)
        if age > deep_every:
            return {
                "mode": "deep",
                "since": "",
                "reason": f"the last successful deep sweep was {age.days} days ago",
            }
    if not successes:
        return {
            "mode": "deep",
            "since": "",
            "reason": f"no successful rotation run in the last {LOOKBACK_RUNS} runs",
        }
    last = successes[0]
    if now - last > MAX_NORMAL_GAP:
        return {
            "mode": "deep",
            "since": "",
            "reason": f"the last successful run was {(now - last).days} days ago",
        }
    return {
        "mode": "normal",
        "since": last.isoformat(),
        "reason": f"everything merged since the last successful run ({last:%Y-%m-%d %H:%M} UTC)",
    }


def last_success_sha(history: list[dict[str, Any]]) -> str:
    """The commit the last successful rotation run audited; empty when unknown.

    The run history is the state store: every run records the SHA it checked out,
    so the next turn's recent-changes window is exact without a state branch.
    """
    succeeded = [r for r in history if r["conclusion"] == "success" and r.get("head_sha")]
    if not succeeded:
        return ""
    return max(succeeded, key=lambda r: parse_time(r["created_at"]))["head_sha"]


# ----------------------------------------------------------------------
# rotate: which guides the walkthrough walks tonight
# ----------------------------------------------------------------------

NEVER = datetime.min.replace(tzinfo=timezone.utc)


def last_walked(history: list[dict[str, Any]]) -> dict[str, datetime]:
    """When each guide was last walked AND its findings reached synthesis.

    A guide whose job passed in a run whose synthesis then failed was walked but
    never filed, so it does not count — otherwise a night that stopped on the
    usage limit would mark its finished guides done and their findings would be
    lost.
    """
    walked: dict[str, datetime] = {}
    for run in history:
        jobs = run["jobs"]
        synthesized = any(
            j["name"] == "synthesize" and j["conclusion"] == "success" for j in jobs
        )
        if not synthesized:
            continue
        when = parse_time(run["created_at"])
        for job in jobs:
            match = WALK_JOB_RE.match(job["name"] or "")
            if match and job["conclusion"] == "success":
                path = match.group(1).strip()
                if walked.get(path, NEVER) < when:
                    walked[path] = when
    return walked


def select_docs(
    docs: list[dict[str, str]],
    walked: dict[str, datetime],
    count: int,
    changed: frozenset[str] = frozenset(),
) -> list[dict[str, str]]:
    """The ``count`` guides to walk: ones a recent merge changed, then the stalest.

    A guide edited since this workflow last ran is where a fresh doc-vs-product
    mismatch is most likely, so it jumps the queue; the rest go least recently
    walked first, never-walked first of all.
    """
    if count < 1:
        raise SystemExit(f"--count must be at least 1, got {count}.")
    order = {d["path"]: i for i, d in enumerate(docs)}
    ranked = sorted(
        docs,
        key=lambda d: (
            d["path"] not in changed,
            walked.get(d["path"], NEVER),
            order[d["path"]],
        ),
    )
    return ranked[:count]


def render_rotation(
    docs: list[dict[str, str]],
    chosen: list[dict[str, str]],
    walked: dict[str, datetime],
    now: datetime,
    changed: frozenset[str] = frozenset(),
) -> str:
    stale = [d for d in docs if now - walked.get(d["path"], NEVER) > timedelta(days=14)]
    lines = [
        f"**{len(chosen)} of {len(docs)} guides tonight**: guides changed by a recent "
        "merge first, then the least recently walked.",
        "",
        "| Guide | Last walked and filed | Changed since last run |",
        "|---|---|---|",
    ]
    for doc in chosen:
        when = walked.get(doc["path"])
        shown = f"{when:%Y-%m-%d}" if when else "never"
        edited = "yes" if doc["path"] in changed else ""
        lines.append(f"| `{doc['path']}` | {shown} | {edited} |")
    lines.append("")
    if stale:
        lines.append(
            f"⚠ {len(stale)} guide(s) have not been walked in over 14 days. A night "
            "that stops early leaves its guides at the front of the queue."
        )
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _load_history(args: argparse.Namespace, with_jobs: bool) -> list[dict[str, Any]]:
    if args.history_file:
        return json.loads(Path(args.history_file).read_text(encoding="utf-8"))
    return fetch_history(args.repo, args.workflow, with_jobs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("window", "rotate"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True)
        p.add_argument("--workflow", required=True)
        p.add_argument("--history-file", help="Read run history from JSON (tests).")
        p.add_argument("--now", help="ISO timestamp to use as now (tests).")
    window = sub.choices["window"]
    window.add_argument("--mode", default="auto")
    window.add_argument("--window-days", default="")
    window.add_argument(
        "--deep-every-days",
        type=int,
        help="Run a whole-codebase sweep when the last successful one is older.",
    )
    rotate = sub.choices["rotate"]
    rotate.add_argument("--docs-json", required=True)
    rotate.add_argument("--count", type=int, required=True)
    rotate.add_argument("--summary", type=Path, required=True)
    rotate.add_argument(
        "--changed-paths",
        type=Path,
        help="File listing paths changed since the last run, one per line.",
    )
    args = parser.parse_args(argv)

    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)

    if args.command == "window":
        deep_every = (
            timedelta(days=args.deep_every_days) if args.deep_every_days else None
        )
        history = _load_history(args, with_jobs=deep_every is not None)
        result = resolve_window(now, args.mode, args.window_days, history, deep_every)
        for key in ("mode", "since", "reason"):
            print(f"{key}={result[key]}")
        print(f"base_sha={last_success_sha(history)}")
        return 0

    docs = json.loads(args.docs_json)
    if not docs:
        raise SystemExit("No docs to rotate through; discovery returned nothing.")
    changed = frozenset(
        line.strip()
        for line in (
            args.changed_paths.read_text(encoding="utf-8").splitlines()
            if args.changed_paths
            else []
        )
        if line.strip()
    )
    walked = last_walked(_load_history(args, with_jobs=True))
    chosen = select_docs(docs, walked, args.count, changed)
    args.summary.write_text(
        render_rotation(docs, chosen, walked, now, changed), encoding="utf-8"
    )
    print(json.dumps(chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
