#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check a published final's notes against its tag. Reads only; never writes.

The curated notes are written in the release PR before the tag, so the Full
Changelog cannot list commits that land after it: the notes commit itself and
anything merged before the tag. v0.25.4 shipped claiming 481 commits when
``git log v0.25.3..v0.25.4`` has 483. This names that gap so the release body is
corrected by hand. It replaces a job that regenerated the notes, bumped
``version.py`` and pushed to main after the release, which would have reverted
main's version and overwritten the curated notes.

Usage::

    gh release view v0.25.4 --json body --jq .body > body.md
    python util/check_published_release_notes.py --tag v0.25.4 --body-file body.md
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_tag import FINAL_TAG_RE  # noqa: E402

CLAIMED_RE = re.compile(r"\*\*(\d+) commits\*\*")
LISTED_RE = re.compile(r"^- `[0-9a-f]{7,40}`")


def _git(cwd: Path | None, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )


def previous_final(tag: str, cwd: Path | None = None) -> str:
    """The last final before ``tag``; RCs and agent-pkg-* tags would shrink the range."""
    out = _git(
        cwd,
        "describe",
        "--tags",
        "--abbrev=0",
        "--match",
        "v[0-9]*",
        "--exclude",
        "v*-rc*",
        f"{tag}^",
    )
    if out.returncode != 0:
        raise RuntimeError(
            f"no final release tag before {tag}: {out.stderr.strip()} "
            "(fetch tags with `git fetch --tags`)"
        )
    return out.stdout.strip()


def commit_count(previous: str, tag: str, cwd: Path | None = None) -> int:
    out = _git(cwd, "rev-list", "--count", f"{previous}..{tag}")
    if out.returncode != 0:
        raise RuntimeError(f"git rev-list {previous}..{tag} failed: {out.stderr}")
    return int(out.stdout.strip())


def notes_at_tag(tag: str, cwd: Path | None = None) -> bool:
    return _git(cwd, "cat-file", "-e", f"{tag}:docs/releases/{tag}.mdx").returncode == 0


def changelog_counts(body: str) -> tuple[int | None, int]:
    """The ``**N commits**`` claim and the number of ``- `sha` `` lines under Full Changelog."""
    section: list[str] = []
    inside = False
    for line in body.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == "## Full Changelog"
            continue
        if inside:
            section.append(line)
    claimed = next(
        (CLAIMED_RE.search(ln) for ln in section if CLAIMED_RE.search(ln)), None
    )
    listed = sum(1 for ln in section if LISTED_RE.match(ln))
    return (int(claimed[1]) if claimed else None), listed


def check(
    tag: str, previous: str, count: int, body: str, notes_present: bool
) -> list[str]:
    errors = []
    if not notes_present:
        errors.append(
            f"docs/releases/{tag}.mdx is not in the tagged tree; the release PR "
            "must add the notes before the tag."
        )
    claimed, listed = changelog_counts(body)
    real = f"`git log {previous}..{tag}` has {count}"
    if claimed is None:
        errors.append(
            f"the published body has no `**N commits**` line under ## Full Changelog; {real}."
        )
    elif claimed != count:
        errors.append(f"the published body claims {claimed} commits; {real}.")
    if listed and listed != count:
        errors.append(f"the published body lists {listed} commits; {real}.")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True, help="the final, e.g. v0.25.4")
    parser.add_argument(
        "--body-file", required=True, type=Path, help="the published release body"
    )
    args = parser.parse_args(argv)

    if not FINAL_TAG_RE.match(args.tag):
        print(f"::error::{args.tag} is not a final release tag (vX.Y.Z)")
        return 1
    previous = previous_final(args.tag)
    count = commit_count(previous, args.tag)
    errors = check(
        args.tag,
        previous,
        count,
        args.body_file.read_text(encoding="utf-8"),
        notes_at_tag(args.tag),
    )
    for error in errors:
        print(f"::error title=Release notes {args.tag}::{error}")
    if errors:
        return 1
    print(
        f"{args.tag}: notes present, Full Changelog matches {count} commits since {previous}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
