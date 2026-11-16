#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Activity stats between two release refs, for the internal leadership summary.

Usage:
    python release_stats.py <prev_final_tag> <release_ref> [--repo amd/gaia]
        [--maintainers kovtcharov,kovtcharov-amd,"Kalin Ovtcharov"] [--no-github]

Prints markdown. It never prints an eval score: quote that from the release
eval run by hand.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime

DEFAULT_REPO = "amd/gaia"
DEFAULT_MAINTAINERS = "kovtcharov,kovtcharov-amd,Kalin Ovtcharov"
COMMIT_TYPES = ("fix", "feat", "test", "docs", "ci", "perf", "refactor")
AREAS = ("src/gaia", "src/gaia/apps/webui", "hub", "tests", "tui", "docs")
TOP_AUTHORS = 5

_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
_TYPE_RE = re.compile(r"^([a-z]+)(\([^)]*\))?!?:")


class ReleaseStatsError(RuntimeError):
    """A ref, git, or gh problem the caller must fix before stats can be read."""


def _git(args, cwd=None):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as e:
        raise ReleaseStatsError("git is not on PATH; install git and retry.") from e
    if result.returncode != 0:
        raise ReleaseStatsError(
            f"`git {' '.join(args)}` failed: {result.stderr.strip()}"
        )
    return result.stdout


def _require_ref(ref, cwd=None):
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseStatsError(
            f"ref '{ref}' does not exist in this repository. "
            "Run `git fetch origin --tags` and check the tag name with "
            "`git tag -l 'v*'`."
        )


def _ref_date(ref, cwd=None):
    return datetime.fromisoformat(_git(["log", "-1", "--format=%cI", ref], cwd).strip())


def _is_maintainer(name, email, maintainers):
    local = email.split("@", 1)[0].split("+", 1)[-1].lower()
    return name.lower() in maintainers or local in maintainers


def _shortstat(prev, ref, cwd=None, path=None):
    args = ["diff", "--shortstat", prev, ref]
    if path:
        args += ["--", path]
    text = _git(args, cwd)

    def grab(word):
        m = re.search(rf"(\d+) {word}", text)
        return int(m.group(1)) if m else 0

    return {
        "files": grab("files? changed"),
        "insertions": grab(r"insertions?\(\+\)"),
        "deletions": grab(r"deletions?\(-\)"),
    }


def _tree_counts(ref, cwd=None):
    tests = _git(["ls-tree", "-r", "--name-only", ref, "--", "tests"], cwd).split()
    skills = _git(["ls-tree", "-r", "--name-only", ref, "--", "hub/skills"], cwd)
    test_files = [
        p
        for p in tests
        if p.rsplit("/", 1)[-1].startswith("test_") and p.endswith(".py")
    ]
    skill_files = [
        p for p in skills.split() if p.endswith("/SKILL.md") and len(p.split("/")) == 4
    ]
    return {"test_files": len(test_files), "skills": len(skill_files)}


def collect(prev, ref, maintainers, cwd=None):
    """Git-only stats for prev..ref."""
    _require_ref(prev, cwd)
    _require_ref(ref, cwd)
    maintainers = {m.strip().lower() for m in maintainers if m.strip()}

    start, end = _ref_date(prev, cwd), _ref_date(ref, cwd)
    log = _git(["log", "--format=%an%x1f%ae%x1f%s", f"{prev}..{ref}"], cwd)

    prs = {}
    authors = Counter()
    outside = set()
    for line in log.splitlines():
        if not line.strip():
            continue
        name, email, subject = line.split("\x1f", 2)
        m = _PR_RE.search(subject)
        if m:
            prs[int(m.group(1))] = subject
        if "[bot]" in name or "[bot]" in email:
            continue
        authors[name] += 1
        if not _is_maintainer(name, email, maintainers):
            outside.add(name)

    types = Counter()
    for subject in prs.values():
        t = _TYPE_RE.match(subject)
        kind = t.group(1) if t else "other"
        types[kind if kind in COMMIT_TYPES else "other"] += 1

    return {
        "prev": prev,
        "ref": ref,
        "start": start,
        "end": end,
        "days": (end - start).days,
        "prs": len(prs),
        "types": {k: types.get(k, 0) for k in (*COMMIT_TYPES, "other")},
        "contributors": len(authors),
        "outside_contributors": len(outside),
        "top_authors": authors.most_common(TOP_AUTHORS),
        "diff_total": _shortstat(prev, ref, cwd),
        "diff_areas": {a: _shortstat(prev, ref, cwd, a) for a in AREAS},
        "trees": {prev: _tree_counts(prev, cwd), ref: _tree_counts(ref, cwd)},
    }


