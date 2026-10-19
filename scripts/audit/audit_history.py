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
  guide skipped by a failed night is first in line on the next one. Each guide is
  judged by its own matrix job, so one guide that keeps failing neither stops the
  others from being recorded nor takes every slot.

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
from typing import Any, Callable

#: How many past runs to read. Covers months of rotation nights; a longer gap
#: resolves to a deep sweep anyway.
LOOKBACK_RUNS = 60

#: How many of those runs the rotation may read jobs for (one API call each).
#: ~90 days of walkthrough turns; a guide not walked in that span is simply stalest.
ROTATE_JOB_LOOKBACK = 30

#: A guide whose walk failed this many turns in a row is backed off: it stops
#: competing for the regular slots and is retried once every BACKOFF_RETRY.
BACKOFF_AFTER = 3
BACKOFF_RETRY = timedelta(days=9)

#: At most this share of tonight's slots goes to guides edited since their last
#: walk; the rest go stalest-first so a busy docs week cannot starve the backlog.
CHANGED_SHARE = 0.5

#: A diff older than this is reviewed as a whole-codebase sweep instead.
MAX_NORMAL_GAP = timedelta(days=7)

#: How the walkthrough names a matrix job: "walkthrough (<path>, <slug>)".
WALK_JOB_RE = re.compile(r"^walkthrough \(([^,]+),")

#: How the static audit names a lens job: "audit (<dimension>, <mode>)".
DEEP_JOB_RE = re.compile(r"^audit \([^,]+, deep\)$")

#: The `run-name` claude-audit-rotation.yml's dispatches carry.
ROTATION_TITLE = "rotation night"

SUNDAY = 7

JobsOf = Callable[[dict[str, Any]], list[dict[str, Any]]]


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


