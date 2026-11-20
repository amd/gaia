#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stats for one release window, for the summary that opens its release notes.

Usage:
    python util/release_stats.py <prev_tag> <release_ref> [--repo amd/gaia]
        [--maintainers kovtcharov,kovtcharov-amd,"Kalin Ovtcharov"]
        [--no-github] [--bullet]

The first line printed is the single stats bullet for the release notes'
summary section; the rest is detail for picking the highest-impact changes.
``--bullet`` prints only that line.

Contributors are counted per person, not per git identity: the repo's
.mailmap is honoured, identities that share a name or an email are one
person, and every --maintainers alias folds into one entry. It never prints
eval results; those stay out of the public notes.
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
GIT_TIMEOUT_S = 120
GH_TIMEOUT_S = 60

_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
_TYPE_RE = re.compile(r"^([a-z]+)(\([^)]*\))?!?:")


class ReleaseStatsError(RuntimeError):
    """A ref, git, or gh problem the caller must fix before stats can be read."""


class _GitExitError(ReleaseStatsError):
    """git ran and exited non-zero."""


def _git(args, cwd=None):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=GIT_TIMEOUT_S,
        )
    except FileNotFoundError as e:
        raise ReleaseStatsError("git is not on PATH; install git and retry.") from e
    except subprocess.TimeoutExpired as e:
        raise ReleaseStatsError(
            f"`git {' '.join(args)}` did not finish in {GIT_TIMEOUT_S}s; "
            "check that no other git process holds the repository lock."
        ) from e
    if result.returncode != 0:
        raise _GitExitError(f"`git {' '.join(args)}` failed: {result.stderr.strip()}")
    return result.stdout


def _require_ref(ref, cwd=None):
    try:
        _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd)
    except _GitExitError as e:
        raise ReleaseStatsError(
            f"ref '{ref}' does not exist in this repository. "
            "Run `git fetch origin --tags` and check the tag name with "
            "`git tag -l 'v*'`."
        ) from e


def _ref_date(ref, cwd=None):
    return datetime.fromisoformat(_git(["log", "-1", "--format=%cI", ref], cwd).strip())


def _is_maintainer(name, email, maintainers):
    local = email.split("@", 1)[0].split("+", 1)[-1].lower()
    return name.lower() in maintainers or local in maintainers


def _people(identities, maintainers):
    """Map each (name, email) identity to a root identity, one root per person.

    Identities are one person when they share a name or an email (case
    insensitive), or when both match a maintainer alias.
    """
    parent = {i: i for i in identities}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(a)] = find(b)

    seen = {}
    maintainer = None
    for ident in identities:
        name, email = ident
        for key in (("name", name.strip().lower()), ("email", email.strip().lower())):
            if not key[1]:
                continue
            if key in seen:
                union(ident, seen[key])
            else:
                seen[key] = ident
        if _is_maintainer(name, email, maintainers):
            if maintainer is None:
                maintainer = ident
            else:
                union(ident, maintainer)
    return {i: find(i) for i in identities}


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
    # %aN/%aE apply the repo's .mailmap, so a mapped alias arrives merged.
    log = _git(["log", "--format=%aN%x1f%aE%x1f%s", f"{prev}..{ref}"], cwd)

    prs = {}
    commits = Counter()
    for line in log.splitlines():
        if not line.strip():
            continue
        name, email, subject = line.split("\x1f", 2)
        m = _PR_RE.search(subject)
        if m:
            prs[int(m.group(1))] = subject
        if "[bot]" in name or "[bot]" in email:
            continue
        commits[(name, email)] += 1

    root_of = _people(list(commits), maintainers)
    per_person = Counter()
    names = {}
    maintainer_roots = set()
    for ident, n in commits.items():
        root = root_of[ident]
        per_person[root] += n
        names.setdefault(root, Counter())[ident[0]] += n
        if _is_maintainer(*ident, maintainers):
            maintainer_roots.add(root)
    top = [
        (names[root].most_common(1)[0][0], n)
        for root, n in per_person.most_common(TOP_AUTHORS)
    ]

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
        "contributors": len(per_person),
        "outside_contributors": len(set(per_person) - maintainer_roots),
        "top_authors": top,
        "diff_total": _shortstat(prev, ref, cwd),
        "diff_areas": {a: _shortstat(prev, ref, cwd, a) for a in AREAS},
        "trees": {prev: _tree_counts(prev, cwd), ref: _tree_counts(ref, cwd)},
    }


def _gh_json(args):
    try:
        result = subprocess.run(
            ["gh", "api", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=GH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise ReleaseStatsError(
            f"`gh api {' '.join(args)}` did not answer in {GH_TIMEOUT_S}s. "
            "Check your network and `gh auth status`, or pass --no-github."
        ) from e
    if result.returncode != 0:
        raise ReleaseStatsError(
            f"`gh api {' '.join(args)}` failed: {result.stderr.strip()}. "
            "Run `gh auth status`, or pass --no-github."
        )
    return json.loads(result.stdout)


def github_stats(repo, start, end):
    """Issues opened and closed on GitHub between the two ref dates."""
    if shutil.which("gh") is None:
        raise ReleaseStatsError(
            "gh (GitHub CLI) is not on PATH. Install it from https://cli.github.com "
            "and run `gh auth login`, or pass --no-github for git-only stats."
        )
    window = f"{start.date().isoformat()}..{end.date().isoformat()}"

    def count(query):
        data = _gh_json(["-X", "GET", "search/issues", "-f", f"q=repo:{repo} {query}"])
        return data["total_count"]

    return {
        "closed": count(f"is:issue closed:{window}"),
        "opened": count(f"is:issue created:{window}"),
    }


def _fmt_diff(d):
    return f"{d['files']:,} files, +{d['insertions']:,} / -{d['deletions']:,}"


def stats_bullet(stats, gh=None):
    """The one stats bullet that goes in the release notes' summary section."""
    issues = f" and closed {gh['closed']:,} issues" if gh is not None else ""
    return (
        f"- {stats['contributors']:,} contributors merged {stats['prs']:,} PRs"
        f"{issues} since {stats['prev']}."
    )


def render(stats, gh=None, repo=DEFAULT_REPO):
    prev, ref = stats["prev"], stats["ref"]
    lines = [
        stats_bullet(stats, gh),
        "",
        f"# Release stats: {prev} -> {ref}",
        "",
        f"- Window: {stats['start'].date()} -> {stats['end'].date()} "
        f"({stats['days']} days)",
        f"- PRs merged: {stats['prs']}",
        "- By type: " + ", ".join(f"{k} {v}" for k, v in stats["types"].items()),
        f"- Contributors (people, bots excluded): {stats['contributors']}, "
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
        ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("prev", help="previous release tag, e.g. v0.25.4")
    parser.add_argument("ref", help="release tag or ref, e.g. v0.26.0 or HEAD")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--maintainers",
        default=DEFAULT_MAINTAINERS,
        help="comma-separated names, email local parts or GitHub logins of the "
        "maintainer; every identity that matches counts as one contributor",
    )
    parser.add_argument("--no-github", action="store_true", help="git-only stats")
    parser.add_argument(
        "--bullet", action="store_true", help="print only the release-notes bullet"
    )
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
    print(stats_bullet(stats, gh) if args.bullet else render(stats, gh, args.repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