def _gh_json(args):
    result = subprocess.run(
        ["gh", "api", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseStatsError(
            f"`gh api {' '.join(args)}` failed: {result.stderr.strip()}. "
            "Run `gh auth status`, or pass --no-github."
        )
    return json.loads(result.stdout)


def github_stats(repo, start, end):
    """Issue and repo counts from GitHub between the two ref dates."""
    if shutil.which("gh") is None:
        raise ReleaseStatsError(
            "gh (GitHub CLI) is not on PATH. Install it from https://cli.github.com "
            "and run `gh auth login`, or pass --no-github for git-only stats."
        )
    window = f"{start.date().isoformat()}..{end.date().isoformat()}"

    def count(query):
        data = _gh_json(["-X", "GET", "search/issues", "-f", f"q=repo:{repo} {query}"])
        return data["total_count"]

    info = _gh_json([f"repos/{repo}"])
    return {
        "closed": count(f"is:issue closed:{window}"),
        "opened": count(f"is:issue created:{window}"),
        "open_now": count("is:issue is:open"),
        "stars": info["stargazers_count"],
        "forks": info["forks_count"],
    }


def _fmt_diff(d):
    return f"{d['files']:,} files, +{d['insertions']:,} / -{d['deletions']:,}"


def render(stats, gh=None, repo=DEFAULT_REPO):
    prev, ref = stats["prev"], stats["ref"]
    lines = [
        f"# Release stats: {prev} -> {ref}",
        "",
        f"- Window: {stats['start'].date()} -> {stats['end'].date()} "
        f"({stats['days']} days)",
        f"- PRs merged: {stats['prs']}",
        "- By type: " + ", ".join(f"{k} {v}" for k, v in stats["types"].items()),
        f"- Contributors (git authors, bots excluded): {stats['contributors']}, "
        f"{stats['outside_contributors']} beyond the maintainers",
        "- Top authors: "
        + ", ".join(f"{name} ({n})" for name, n in stats["top_authors"]),
        "",
        "## Diff",
        "",
        f"- Total: {_fmt_diff(stats['diff_total'])}",
    ]
    lines += [f"- {a}: {_fmt_diff(d)}" for a, d in stats["diff_areas"].items()]
    before, after = stats["trees"][prev], stats["trees"][ref]
    lines += [
        "",
        "## Size",
        "",
        f"- Test files (tests/**/test_*.py): {before['test_files']} -> "
        f"{after['test_files']}",
        f"- Skills (hub/skills/*/SKILL.md): {before['skills']} -> {after['skills']}",
    ]
    if gh is not None:
        lines += [
            "",
            f"## GitHub ({repo})",
            "",
            f"- Issues closed in window: {gh['closed']}",
            f"- Issues opened in window: {gh['opened']}",
            f"- Open issues now: {gh['open_now']}",
            f"- Stars: {gh['stars']:,}, forks: {gh['forks']:,}",
        ]
    lines += [
        "",
        "Eval result: not computed here. Quote it from the release eval run.",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("prev", help="previous final tag, e.g. v0.25.4")
    parser.add_argument("ref", help="release tag or ref, e.g. v0.26.0")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--maintainers",
        default=DEFAULT_MAINTAINERS,
        help="comma-separated author names or GitHub logins",
    )
    parser.add_argument("--no-github", action="store_true", help="git-only stats")
    args = parser.parse_args(argv)

    try:
        stats = collect(args.prev, args.ref, args.maintainers.split(","))
        gh = (
            None
            if args.no_github
            else github_stats(args.repo, stats["start"], stats["end"])
        )
    except ReleaseStatsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(render(stats, gh, args.repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
