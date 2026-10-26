# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Rank open PRs and issues for triage: third-party first, bug fixes before features.

Read-only. Prints a worklist with the blockers each PR still has; it decides
nothing. A "ready" row only means the cheap signals are clean -- the human or
subagent still reads the diff and every comment before merging.

    python util/triage_scan.py
    python util/triage_scan.py --json
    python util/triage_scan.py --team some-login
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

BOT_LOGINS = {"dependabot", "github-actions", "claude", "copilot"}
LARGE_PR_LINES = 5000
FAILED = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ERROR", "CANCELLED", "STALE"}
# A PR whose workflows never started (fork runs awaiting approval, or failed before
# creating jobs) still shows green label/bot checks; these must be present to count.
REQUIRED_CHECKS = ("Unit Tests (Linux", "Run Code Quality Checks")

BUGFIX_TITLE = re.compile(
    r"^(fix|test)(\(|:|!)|^(chore|build)\(deps[^)]*\)|^bump ", re.I
)

_PR_QUERY = """
query($owner:String!,$name:String!,$cursor:String){
  repository(owner:$owner,name:$name){
    pullRequests(states:OPEN,first:50,after:$cursor){
      pageInfo{hasNextPage endCursor}
      nodes{
        number title url isDraft additions deletions mergeable reviewDecision
        maintainerCanModify headRefName headRefOid
        headRepository{nameWithOwner}
        author{login}
        headRepositoryOwner{login}
        labels(first:10){nodes{name}}
        milestone{title}
        closingIssuesReferences(first:5){nodes{number}}
        commits(last:1){nodes{commit{committedDate statusCheckRollup{contexts(first:100){totalCount pageInfo{hasNextPage} nodes{
          __typename
          ... on CheckRun{name conclusion status}
          ... on StatusContext{context state}
        }}}}}}
        comments(last:30){nodes{author{login} createdAt body}}
        reviews(last:30){nodes{author{login} submittedAt}}
        reviewThreads(first:100){nodes{isResolved isOutdated}}
      }
    }
  }
}
"""

_ISSUE_QUERY = """
query($owner:String!,$name:String!,$cursor:String){
  repository(owner:$owner,name:$name){
    issues(states:OPEN,first:100,after:$cursor,orderBy:{field:CREATED_AT,direction:DESC}){
      pageInfo{hasNextPage endCursor}
      nodes{
        number title createdAt
        author{login}
        labels(first:10){nodes{name}}
        milestone{title}
        comments(last:10){nodes{author{login}}}
        closedByPullRequestsReferences(first:10,includeClosedPrs:false){nodes{number}}
      }
    }
  }
}
"""


def gh_binary():
    path = os.environ.get("GH") or shutil.which("gh")
    if not path:
        sys.exit(
            "gh CLI not found. Install it, or set GH to its full path "
            '(Windows default: "C:\\Program Files\\GitHub CLI\\gh.exe").'
        )
    return path


