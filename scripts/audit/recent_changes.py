#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Write a compact, bounded summary of what merged to main since this audit last ran.

Each audit now runs every third night, so a lens starting cold re-derives "what
changed" from scratch, spends turns on it, and still misses the PR that fixed the
thing it is about to file. This does it once, deterministically, before any
Claude call: first-parent commits on main since the commit the audit's last
SUCCESSFUL run checked out, each with its PR title, a short body excerpt and the
paths it touched. The prompts read the file to

* look first at recently changed code, docs and workflows (new code is where new
  bugs are), and at the code next to it (regressions),
* check docs that a recent merge touched, or whose described behaviour it changed,
* not re-file something a recent PR just fixed.

The window is exact when the last run's commit is known (audit_history.py window
prints it as base_sha) and falls back to ``--fallback-days`` otherwise, saying so.
Output is capped at ``--max-bytes`` so it cannot eat the usage budget.

stdlib + git + gh only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

PR_RE = re.compile(r"\(#(\d+)\)\s*$")
COMMIT_SEP = "\x1e"
FIELD_SEP = "\x1f"
MAX_PATHS_PER_COMMIT = 12
BODY_CHARS = 300


def _run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", check=False
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"`{args[0]}` is not on PATH: {exc}") from exc


def _must(args: list[str]) -> str:
    completed = _run(args)
    if completed.returncode != 0:
        raise SystemExit(
            f"`{' '.join(args[:4])} ...` failed with exit {completed.returncode}:\n"
            f"{completed.stderr.strip()}\nThe audit's recent-changes context could "
            "not be built; fix this rather than auditing without it."
        )
    return completed.stdout


def resolve_range(base_sha: str, fallback_days: int) -> tuple[list[str], str, bool]:
    """Return ``(git log selector args, plain-words description, exact?)``."""
    if base_sha:
        known = _run(["git", "cat-file", "-e", f"{base_sha}^{{commit}}"]).returncode == 0
        ancestor = (
            known
            and _run(["git", "merge-base", "--is-ancestor", base_sha, "HEAD"]).returncode
            == 0
        )
        if ancestor:
            return (
                [f"{base_sha}..HEAD"],
                f"everything merged to main since this audit's last successful run "
                f"(`{base_sha[:9]}`)",
                True,
            )
        why = (
            f"the last successful run's commit `{base_sha[:9]}` is not in this "
            "checkout's history"
        )
    else:
        why = "no successful run of this audit is on record"
    return (
        [f"--since={fallback_days}.days", "HEAD"],
        f"the last {fallback_days} days, because {why}",
        False,
    )


def read_commits(selector: list[str]) -> list[dict]:
    raw = _must(
        [
            "git",
            "log",
            "--first-parent",
            f"--format={COMMIT_SEP}%H{FIELD_SEP}%cI{FIELD_SEP}%s",
            "--name-only",
            *selector,
        ]
    )
    commits = []
    for block in raw.split(COMMIT_SEP):
        if not block.strip():
            continue
        head, _, rest = block.partition("\n")
        sha, when, subject = head.split(FIELD_SEP, 2)
        paths = [line.strip() for line in rest.splitlines() if line.strip()]
        match = PR_RE.search(subject)
        commits.append(
            {
                "sha": sha,
                "date": when,
                "subject": subject,
                "pr": int(match.group(1)) if match else None,
                "paths": paths,
            }
        )
    return commits


def fetch_prs(repo: str, commits: list[dict]) -> dict[int, dict]:
    """One `gh` call for every merged PR the window could contain."""
    if not any(c["pr"] for c in commits):
        return {}
    oldest = min(datetime.fromisoformat(c["date"]) for c in commits)
    since = (oldest - timedelta(days=1)).date().isoformat()
    prs = json.loads(
        _must(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "merged",
                "--search",
                f"merged:>={since}",
                "--limit",
                "1000",
                "--json",
                "number,title,body",
            ]
        )
    )
    return {p["number"]: p for p in prs}


def excerpt(body: str) -> str:
    """The PR's opening prose: no comments, collapsed blocks, headings or test plan."""
    text = re.sub(r"<!--.*?-->", " ", body or "", flags=re.S)
    text = re.sub(r"<details>.*?</details>", " ", text, flags=re.S | re.I)
    text = re.split(r"^#+\s*test plan", text, flags=re.I | re.M)[0]
    text = re.sub(r"^#+.*$", " ", text, flags=re.M)
    text = " ".join(text.split())
    return text if len(text) <= BODY_CHARS else text[:BODY_CHARS].rsplit(" ", 1)[0] + " …"


def area(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:3]) if parts[0] in ("src", "hub") else "/".join(parts[:2])


def render(commits: list[dict], prs: dict[int, dict], description: str, max_bytes: int) -> str:
    areas = Counter(area(p) for c in commits for p in c["paths"])
    lines = [
        "# Recent changes on main",
        "",
        f"{len(commits)} commit(s): {description}. Newest first. Read this before "
        "auditing: look hardest at what changed and at the code next to it, check "
        "docs these changes touched or made wrong, and do not report something a PR "
        "below already fixed.",
        "",
        "Most-changed areas: "
        + (", ".join(f"`{a}` ({n})" for a, n in areas.most_common(10)) or "none"),
        "",
    ]
    size = sum(len(line) + 1 for line in lines)
    shown = 0
    for commit in commits:
        pr = prs.get(commit["pr"]) if commit["pr"] else None
        title = pr["title"] if pr else commit["subject"]
        ref = f"#{commit['pr']}" if commit["pr"] else commit["sha"][:9]
        block = [f"## {ref} {title}"]
        body = excerpt(pr["body"]) if pr else ""
        if body:
            block.append(body)
        paths = commit["paths"][:MAX_PATHS_PER_COMMIT]
        more = len(commit["paths"]) - len(paths)
        block.append(
            "Paths: "
            + (", ".join(f"`{p}`" for p in paths) or "none")
            + (f" (+{more} more)" if more > 0 else "")
        )
        block.append("")
        cost = sum(len(line) + 1 for line in block)
        if size + cost > max_bytes:
            break
        lines += block
        size += cost
        shown += 1
    if shown < len(commits):
        lines.append(
            f"…{len(commits) - shown} older commit(s) omitted to keep this under "
            f"{max_bytes} bytes; `git log --first-parent` has them."
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--fallback-days", type=int, default=3)
    parser.add_argument("--max-bytes", type=int, default=40_000)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--paths-out", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args(argv)

    selector, description, exact = resolve_range(args.base_sha, args.fallback_days)
    commits = read_commits(selector)
    prs = fetch_prs(args.repo, commits)
    args.out.write_text(render(commits, prs, description, args.max_bytes), encoding="utf-8")
    paths = sorted({p for c in commits for p in c["paths"]})
    args.paths_out.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
    with args.summary.open("a", encoding="utf-8") as fh:
        fh.write(f"**Recent changes read first:** {len(commits)} commit(s), {description}.\n\n")
    if not exact:
        print(f"::warning title=Audit window is approximate::Covering {description}.")
    print(f"{len(commits)} commits, {len(paths)} paths -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
