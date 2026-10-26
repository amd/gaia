# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Approve the stalled CI runs of a fork PR -- only for a commit you have reviewed.

A first-time contributor's workflows wait in `action_required` until a maintainer
approves them, and an approved run executes the PR's code on our runners. So the
approval is tied to the exact commit you read: pass `--reviewed N@<sha>` (the
head SHA you reviewed, 7+ hex chars). A contributor who pushes again after your
review gets new runs for a new SHA, which this tool will not approve.

Dry run by default; nothing is approved without `--apply`. A PR that touches CI,
install, packaging or test-harness paths is refused unless you also pass
`--include-risky N` after reading those files.

    python approve_fork_runs.py --reviewed 5732@6d02e4f --reviewed 5741@1a2b3c4
    python approve_fork_runs.py --reviewed 5732@6d02e4f --apply
"""

import argparse
import json
import re
import subprocess
import sys

import triage_scan as ts

# Paths where a change runs with the runner's privileges or alters what CI trusts.
RISKY_PATHS = re.compile(
    r"^(\.github/|\.claude/|util/|scripts/|installer/|setup\.py$|pyproject\.toml$|"
    r"setup\.cfg$|tox\.ini$|Makefile$|Dockerfile|requirements[^/]*\.txt$|"
    r"\.npmrc$|\.pth$|.*conftest\.py$|.*package(-lock)?\.json$|.*\.(sh|ps1|bat|cmd)$)"
)


def api(gh, path, *extra):
    done = subprocess.run(
        [gh, "api", path, *extra], capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode != 0:
        sys.exit(f"gh api {path} failed: {done.stderr.strip() or done.stdout[:300]}")
    return done.stdout


def parse_reviewed(items):
    reviewed = {}
    for item in items:
        m = re.fullmatch(r"(\d+)@([0-9a-fA-F]{7,40})", item)
        if not m:
            sys.exit(f"--reviewed expects N@<7+ hex sha>, got {item!r}")
        reviewed[int(m.group(1))] = m.group(2).lower()
    return reviewed


def risky_files(files):
    return sorted(f for f in files if RISKY_PATHS.match(f))


def plan_for(gh, repo, number, sha_prefix, stalled, team):
    """Decide what approving this PR would mean; raises SystemExit on a mismatch."""
    pr = json.loads(api(gh, f"repos/{repo}/pulls/{number}"))
    head = pr["head"]["sha"]
    if not head.startswith(sha_prefix):
        sys.exit(
            f"#{number}: head is now {head[:10]}, not the {sha_prefix} you reviewed. "
            "Read the new commits, then pass the new SHA."
        )
    if pr["state"] != "open":
        sys.exit(f"#{number} is {pr['state']}, not open.")
    author = pr["user"] or None
    files = json.loads(
        api(
            gh,
            f"repos/{repo}/pulls/{number}/files?per_page=100",
            "--paginate",
            "--slurp",
        )
    )
    names = [f["filename"] for page in files for f in page]
    return {
        "number": number,
        "sha": head,
        "author": (author or {}).get("login", "ghost"),
        "third_party": ts.is_third_party(author, team),
        "files": names,
        "risky": risky_files(names),
        "runs": stalled.get(head, []),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default="amd/gaia")
    ap.add_argument("--team", default="", help="extra maintainer logins")
    ap.add_argument(
        "--reviewed",
        action="append",
        required=True,
        metavar="N@SHA",
        help="PR number and the head SHA you reviewed (repeatable)",
    )
    ap.add_argument(
        "--include-risky",
        default="",
        help="comma-separated PR numbers whose sensitive-path changes you read",
    )
    ap.add_argument("--apply", action="store_true", help="actually approve the runs")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    gh = ts.gh_binary()
    reviewed = parse_reviewed(args.reviewed)
    allow_risky = {int(n) for n in args.include_risky.split(",") if n.strip()}
    extra = {t.strip().lower() for t in args.team.split(",") if t.strip()}
    team = ts.maintainers(gh, args.repo, extra)
    stalled = ts.awaiting_approval(gh, args.repo)

    failures = 0
    for number, sha in sorted(reviewed.items()):
        plan = plan_for(gh, args.repo, number, sha, stalled, team)
        label = f"#{number} @{plan['sha'][:10]} ({plan['author']}, {len(plan['files'])} files)"
        if not plan["third_party"]:
            print(
                f"SKIP {label}: author is a maintainer; runs need no outside approval"
            )
            continue
        if not plan["runs"]:
            print(f"SKIP {label}: no runs awaiting approval for this commit")
            continue
        if plan["risky"] and number not in allow_risky:
            print(
                f"REFUSE {label}: touches {', '.join(plan['risky'])} -- read it, "
                f"then re-run with --include-risky {number}"
            )
            failures += 1
            continue
        if not args.apply:
            print(f"WOULD APPROVE {label}: {len(plan['runs'])} run(s)")
            continue
        ok = 0
        for run_id in plan["runs"]:
            done = subprocess.run(
                [
                    gh,
                    "api",
                    "-X",
                    "POST",
                    f"repos/{args.repo}/actions/runs/{run_id}/approve",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if done.returncode == 0:
                ok += 1
            else:
                failures += 1
                print(
                    f"  run {run_id} failed: {done.stderr.strip() or done.stdout.strip()}"
                )
        print(f"APPROVED {label}: {ok}/{len(plan['runs'])} run(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
