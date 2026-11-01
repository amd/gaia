#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hand the issues an audit just filed to auto-fix.

Of 367 audit issues filed by October 2026, none ever got the ``bug`` label a
maintainer had to add by hand, so auto-fix never saw one and outside contributors
fixed them weeks later. This step promotes each new issue marked auto-fixable.

Labelling alone would do nothing. The audits run on GITHUB_TOKEN, and GitHub
creates no workflow runs from GITHUB_TOKEN events except ``workflow_dispatch`` and
``repository_dispatch``, so the ``issues: labeled`` trigger in claude.yml never
fires. Hence the explicit dispatch of its auto-fix job for each issue.

``--max`` caps a run because a fix can take 100 Opus turns from the same
subscription whose weekly limit forced the audit rotation. Issues over the cap stay
unlabelled and are named in the job summary: adding ``bug`` by hand still starts
auto-fix for them.

stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

AUDIT_LABEL = "weekly-audit"
BUG_LABEL = "bug"
BOT_AUTHORS = {"github-actions", "app/github-actions", "github-actions[bot]"}
AUTO_FIXABLE = re.compile(r"\*\*Auto-fixable:\*\*\s*yes\b", re.IGNORECASE)
HIGH_SEVERITY = re.compile(r"\*\*Severity:\*\*\s*🔴")
LIST_LIMIT = 200
GH_TIMEOUT = 60


def _gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=GH_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout


def list_audit_issues(repo: str) -> list[dict]:
    return json.loads(
        _gh(
            "issue",
            "list",
            "--repo",
            repo,
            "--label",
            AUDIT_LABEL,
            "--state",
            "open",
            "--limit",
            str(LIST_LIMIT),
            "--json",
            "number,title,body,author,labels",
        )
    )


def select(issues: list[dict], watermark: int) -> list[dict]:
    """New, bot-filed, auto-fixable issues without ``bug``: 🔴 first, then oldest."""
    picked = [
        issue
        for issue in issues
        if issue["number"] > watermark
        and (issue.get("author") or {}).get("login") in BOT_AUTHORS
        and AUTO_FIXABLE.search(issue.get("body") or "")
        and BUG_LABEL not in {label["name"] for label in issue.get("labels") or []}
    ]
    return sorted(
        picked,
        key=lambda issue: (
            not HIGH_SEVERITY.search(issue.get("body") or ""),
            issue["number"],
        ),
    )


def promote(repo: str, ref: str, number: int) -> None:
    _gh("issue", "edit", str(number), "--repo", repo, "--add-label", BUG_LABEL)
    _gh(
        "workflow",
        "run",
        "claude.yml",
        "--repo",
        repo,
        "--ref",
        ref,
        "-f",
        f"issue_number={number}",
    )


def report(
    path: str, promoted: list[dict], skipped: list[dict], failed: dict[int, str]
) -> None:
    if not path:
        return
    lines = ["## 🔧 Auto-fix promotion", ""]
    if not (promoted or skipped or failed):
        lines.append("No new auto-fixable issues this run.")
    if promoted:
        lines.append(f"Sent {len(promoted)} issue(s) to auto-fix:")
        lines += [f"- #{i['number']} {i['title']}" for i in promoted]
    if skipped:
        lines += ["", "Over the per-run cap, so left for a maintainer to add `bug`:"]
        lines += [f"- #{i['number']} {i['title']}" for i in skipped]
    if failed:
        lines += ["", "Could not be sent to auto-fix:"]
        lines += [f"- #{n}: {reason}" for n, reason in failed.items()]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument(
        "--watermark",
        required=True,
        type=int,
        help="Highest issue number before synthesis ran; only newer issues qualify",
    )
    parser.add_argument("--ref", default="main")
    parser.add_argument("--max", type=int, default=5, help="Issues to send per run")
    parser.add_argument("--summary", default="", help="Append the outcome here")
    args = parser.parse_args(argv)

    candidates = select(list_audit_issues(args.repo), args.watermark)
    to_send, skipped = candidates[: args.max], candidates[args.max :]

    promoted: list[dict] = []
    failed: dict[int, str] = {}
    for issue in to_send:
        try:
            promote(args.repo, args.ref, issue["number"])
            promoted.append(issue)
        except RuntimeError as e:
            failed[issue["number"]] = str(e)

    report(args.summary, promoted, skipped, failed)
    for number, reason in failed.items():
        print(
            f"::error title=Auto-fix not started::#{number} was filed as auto-fixable "
            f"but was not sent to auto-fix: {reason}. Add the `bug` label by hand once "
            "the cause is fixed.",
            file=sys.stderr,
        )
    print(f"promoted={len(promoted)} over_cap={len(skipped)} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
