#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read the audits' labelled issues and, for each closed duplicate, its canonical.

Shared by prepare_synthesis.py (re-home a duplicate's dedup keys onto its open
canonical) and miss_analysis.py (count what the audits filed and what got closed
as noise).

Two reads, both small GraphQL pages with a hard cap that fails loudly:

1. Every issue with the label, number and close reason only.
2. Only the duplicates, with what names their canonical: GitHub's own
   ``MarkedAsDuplicateEvent`` (what the UI's "Close as duplicate" records — it
   writes no comment), the last comments, and the body.

``gh issue list --limit 5000 --json ...,comments`` did both in one unbounded
query, pulled every comment of every closed issue, and could not tell a short
list from a truncated one.

stdlib + gh only.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

#: Issues read per page in the cheap listing.
LIST_PAGE = 100

#: Duplicates whose details are read per query.
DETAIL_PAGE = 25

#: The newest comments read per duplicate. The canonical is named when the issue
#: is closed, so it is among the last few; older comments are not needed.
COMMENTS_READ = 10

#: Refuse to read past this many labelled issues instead of silently stopping.
DEFAULT_CAP = 3000

#: "Duplicate of #N": GitHub's wording, and what triage comments use.
DUPLICATE_OF_RE = re.compile(r"duplicate of #(\d+)", re.IGNORECASE)

_LIST_QUERY = """
query($owner: String!, $name: String!, $label: String!, $states: [IssueState!],
      $first: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(labels: [$label], states: $states, first: $first, after: $cursor,
           orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { id number state stateReason }
    }
  }
}
"""

_DETAIL_QUERY = """
query($ids: [ID!]!, $comments: Int!) {
  nodes(ids: $ids) {
    ... on Issue {
      number
      body
      comments(last: $comments) { nodes { body } }
      timelineItems(last: 10,
                    itemTypes: [MARKED_AS_DUPLICATE_EVENT, UNMARKED_AS_DUPLICATE_EVENT]) {
        nodes {
          __typename
          ... on MarkedAsDuplicateEvent {
            duplicate { ... on Issue { number } ... on PullRequest { number } }
            canonical { ... on Issue { number } ... on PullRequest { number } }
          }
          ... on UnmarkedAsDuplicateEvent {
            duplicate { ... on Issue { number } ... on PullRequest { number } }
            canonical { ... on Issue { number } ... on PullRequest { number } }
          }
        }
      }
    }
  }
}
"""


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables})
    try:
        completed = subprocess.run(
            ["gh", "api", "graphql", "--input", "-"],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "`gh` is not on PATH. It is preinstalled on GitHub-hosted runners and only "
            "needs GH_TOKEN in the environment."
        ) from exc
    if completed.returncode != 0:
        raise SystemExit(
            f"`gh api graphql` failed with exit {completed.returncode}:\n"
            f"{completed.stderr.strip()}\nRefusing to go on with a partial issue list."
        )
    data = json.loads(completed.stdout)
    if data.get("errors"):
        raise SystemExit(f"GraphQL returned errors: {json.dumps(data['errors'])[:800]}")
    return data["data"]


def list_labelled(
    repo: str, label: str, states: tuple[str, ...], cap: int = DEFAULT_CAP
) -> list[dict[str, Any]]:
    """Every issue with *label* in *states*: ``{id, number, state, stateReason}``."""
    owner, name = repo.split("/", 1)
    issues: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = graphql(
            _LIST_QUERY,
            {
                "owner": owner,
                "name": name,
                "label": label,
                "states": list(states),
                "first": LIST_PAGE,
                "cursor": cursor,
            },
        )["repository"]["issues"]
        issues += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return issues
        if len(issues) >= cap:
            raise SystemExit(
                f"{repo} has {page['totalCount']} issues labelled {label!r} in "
                f"{'/'.join(states)}, over the cap of {cap}. Stopping rather than "
                "reading part of the list: raise the cap (--closed-cap) knowingly."
            )
        cursor = page["pageInfo"]["endCursor"]


def duplicate_details(ids: list[str]) -> list[dict[str, Any]]:
    """Body, newest comments and duplicate events for each issue id."""
    details = []
    for start in range(0, len(ids), DETAIL_PAGE):
        nodes = graphql(
            _DETAIL_QUERY,
            {"ids": ids[start : start + DETAIL_PAGE], "comments": COMMENTS_READ},
        )["nodes"]
        for node in nodes:
            if not node:
                continue
            details.append(
                {
                    "number": node["number"],
                    "body": node.get("body") or "",
                    "comments": [
                        {"body": c.get("body") or ""}
                        for c in (node.get("comments") or {}).get("nodes") or []
                    ],
                    "duplicate_events": [
                        {
                            "type": e["__typename"],
                            "duplicate": (e.get("duplicate") or {}).get("number"),
                            "canonical": (e.get("canonical") or {}).get("number"),
                        }
                        for e in (node.get("timelineItems") or {}).get("nodes") or []
                        if e
                    ],
                }
            )
    return details


def fetch_closed_duplicates(
    repo: str, label: str, cap: int = DEFAULT_CAP
) -> list[dict[str, Any]]:
    """Closed-as-duplicate issues with *label*, ready for :func:`canonical_of`."""
    closed = list_labelled(repo, label, ("CLOSED",), cap)
    ids = [i["id"] for i in closed if i.get("stateReason") == "DUPLICATE"]
    return [dict(d, stateReason="DUPLICATE") for d in duplicate_details(ids)]


def canonical_of(issue: dict[str, Any]) -> int | None:
    """The issue this duplicate points at, or None when nothing names one.

    GitHub's own record wins: the newest duplicate event about this issue, unless
    it was later unmarked. Then the newest "Duplicate of #N" in a comment, then
    the body. Never the issue itself.
    """
    number = issue.get("number")
    canonical: int | None = None
    for event in issue.get("duplicate_events") or []:
        if event.get("duplicate") not in (None, number):
            continue
        if event.get("type") == "MarkedAsDuplicateEvent":
            canonical = event.get("canonical")
        elif event.get("type") == "UnmarkedAsDuplicateEvent":
            canonical = None
    if canonical is not None and canonical != number:
        return canonical
    texts = [c.get("body") or "" for c in reversed(issue.get("comments") or [])]
    texts.append(issue.get("body") or "")
    for text in texts:
        matches = [int(n) for n in DUPLICATE_OF_RE.findall(text) if int(n) != number]
        if matches:
            return matches[-1]
    return None
