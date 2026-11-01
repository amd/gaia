#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fail a PR's status while its review still says Request changes.

The review is a comment, so nothing stopped a merge: 26 PRs marked Request changes
merged with no later push, and 8 reviewed bugs shipped that way. A reply does not
clear this status. Only a push that re-review passes, or a maintainer adding
``review-override``, does.

claude-run.yml stamps each review with the commit it read, so a push is detected
from commits and never from comment dates. Reviews with no stamp predate this check
and are ignored.

Reads comments and posts a status; it never runs PR code. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

BOT_AUTHORS = {"github-actions", "app/github-actions", "github-actions[bot]"}
OVERRIDE_LABEL = "review-override"
CONTEXT = "PR review verdict"
STAMP = re.compile(r"<!-- reviewed-sha: ([0-9a-f]{40}) -->")
VERDICT = re.compile(r"^\*\*Verdict: ([A-Za-z ]+?)\*\*", re.MULTILINE)
BLOCKING = "Request changes"
MAX_DESCRIPTION = 140


def _gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout


def latest_review(comments: list[dict]) -> tuple[str, str] | None:
    """``(reviewed_sha, verdict)`` of the newest stamped bot review; verdict may be ''."""
    for comment in reversed(comments):
        if (comment.get("author") or {}).get("login") not in BOT_AUTHORS:
            continue
        body = comment.get("body") or ""
        stamps = STAMP.findall(body)
        if stamps:
            verdict = VERDICT.search(body)
            # Last: the workflow appends its stamp after anything the model wrote.
            return stamps[-1], verdict.group(1) if verdict else ""
    return None


def decide(
    review: tuple[str, str] | None, head_sha: str, overridden: bool, rereviewed: bool
) -> tuple[str, str]:
    """Commit status ``(state, description)`` for this PR's head."""
    if overridden:
        return "success", f"A maintainer overrode the review with {OVERRIDE_LABEL}"
    if review is None:
        return "success", "No review to answer"
    reviewed_sha, verdict = review
    if not verdict:
        return "failure", "The latest review has no verdict line, so re-run the review"
    if verdict != BLOCKING:
        return "success", f"Review verdict: {verdict}"
    if reviewed_sha == head_sha:
        return "failure", (
            f"Review requested changes. Push a fix, or a maintainer adds {OVERRIDE_LABEL}"
        )
    if rereviewed:
        return "success", "Re-review found nothing still blocking on this commit"
    return "pending", "Waiting for re-review of this commit"


def rereview_finished(repo: str, head_sha: str) -> bool:
    runs = _gh(
        "api",
        f"repos/{repo}/actions/workflows/claude.yml/runs"
        f"?head_sha={head_sha}&status=success&event=pull_request_target",
        "--jq",
        ".total_count",
    )
    return int(runs.strip()) > 0


def pulls_for(repo: str, sha: str) -> list[int]:
    # By search: workflow_run lists no pull requests for a fork.
    found = _gh(
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--search",
        sha,
        "--json",
        "number,headRefOid",
    )
    return [pr["number"] for pr in json.loads(found) if pr["headRefOid"] == sha]


def evaluate(repo: str, number: int) -> None:
    pr = json.loads(
        _gh(
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "comments,headRefOid,labels",
        )
    )
    head_sha = pr["headRefOid"]
    review = latest_review(pr["comments"])
    overridden = OVERRIDE_LABEL in {label["name"] for label in pr["labels"]}
    stale = review is not None and review[0] != head_sha
    rereviewed = stale and rereview_finished(repo, head_sha)
    state, description = decide(review, head_sha, overridden, rereviewed)
    _gh(
        "api",
        f"repos/{repo}/statuses/{head_sha}",
        "-f",
        f"state={state}",
        "-f",
        f"context={CONTEXT}",
        "-f",
        f"description={description[:MAX_DESCRIPTION]}",
    )
    print(f"#{number} {head_sha[:9]}: {state}: {description}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--sha", help="Find the open PRs at this head instead")
    args = parser.parse_args(argv)
    if bool(args.pr) == bool(args.sha):
        parser.error("pass exactly one of --pr and --sha")

    numbers = [args.pr] if args.pr else pulls_for(args.repo, args.sha)
    for number in numbers:
        evaluate(args.repo, number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