def graphql(gh, query, repo, cursor):
    owner, name = repo.split("/")
    body = {
        "query": query,
        "variables": {"owner": owner, "name": name, "cursor": cursor},
    }
    done = subprocess.run(
        [gh, "api", "graphql", "--input", "-"],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if done.returncode != 0:
        sys.exit(
            f"gh graphql failed for {repo}: {done.stderr.strip() or done.stdout[:300]}"
        )
    payload = json.loads(done.stdout)
    if payload.get("errors") or not payload.get("data"):
        sys.exit(f"gh graphql returned errors for {repo}: {payload.get('errors')}")
    return payload["data"]["repository"]


def fetch_all(gh, query, field, repo):
    nodes, cursor = [], None
    while True:
        page = graphql(gh, query, repo, cursor)[field]
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def awaiting_approval(gh, repo):
    """Map head SHA -> run ids of workflow runs a maintainer must approve.

    Fork runs carry no PR link, so SHA is the only join key.
    """
    done = subprocess.run(
        [
            gh,
            "api",
            f"repos/{repo}/actions/runs?status=action_required&per_page=100",
            "--paginate",
            "--jq",
            r'.workflow_runs[] | "\(.head_sha) \(.id)"',
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if done.returncode != 0:
        sys.exit(f"Could not list workflow runs of {repo}: {done.stderr.strip()}")
    runs = {}
    for line in done.stdout.splitlines():
        sha, run_id = line.split()
        runs.setdefault(sha, []).append(int(run_id))
    return runs


def is_bot(login):
    return not login or login.endswith("[bot]") or login.lower() in BOT_LOGINS


def is_third_party(author, team):
    if author is None:  # deleted account ("ghost"): unknown, so not a maintainer
        return True
    login = author.get("login", "")
    return not is_bot(login) and login.lower() not in team


def maintainers(gh, repo, extra):
    """Push-access collaborators, plus the personal account behind each `-amd` one.

    PRs from a maintainer's personal fork report no org membership, so the
    collaborator list is the only reliable maintainer set.
    """
    done = subprocess.run(
        [
            gh,
            "api",
            f"repos/{repo}/collaborators?permission=push&per_page=100",
            "--paginate",
            "--jq",
            ".[].login",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if done.returncode != 0:
        sys.exit(
            f"Could not list collaborators of {repo} (needs push access): "
            f"{done.stderr.strip()}. Authenticate with a maintainer token."
        )
    team = set(extra)
    for login in done.stdout.split():
        login = login.lower()
        team.add(login)
        if login.endswith("-amd"):
            team.add(login[: -len("-amd")])
    return team


def classify_kind(title, labels):
    """bugfix = fix/test/dependency bump; everything else waits behind it."""
    if "bug" in labels or BUGFIX_TITLE.search(title):
        return "bugfix"
    return "other"


def summarize_checks(contexts):
    failing, pending, needs_approval = [], 0, 0
    for c in contexts:
        if c["__typename"] == "CheckRun":
            if c["status"] != "COMPLETED":
                pending += 1
            elif c["conclusion"] == "ACTION_REQUIRED":
                needs_approval += 1
            elif c["conclusion"] in FAILED:
                failing.append(c["name"])
        elif c["state"] in ("FAILURE", "ERROR"):
            failing.append(c["context"])
        elif c["state"] in ("PENDING", "EXPECTED"):
            pending += 1
    return failing, pending, needs_approval


def head_commit(pr):
    nodes = pr["commits"]["nodes"]
    return nodes[0]["commit"] if nodes else None


VERDICT = re.compile(r"\*\*Verdict:\s*([^*\n]+?)\s*\*\*", re.I)


def is_verdict_comment(c):
    return is_bot((c["author"] or {}).get("login")) and VERDICT.search(
        c.get("body") or ""
    )


def bot_verdict(pr):
    """Latest review-bot verdict as (text, postdates_head_commit), or None.

    A verdict older than the head commit says nothing about the current diff.
    """
    commit = head_commit(pr)
    head_at = commit["committedDate"] if commit else ""
    latest = None
    for c in pr["comments"]["nodes"]:
        if is_verdict_comment(c):
            latest = (
                VERDICT.search(c["body"]).group(1).lower(),
                c["createdAt"] > head_at,
            )
    return latest


def comments_after_last_commit(pr):
    """Count comments/reviews by others newer than the head commit."""
    commit = head_commit(pr)
    if commit is None:
        return 0
    head_at = commit["committedDate"]
    author = (pr["author"] or {}).get("login")
    events = [
        (c["author"], c["createdAt"])
        for c in pr["comments"]["nodes"]
        if not is_verdict_comment(c)
    ]
    events += [(r["author"], r["submittedAt"]) for r in pr["reviews"]["nodes"]]
    return sum(
        1
        for who, at in events
        if at and at > head_at and (who or {}).get("login") != author
    )


def assess_pr(pr, team, stalled=(), required=REQUIRED_CHECKS):
    commit = head_commit(pr)
    rollup = commit["statusCheckRollup"] if commit else None
    contexts = rollup["contexts"]["nodes"] if rollup else []
    failing, pending, needs_approval = summarize_checks(contexts)
    labels = [n["name"] for n in pr["labels"]["nodes"]]
    author = (pr["author"] or {}).get("login", "?")
    fork = (pr["headRepositoryOwner"] or {}).get("login") != "amd"
    unaddressed = comments_after_last_commit(pr)
    size = pr["additions"] + pr["deletions"]

    blockers = []
    if pr["isDraft"]:
        blockers.append("draft")
    if pr["mergeable"] == "CONFLICTING":
        blockers.append("conflict")
    elif pr["mergeable"] != "MERGEABLE":
        blockers.append("mergeability unknown: re-run the scan")
    if needs_approval or stalled:
        blockers.append(
            f"CI awaiting maintainer approval ({max(needs_approval, len(stalled))} runs)"
        )
    if not contexts:
        blockers.append("no CI results")
    else:
        names = [c.get("name") or c.get("context") or "" for c in contexts]
        missing = [r for r in required if not any(n.startswith(r) for n in names)]
        if missing:
            blockers.append("required CI not run: " + ", ".join(missing))
        if rollup["contexts"]["pageInfo"]["hasNextPage"]:
            blockers.append("checks truncated: read CI in the UI")
    if failing:
        blockers.append("CI red: " + ", ".join(sorted(set(failing))[:4]))
    if pending:
        blockers.append(f"CI pending ({pending})")
    verdict = bot_verdict(pr)
    notes = []
    if verdict is None:
        notes.append("no review-bot verdict")
    elif "approve" not in verdict[0]:
        blockers.append(f"review bot: {verdict[0]}")
    elif not verdict[1]:
        notes.append("bot verdict predates the last commit")
    if unaddressed:
        blockers.append(f"{unaddressed} comment(s) since last commit")
    threads = sum(
        1
        for t in pr["reviewThreads"]["nodes"]
        if not t["isResolved"] and not t["isOutdated"]
    )
    if threads:
        blockers.append(f"{threads} unresolved review thread(s)")
    if pr["reviewDecision"] == "CHANGES_REQUESTED":
        blockers.append("changes requested")
    if size > LARGE_PR_LINES:
        blockers.append(f"large ({size} lines): human sign-off")
    if fork and not pr["maintainerCanModify"]:
        blockers.append("fork, maintainer edits off")

    return {
        "number": pr["number"],
        "title": pr["title"],
        "author": author,
        "third_party": is_third_party(pr["author"], team),
        "kind": classify_kind(pr["title"], labels),
        "fork": fork,
        "lines": size,
        "milestone": (pr["milestone"] or {}).get("title"),
        "closes": [n["number"] for n in pr["closingIssuesReferences"]["nodes"]],
        "url": pr["url"],
        "head_sha": pr["headRefOid"],
        "head_ref": pr["headRefName"],
        "head_repo": (pr["headRepository"] or {}).get("nameWithOwner"),
        "maintainer_can_modify": pr["maintainerCanModify"],
        "stalled_runs": list(stalled),
        "bot_verdict": verdict[0] if verdict else None,
        "blockers": blockers,
        "notes": notes,
        "ready": not blockers,
    }


def pr_rank(row):
    """Third-party before maintainer, bug fixes before the rest, ready first."""
    return (
        not row["third_party"],
        row["kind"] != "bugfix",
        not row["ready"],
        row["number"],
    )


def assess_issue(issue, team, now):
    labels = [n["name"] for n in issue["labels"]["nodes"]]
    maintainer_replied = any(
        (c["author"] or {}).get("login", "").lower() in team
        for c in issue["comments"]["nodes"]
    )
    linked = sorted(
        n["number"] for n in issue["closedByPullRequestsReferences"]["nodes"]
    )
    created = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
    return {
        "number": issue["number"],
        "title": issue["title"],
        "author": (issue["author"] or {}).get("login", "?"),
        "third_party": is_third_party(issue["author"], team),
        "age_days": (now - created).days,
        "labels": labels,
        "milestone": (issue["milestone"] or {}).get("title"),
        "maintainer_replied": maintainer_replied,
        "open_prs": linked,
    }


def issue_rank(row):
    """Unanswered, unlabelled, bug-labelled third-party issues first."""
    return (
        row["maintainer_replied"],
        "bug" not in row["labels"],
        not row["open_prs"] == [],
        row["age_days"] * -1,
    )


def render(prs, issues):
    out = ["## Third-party PRs (bug fixes first)", ""]
    third = [p for p in prs if p["third_party"]]
    out += _pr_table(third) or ["_none open_"]
    out += ["", "## Maintainer PRs (bug fixes first)", ""]
    out += _pr_table([p for p in prs if not p["third_party"]]) or ["_none open_"]
    out += ["", "## Third-party issues (unanswered first)", ""]
    out += _issue_table(issues) or ["_none open_"]
    return "\n".join(out)


def _pr_table(rows):
    if not rows:
        return []
    lines = [
        "| PR | author | kind | lines | state | title |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        state = "ready candidate" if r["ready"] else "; ".join(r["blockers"])
        if r["notes"]:
            state += f" [{'; '.join(r['notes'])}]"
        fork = " (fork)" if r["fork"] else ""
        lines.append(
            f"| #{r['number']} | {r['author']}{fork} | {r['kind']} | {r['lines']} "
            f"| {state} | {r['title'][:70]} |"
        )
    return lines


def _issue_table(rows):
    if not rows:
        return []
    lines = ["| issue | author | age | replied | open PRs | labels | title |"]
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        prs = ",".join(f"#{n}" for n in r["open_prs"]) or "-"
        lines.append(
            f"| #{r['number']} | {r['author']} | {r['age_days']}d "
            f"| {'yes' if r['maintainer_replied'] else 'NO'} | {prs} "
            f"| {','.join(r['labels']) or '-'} | {r['title'][:60]} |"
        )
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default="amd/gaia")
    ap.add_argument(
        "--team",
        default="",
        help="extra logins to treat as maintainers (comma-separated)",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--require",
        default=",".join(REQUIRED_CHECKS),
        help="comma-separated check-name prefixes that must exist on every PR",
    )
    ap.add_argument(
        "--all-issues", action="store_true", help="include maintainer-filed issues"
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    gh = gh_binary()
    extra = {t.strip().lower() for t in args.team.split(",") if t.strip()}
    team = maintainers(gh, args.repo, extra)
    required = tuple(r for r in args.require.split(",") if r)
    now = datetime.now(timezone.utc)

    stalled = awaiting_approval(gh, args.repo)
    prs = [
        assess_pr(p, team, stalled.get(p["headRefOid"], ()), required)
        for p in fetch_all(gh, _PR_QUERY, "pullRequests", args.repo)
    ]
    prs.sort(key=pr_rank)
    issues = [
        assess_issue(i, team, now)
        for i in fetch_all(gh, _ISSUE_QUERY, "issues", args.repo)
    ]
    if not args.all_issues:
        issues = [i for i in issues if i["third_party"]]
    issues.sort(key=issue_rank)

    if args.json:
        print(json.dumps({"prs": prs, "issues": issues}, indent=2))
    else:
        print(render(prs, issues))


if __name__ == "__main__":
    main()
