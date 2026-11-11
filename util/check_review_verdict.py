#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fail a PR's status while its review still says Request changes.

The review is a comment, so nothing stopped a merge: 26 PRs marked Request changes
merged with no later push, and 8 reviewed bugs shipped that way. A reply does not
clear this status. Only a push that re-review passes, or a maintainer adding
``review-override``, does.

The verdict is read from the ``review-verdict`` artifact that claude-run.yml uploads
from a review run of claude.yml, never from comment text: a comment proves nothing
about which job wrote it. The artifact exists only when the review job finished on
the requested model and posted what it wrote (or, on a re-review, chose silence).

Reads run records and labels, and posts a status; it never runs PR code. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

OVERRIDE_LABEL = "review-override"
OVERRIDE_ROLES = {"admin", "maintain", "write"}
CONTEXT = "PR review verdict"
ARTIFACT = "review-verdict"
REVIEW_WORKFLOW = "claude.yml"
VERDICT = re.compile(r"^\*\*Verdict: ([A-Za-z ]+?)\*\*", re.MULTILINE)
SHA = re.compile(r"^[0-9a-f]{40}$")
BLOCKING = "Request changes"
#: Silent re-review: the reviewer finished and found nothing blocking.
CLEAR = "clear"
VERDICTS = {"Approve", "Approve with suggestions", BLOCKING, CLEAR}
#: A review ran but its record aged out of artifact retention.
EXPIRED = "expired"
MAX_DESCRIPTION = 140
GH_TIMEOUT = 60


class NotFound(RuntimeError):
    """gh got HTTP 404."""


def _gh(*args: str) -> str:
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"gh {args[0]} {args[1]} timed out after {GH_TIMEOUT}s"
        ) from e
    if result.returncode != 0:
        if "(HTTP 404)" in result.stderr:
            raise NotFound(f"gh {args[0]} {args[1]}: {result.stderr.strip()}")
        raise RuntimeError(f"gh {args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout


def parse_record(text: str, number: int) -> tuple[str, str] | None:
    """``(reviewed_sha, verdict)`` from an artifact, or None if it isn't this PR's."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("pr") != number:
        return None
    sha, verdict = data.get("sha"), data.get("verdict")
    if not isinstance(sha, str) or not SHA.match(sha) or not isinstance(verdict, str):
        return None
    # An unknown verdict is a review with no readable verdict line.
    return sha, verdict if verdict in VERDICTS else ""


def review_runs(repo: str, branch: str, head_repo: str) -> list[dict]:
    """claude.yml review runs for this head branch, newest first."""
    pages = _gh(
        "api",
        "--paginate",
        f"repos/{repo}/actions/workflows/{REVIEW_WORKFLOW}/runs"
        f"?event=pull_request_target&branch={quote(branch, safe='')}&per_page=100",
        "--jq",
        ".workflow_runs[] | {id, created_at, conclusion, "
        "head: .head_repository.full_name}",
    )
    runs = [json.loads(line) for line in pages.splitlines() if line.strip()]
    runs = [
        r
        for r in runs
        if r["head"] == head_repo and r["conclusion"] in ("success", "failure")
    ]
    return sorted(runs, key=lambda r: r["created_at"], reverse=True)


def read_artifact(repo: str, run_id: int) -> str | None:
    """The run's verdict.json, None if it uploaded none, or EXPIRED."""
    expired = _gh(
        "api",
        f"repos/{repo}/actions/runs/{run_id}/artifacts?name={ARTIFACT}",
        "--jq",
        ".artifacts[] | .expired",
    ).split()
    if not expired:
        return None
    if "false" not in expired:
        return EXPIRED
    with tempfile.TemporaryDirectory() as tmp:
        _gh("run", "download", str(run_id), "--repo", repo, "-n", ARTIFACT, "-D", tmp)
        path = Path(tmp) / "verdict.json"
        return path.read_text(encoding="utf-8") if path.is_file() else None


def latest_review(
    repo: str, number: int, branch: str, head_repo: str
) -> tuple[str, str] | str | None:
    """The newest ``(reviewed_sha, verdict)``, EXPIRED, or None if never reviewed."""
    for run in review_runs(repo, branch, head_repo):
        text = read_artifact(repo, run["id"])
        if text is None:
            continue
        # Its verdict can't be read, so an older record must not stand in for it.
        if text == EXPIRED:
            return EXPIRED
        record = parse_record(text, number)
        if record is not None:
            return record
    return None


def override_by_maintainer(repo: str, number: int, labels: set[str]) -> bool:
    """The label counts only when a person with write access or above added it last."""
    if OVERRIDE_LABEL not in labels:
        return False
    events = _gh(
        "api",
        "--paginate",
        f"repos/{repo}/issues/{number}/events?per_page=100",
        "--jq",
        f'.[] | select(.event == "labeled" and .label.name == "{OVERRIDE_LABEL}") '
        "| {login: .actor.login, type: .actor.type}",
    )
    added = [json.loads(line) for line in events.splitlines() if line.strip()]
    if not added:
        return False
    actor = added[-1]
    if actor.get("type") != "User":
        return False
    try:
        role = _gh(
            "api",
            f"repos/{repo}/collaborators/{actor['login']}/permission",
            "--jq",
            ".role_name",
        ).strip()
    except NotFound:
        # A removed collaborator.
        return False
    return role in OVERRIDE_ROLES


def decide(
    review: tuple[str, str] | str | None, head_sha: str, overridden: bool
) -> tuple[str, str]:
    """Commit status ``(state, description)`` for this PR's head."""
    if overridden:
        return "success", f"A maintainer overrode the review with {OVERRIDE_LABEL}"
    if review is None:
        return "success", "No review to answer"
    if review == EXPIRED:
        return "pending", "The latest review record expired, so re-run the review"
    reviewed_sha, verdict = review
    if not verdict:
        return "failure", "The latest review has no verdict line, so re-run the review"
    if verdict != BLOCKING:
        return "success", f"Review verdict: {verdict}"
    if reviewed_sha == head_sha:
        return "failure", (
            f"Review requested changes. Push a fix, or a maintainer adds {OVERRIDE_LABEL}"
        )
    return "pending", "Waiting for re-review of this commit"


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
            "headRefOid,headRefName,headRepository,headRepositoryOwner,labels",
        )
    )
    head_sha = pr["headRefOid"]
    if not pr.get("headRepository"):
        raise RuntimeError(
            f"#{number}: its head repository is gone, so no review is found"
        )
    head_repo = f"{pr['headRepositoryOwner']['login']}/{pr['headRepository']['name']}"
    labels = {label["name"] for label in pr["labels"]}
    overridden = override_by_maintainer(repo, number, labels)
    review = latest_review(repo, number, pr["headRefName"], head_repo)
    state, description = decide(review, head_sha, overridden)
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
    if not numbers:
        print(
            f"::warning title=No verdict posted::no open PR has head {args.sha}, so "
            "no status was posted"
        )
    for number in numbers:
        evaluate(args.repo, number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
