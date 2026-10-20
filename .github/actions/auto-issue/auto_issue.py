#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""File, update, or close the one tracking issue a workflow owns for an alert.

Alert workflows used to find "their" issue by fuzzy title search and never
closed it, so an alert outlived the condition that raised it (#3920, #4462)
and three mutually exclusive "Release check:" states sat open together. Every
issue this script files carries a hidden marker naming its key, and lookup
matches that marker exactly:

    <!-- gaia-auto-issue: nightly-patch-release/failure -->

``report`` comments on (or edits) the open issue carrying the key, else files
one. ``resolve`` closes every open issue carrying the key, with a comment
saying which run recovered it.

Only issues filed by the workflow token's bot are considered, so a human issue
that quotes a marker or a title is never commented on or closed.

``--legacy-title`` / ``--legacy-title-prefix`` let a workflow adopt the
unmarked issues it filed before markers existed. ``report`` rewrites an adopted
issue's body with the marker only in ``--update edit`` mode; in comment mode
the title keeps matching it.

Fails loudly: if the lookup itself fails, ``report`` still files the alert
(a duplicate beats a dropped alert) and then exits non-zero; ``resolve`` exits
non-zero without closing anything.

stdlib only; runs on a bare GitHub-hosted runner before any venv exists.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Sequence

#: The login `gh issue list --author` accepts for issues filed with GITHUB_TOKEN.
BOT_AUTHOR = "github-actions[bot]"

#: Upper bound on open bot issues fetched in one lookup. Reaching it means the
#: lookup may have missed the issue, which is reported as a failure.
LOOKUP_LIMIT = 1000

MARKER_RE = re.compile(r"<!--\s*gaia-auto-issue:\s*(\S+?)\s*-->")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$")

Runner = Callable[[Sequence[str]], str]


class LookupFailed(RuntimeError):
    """The open-issue lookup could not be completed."""


def marker(key: str) -> str:
    return f"<!-- gaia-auto-issue: {key} -->"


def validate_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise ValueError(
            f"invalid auto-issue key {key!r}: use letters, digits and ._/:- "
            "(no spaces), e.g. 'nightly-patch-release/failure'"
        )
    return key


VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?")


def parse_version(text: str) -> tuple[int, int, int, float] | None:
    """Last ``X.Y.Z[-rcN]`` in ``text``; a final release sorts after its RCs."""
    found = VERSION_RE.findall(text)
    if not found:
        return None
    major, minor, patch, rc = found[-1]
    return int(major), int(minor), int(patch), int(rc) if rc else float("inf")


def issue_keys(body: str | None) -> set[str]:
    return set(MARKER_RE.findall(body or ""))


def with_marker(body: str, key: str) -> str:
    """Return ``body`` carrying exactly one marker, for ``key``."""
    stripped = MARKER_RE.sub("", body).rstrip()
    return f"{stripped}\n\n{marker(key)}\n"


@dataclass
class Selector:
    """Which open issues belong to one alert."""

    keys: set[str] = field(default_factory=set)
    key_prefixes: list[str] = field(default_factory=list)
    legacy_titles: set[str] = field(default_factory=set)
    legacy_title_prefixes: list[str] = field(default_factory=list)
    except_keys: set[str] = field(default_factory=set)
    except_titles: set[str] = field(default_factory=set)
    #: When set, only issues for this version or an earlier one on the same
    #: major.minor line match; an unparseable version never matches.
    up_to_version: tuple[int, int, int, float] | None = None

    def matches(self, issue: dict) -> bool:
        keys = issue_keys(issue.get("body"))
        title = issue.get("title") or ""
        if keys & self.except_keys or title in self.except_titles:
            return False
        if self.up_to_version is not None:
            found = parse_version(" ".join(sorted(keys)) if keys else title)
            if (
                found is None
                or found[:2] != self.up_to_version[:2]
                or found > self.up_to_version
            ):
                return False
        if keys:
            # A marked issue is matched by its marker only, never by title.
            return bool(keys & self.keys) or any(
                k.startswith(p) for k in keys for p in self.key_prefixes
            )
        return title in self.legacy_titles or any(
            title.startswith(p) for p in self.legacy_title_prefixes
        )


def gh(argv: Sequence[str]) -> str:
    result = subprocess.run(
        ["gh", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, ["gh", *argv], result.stdout, result.stderr
        )
    return result.stdout


def list_open_bot_issues(repo: str, run: Runner) -> list[dict]:
    try:
        out = run(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--author",
                BOT_AUTHOR,
                "--limit",
                str(LOOKUP_LIMIT),
                "--json",
                "number,title,body",
            ]
        )
        issues = json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise LookupFailed(f"listing open issues in {repo} failed: {detail}") from e
    if not isinstance(issues, list):
        raise LookupFailed(f"unexpected `gh issue list` output: {out[:200]!r}")
    if len(issues) >= LOOKUP_LIMIT:
        raise LookupFailed(
            f"{len(issues)} open bot issues reached the lookup limit "
            f"({LOOKUP_LIMIT}); the matching issue may not be among them"
        )
    return issues


def report(
    *,
    repo: str,
    key: str,
    title: str,
    body: str,
    labels: Sequence[str],
    assignees: Sequence[str],
    update: str,
    selector: Selector,
    run: Runner,
    out: Callable[[str], None] = print,
) -> tuple[int, str]:
    """Comment on / edit the open issue for ``key``, else file one.

    Returns ``(exit_code, issue_ref)``.
    """
    selector.keys.add(key)
    selector.legacy_titles.add(title)
    exit_code = 0
    target = None
    try:
        matches = [i for i in list_open_bot_issues(repo, run) if selector.matches(i)]
        if matches:
            target = min(matches, key=lambda i: i["number"])
    except LookupFailed as e:
        out(f"::error::{e}. Filing a new issue so the alert is not lost.")
        exit_code = 1

    if target is not None:
        number = str(target["number"])
        if update == "edit":
            run(
                [
                    "issue",
                    "edit",
                    number,
                    "--repo",
                    repo,
                    "--title",
                    title,
                    "--body",
                    with_marker(body, key),
                ]
            )
            out(f"Updated #{number} in place ({key}).")
        else:
            run(["issue", "comment", number, "--repo", repo, "--body", body])
            out(f"Commented on #{number} ({key}).")
        return exit_code, number

    argv = ["issue", "create", "--repo", repo, "--title", title]
    argv += ["--body", with_marker(body, key)]
    for label in labels:
        argv += ["--label", label]
    for assignee in assignees:
        argv += ["--assignee", assignee]
    url = run(argv).strip()
    out(f"Filed {url} ({key}).")
    return exit_code, url


def resolve(
    *,
    repo: str,
    selector: Selector,
    note: str,
    run: Runner,
    out: Callable[[str], None] = print,
) -> list[int]:
    """Close every open issue the selector matches. Raises LookupFailed."""
    targets = sorted(
        i["number"] for i in list_open_bot_issues(repo, run) if selector.matches(i)
    )
    for number in targets:
        run(
            [
                "issue",
                "close",
                str(number),
                "--repo",
                repo,
                "--reason",
                "completed",
                "--comment",
                note,
            ]
        )
        out(f"Closed #{number}.")
    if not targets:
        out("No open issue to close.")
    return targets


def _lines(values: Sequence[str] | None) -> list[str]:
    """Flatten repeated and newline-separated values, dropping blanks."""
    flat: list[str] = []
    for value in values or []:
        flat += [v.strip() for v in value.splitlines() if v.strip()]
    return flat


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", required=True)
    common.add_argument("--legacy-title", action="append")
    common.add_argument("--legacy-title-prefix", action="append")

    r = sub.add_parser("report", parents=[common])
    r.add_argument("--key", required=True)
    r.add_argument("--title", required=True)
    r.add_argument("--body-file", required=True)
    r.add_argument("--label", action="append")
    r.add_argument("--assignee", action="append")
    r.add_argument("--update", choices=("comment", "edit"), default="comment")

    s = sub.add_parser("resolve", parents=[common])
    s.add_argument("--key", action="append")
    s.add_argument("--key-prefix", action="append")
    s.add_argument("--except-key", action="append")
    s.add_argument("--except-title", action="append")
    s.add_argument("--up-to-version")
    s.add_argument("--note", required=True)
    return p


def main(argv: Sequence[str] | None = None, run: Runner = gh) -> int:
    try:
        return _main(argv, run)
    except subprocess.CalledProcessError as e:
        # Never echo argv: it carries the whole issue body.
        verb = " ".join(str(a) for a in list(e.cmd)[1:3])
        detail = (e.stderr or e.stdout or "").strip() or "no output"
        print(f"::error::gh {verb} failed (exit {e.returncode}): {detail}")
        return 1


def _main(argv: Sequence[str] | None, run: Runner) -> int:
    args = build_parser().parse_args(argv)
    selector = Selector(
        legacy_titles=set(_lines(args.legacy_title)),
        legacy_title_prefixes=_lines(args.legacy_title_prefix),
    )
    if args.mode == "report":
        title = args.title.strip()
        if not title:
            raise SystemExit("report needs a non-empty --title")
        with open(args.body_file, encoding="utf-8") as f:
            body = f.read()
        code, _ = report(
            repo=args.repo,
            key=validate_key(args.key.strip()),
            title=title,
            body=body,
            labels=_lines(args.label),
            assignees=_lines(args.assignee),
            update=args.update,
            selector=selector,
            run=run,
        )
        return code

    selector.keys = {validate_key(k) for k in _lines(args.key)}
    selector.key_prefixes = [validate_key(k) for k in _lines(args.key_prefix)]
    selector.except_keys = set(_lines(args.except_key))
    selector.except_titles = set(_lines(args.except_title))
    if args.up_to_version:
        selector.up_to_version = parse_version(args.up_to_version)
        if selector.up_to_version is None:
            raise SystemExit(f"--up-to-version {args.up_to_version!r} has no X.Y.Z")
    if not (
        selector.keys
        or selector.key_prefixes
        or selector.legacy_titles
        or selector.legacy_title_prefixes
    ):
        raise SystemExit("resolve needs --key, --key-prefix or a legacy title to match")
    try:
        resolve(repo=args.repo, selector=selector, note=args.note, run=run)
    except LookupFailed as e:
        print(f"::error::{e}. Nothing was closed; close the recovered issue by hand.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