def preloaded_jobs(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Jobs already attached to a run (a --history-file snapshot, or tests)."""
    return run.get("jobs") or []


def was_deep(run: dict[str, Any], jobs_of: JobsOf = preloaded_jobs) -> bool:
    """A legacy Sunday cron run (auto -> deep), or a lens job named for deep mode."""
    if (
        run.get("event") == "schedule"
        and parse_time(run["created_at"]).isoweekday() == SUNDAY
    ):
        return True
    return any(DEEP_JOB_RE.match(j.get("name") or "") for j in jobs_of(run))


def fetch_history(repo: str, workflow: str) -> list[dict[str, Any]]:
    """Completed rotation runs on main, newest first. Jobs are read lazily."""
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
            "id": run["databaseId"],
            "created_at": run["createdAt"],
            "conclusion": run["conclusion"],
            "event": run["event"],
            "displayTitle": run["displayTitle"],
            "head_sha": run["headSha"],
        }
        if counts_as_history(entry):
            history.append(entry)
    return history


def jobs_fetcher(repo: str) -> JobsOf:
    """Read a run's jobs on first use, once; a snapshot's own jobs win.

    One API call per run, so callers read jobs only for the runs whose answer they
    need (the newest successful ones), not for every run in the lookback.
    """

    def jobs_of(run: dict[str, Any]) -> list[dict[str, Any]]:
        if "jobs" not in run:
            raw = _gh(
                [
                    "gh",
                    "api",
                    f"repos/{repo}/actions/runs/{run['id']}/jobs",
                    "--paginate",
                    "--jq",
                    ".jobs[] | {name, conclusion}",
                ]
            )
            run["jobs"] = [json.loads(line) for line in raw.splitlines() if line]
        return run["jobs"]

    return jobs_of


def _newest(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(runs, key=lambda r: parse_time(r["created_at"]), reverse=True)


# ----------------------------------------------------------------------
# window: what the static audit reviews tonight
# ----------------------------------------------------------------------


def resolve_window(
    now: datetime,
    mode: str,
    window_days: str,
    history: list[dict[str, Any]],
    deep_every: timedelta | None,
    jobs_of: JobsOf = preloaded_jobs,
) -> dict[str, str]:
    """Return ``{"mode", "since", "reason"}``; ``since`` is empty for a deep sweep.

    Only ``auto`` (what the rotation dispatches) may escalate to a deep sweep. An
    explicit ``normal`` stays normal: with no usable history it reviews the last
    ``MAX_NORMAL_GAP`` and the reason says what that leaves out.
    """
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

    succeeded = _newest([r for r in history if r["conclusion"] == "success"])
    if mode == "auto" and deep_every is not None:
        # Newest first, stopping at the first deep sweep: usually one jobs call.
        last_deep = next((r for r in succeeded if was_deep(r, jobs_of)), None)
        if last_deep is None:
            return {
                "mode": "deep",
                "since": "",
                "reason": "no successful deep sweep on record",
            }
        age = now - parse_time(last_deep["created_at"])
        if age > deep_every:
            return {
                "mode": "deep",
                "since": "",
                "reason": f"the last successful deep sweep was {age.days} days ago",
            }

    last = parse_time(succeeded[0]["created_at"]) if succeeded else None
    if last is not None and now - last <= MAX_NORMAL_GAP:
        return {
            "mode": "normal",
            "since": last.isoformat(),
            "reason": f"everything merged since the last successful run ({last:%Y-%m-%d %H:%M} UTC)",
        }
    gap = (
        f"the last successful run was {(now - last).days} days ago"
        if last is not None
        else f"no successful rotation run in the last {LOOKBACK_RUNS} runs"
    )
    if mode == "normal":
        floor = now - MAX_NORMAL_GAP
        return {
            "mode": "normal",
            "since": floor.isoformat(),
            "reason": (
                f"normal mode was requested but {gap}, so this reviews only the last "
                f"{MAX_NORMAL_GAP.days} days (since {floor:%Y-%m-%d %H:%M} UTC). "
                "Older changes are not covered; dispatch mode=deep for those"
            ),
        }
    return {"mode": "deep", "since": "", "reason": gap}


def last_success_sha(
    history: list[dict[str, Any]],
    jobs_of: JobsOf = preloaded_jobs,
    success_job: str = "",
) -> str:
    """The commit the last successful rotation run audited; empty when unknown.

    The run history is the state store: every run records the SHA it checked out,
    so the next turn's recent-changes window is exact without a state branch.

    *success_job* judges success by one job instead of the whole run. The
    walkthrough's run goes red whenever any one guide fails, but its
    ``synthesize`` job still filed everything the other guides found.
    """
    for run in _newest([r for r in history if r.get("head_sha")]):
        if success_job:
            ok = any(
                j.get("name") == success_job and j.get("conclusion") == "success"
                for j in jobs_of(run)
            )
        else:
            ok = run["conclusion"] == "success"
        if ok:
            return run["head_sha"]
    return ""


# ----------------------------------------------------------------------
# rotate: which guides the walkthrough walks tonight
# ----------------------------------------------------------------------

NEVER = datetime.min.replace(tzinfo=timezone.utc)

#: Job conclusions that mean the guide's own walk did not finish. A walk job can
#: also end ``cancelled`` in a run nobody cancelled (a lost runner); that counts
#: too, but not when a person cancelled the whole run.
FAILED = ("failure", "timed_out")


def guide_records(
    history: list[dict[str, Any]],
    paths: list[str],
    jobs_of: JobsOf = preloaded_jobs,
    max_runs: int = ROTATE_JOB_LOOKBACK,
) -> dict[str, dict[str, Any]]:
    """Per guide: when it was last walked and filed, at which commit, and how many
    turns in a row it has failed since.

    Judged from each guide's own matrix job, not the run. A run goes red whenever
    any one guide fails, so counting only green runs let one always-failing guide
    stop all ten from being recorded. A finished guide counts once its run's
    ``synthesize`` job succeeded, because only then were its findings filed.

    A failure counts against a guide only when another guide in the same run
    finished. When every guide failed (the usage limit, a runner image with no
    Git) the night failed, not the guide, and backing off all ten would leave
    nothing to walk.

    Reads runs newest first and stops once every guide's last walk is known, so a
    settled rotation costs a few jobs calls, not one per run in the lookback.
    """
    records: dict[str, dict[str, Any]] = {
        p: {"walked": None, "sha": "", "failures": 0, "last_attempt": None}
        for p in paths
    }
    unresolved = set(paths)
    for run in _newest(history)[:max_runs]:
        if not unresolved:
            break
        jobs = jobs_of(run)
        synthesized = any(
            j.get("name") == "synthesize" and j.get("conclusion") == "success"
            for j in jobs
        )
        walks = [
            (match.group(1).strip(), job.get("conclusion"))
            for job in jobs
            if (match := WALK_JOB_RE.match(job.get("name") or ""))
        ]
        some_finished = any(conclusion == "success" for _, conclusion in walks)
        when = parse_time(run["created_at"])
        for path, conclusion in walks:
            if path not in unresolved:
                continue
            record = records[path]
            failed = conclusion in FAILED or (
                conclusion == "cancelled" and run.get("conclusion") != "cancelled"
            )
            if conclusion == "success" or failed:
                record["last_attempt"] = record["last_attempt"] or when
            if conclusion == "success" and synthesized:
                record["walked"] = when
                record["sha"] = run.get("head_sha") or ""
                unresolved.discard(path)
            elif failed and some_finished:
                record["failures"] += 1
    return records


def last_walked(records: dict[str, dict[str, Any]]) -> dict[str, datetime]:
    return {p: r["walked"] for p, r in records.items() if r["walked"] is not None}


def backed_off(records: dict[str, dict[str, Any]]) -> set[str]:
    return {p for p, r in records.items() if r["failures"] >= BACKOFF_AFTER}


def changed_since_walk(
    records: dict[str, dict[str, Any]], diff: Callable[[str, str], bool]
) -> frozenset[str]:
    """Guides whose file changed since the commit of their own last walk.

    Per guide, not per run: a run-wide base only advanced when a whole run was
    green, so a stale base marked the same guides "changed" every turn.
    """
    return frozenset(
        path for path, r in records.items() if r["sha"] and diff(r["sha"], path)
    )


def git_changed(sha: str, path: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "diff", "--quiet", sha, "HEAD", "--", path],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "`git` is not on PATH; the rotation needs a checkout."
        ) from exc
    if completed.returncode not in (0, 1):
        raise SystemExit(
            f"`git diff {sha[:9]} HEAD -- {path}` failed: {completed.stderr.strip()}\n"
            "The rotation needs full history (actions/checkout fetch-depth: 0)."
        )
    return completed.returncode == 1


def select_docs(
    docs: list[dict[str, str]],
    records: dict[str, dict[str, Any]],
    count: int,
    changed: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """The ``count`` guides to walk tonight.

    1. Guides edited since their own last walk, stalest first, capped at
       ``CHANGED_SHARE`` of the slots. A fresh doc-vs-product mismatch is most
       likely there, but they must not crowd out the rest of the queue.
    2. One backed-off guide (failed ``BACKOFF_AFTER`` turns running) whose last
       attempt is at least ``BACKOFF_RETRY`` old, so a fix gets noticed without
       the guide taking a slot every turn.
    3. Everything else, least recently walked first, never-walked first of all.
    """
    if count < 1:
        raise SystemExit(f"--count must be at least 1, got {count}.")
    now = now or datetime.now(timezone.utc)
    order = {d["path"]: i for i, d in enumerate(docs)}

    def record(d: dict[str, str]) -> dict[str, Any]:
        return records.get(d["path"]) or {}

    def stalest(items: list[dict[str, str]]) -> list[dict[str, str]]:
        return sorted(
            items, key=lambda d: (record(d).get("walked") or NEVER, order[d["path"]])
        )

    failing = backed_off(records)
    healthy = [d for d in docs if d["path"] not in failing]

    changed_cap = int(count * CHANGED_SHARE)
    chosen = stalest([d for d in healthy if d["path"] in changed])[:changed_cap]

    due = [
        d
        for d in docs
        if d["path"] in failing
        and now - (record(d).get("last_attempt") or NEVER) >= BACKOFF_RETRY
    ]
    chosen += sorted(
        due, key=lambda d: (record(d).get("last_attempt") or NEVER, order[d["path"]])
    )[:1]

    picked = {d["path"] for d in chosen}
    chosen += [d for d in stalest(healthy) if d["path"] not in picked]
    return chosen[:count]


def render_rotation(
    docs: list[dict[str, str]],
    chosen: list[dict[str, str]],
    records: dict[str, dict[str, Any]],
    now: datetime,
    changed: frozenset[str] = frozenset(),
) -> str:
    walked = last_walked(records)
    stale = [d for d in docs if now - walked.get(d["path"], NEVER) > timedelta(days=14)]
    lines = [
        f"**{len(chosen)} of {len(docs)} guides tonight**: guides changed since their "
        "last walk first (at most half the slots), then the least recently walked.",
        "",
        "| Guide | Last walked and filed | Changed since its last walk |",
        "|---|---|---|",
    ]
    for doc in chosen:
        when = walked.get(doc["path"])
        shown = f"{when:%Y-%m-%d}" if when else "never"
        edited = "yes" if doc["path"] in changed else ""
        lines.append(f"| `{doc['path']}` | {shown} | {edited} |")
    lines.append("")
    failing = sorted(backed_off(records))
    if failing:
        picked = {d["path"] for d in chosen}
        lines += [
            f"⛔ **{len(failing)} guide(s) failed {BACKOFF_AFTER} or more turns in a "
            "row and are not being verified.** Each is retried once every "
            f"{BACKOFF_RETRY.days} days instead of taking a slot every turn. Fix the "
            "walk or the runner.",
            "",
            "| Guide | Failed turns in a row | Last walked and filed | Tonight |",
            "|---|---|---|---|",
        ]
        for path in failing:
            when = walked.get(path)
            lines.append(
                f"| `{path}` | {records[path]['failures']} | "
                f"{f'{when:%Y-%m-%d}' if when else 'never'} | "
                f"{'retrying' if path in picked else 'skipped'} |"
            )
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


def _load_history(args: argparse.Namespace) -> tuple[list[dict[str, Any]], JobsOf]:
    if args.history_file:
        history = json.loads(Path(args.history_file).read_text(encoding="utf-8"))
        return history, preloaded_jobs
    return fetch_history(args.repo, args.workflow), jobs_fetcher(args.repo)


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
    window.add_argument(
        "--success-job",
        default="",
        help="Take base_sha from the newest run where this job succeeded.",
    )
    rotate = sub.choices["rotate"]
    rotate.add_argument("--docs-json", required=True)
    rotate.add_argument("--count", type=int, required=True)
    rotate.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args(argv)

    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    history, jobs_of = _load_history(args)

    if args.command == "window":
        deep_every = (
            timedelta(days=args.deep_every_days) if args.deep_every_days else None
        )
        result = resolve_window(
            now, args.mode, args.window_days, history, deep_every, jobs_of
        )
        for key in ("mode", "since", "reason"):
            print(f"{key}={result[key]}")
        print(f"base_sha={last_success_sha(history, jobs_of, args.success_job)}")
        return 0

    docs = json.loads(args.docs_json)
    if not docs:
        raise SystemExit("No docs to rotate through; discovery returned nothing.")
    records = guide_records(history, [d["path"] for d in docs], jobs_of)
    changed = changed_since_walk(records, git_changed)
    chosen = select_docs(docs, records, args.count, changed, now)
    args.summary.write_text(
        render_rotation(docs, chosen, records, now, changed), encoding="utf-8"
    )
    for path in sorted(backed_off(records)):
        # stderr: stdout is captured as the matrix JSON. The runner reads both.
        print(
            f"::error title=Doc walkthrough guide keeps failing::{path} failed "
            f"{records[path]['failures']} turns in a row and is not being verified; "
            f"it is retried once every {BACKOFF_RETRY.days} days. See the rotation "
            "table in the job summary.",
            file=sys.stderr,
        )
    print(json.dumps(chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
