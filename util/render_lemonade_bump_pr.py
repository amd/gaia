# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Write the Lemonade bump's PR body and feature proposals from fixed templates.

The bump agent's text is untrusted, so it goes inside a code fence: GitHub acts
on no closing keyword, @mention or HTML comment there. The fence is longer than
any backtick run in the text, so the text cannot close it, and the text is cut
to MAX_TEXT characters. Follow-up issue numbers come from the job that filed
them, never from the agent's text.

    python -I util/render_lemonade_bump_pr.py pr --summary pr-body.md \\
        --current 2026.42.1 --target 2026.43.1 --follow-ups "12 13" --out body.md
    python -I util/render_lemonade_bump_pr.py proposal --proposal 1.md \\
        --target 2026.43.1 --title-out title.txt --body-out body.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MAX_TEXT = 4000
MAX_TITLE = 100
MAX_FOLLOW_UPS = 2
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
ISSUE_NUMBER = re.compile(r"[1-9][0-9]{0,6}")
RELEASE = "https://github.com/lemonade-sdk/lemonade/releases/tag/v{version}"


def _check_versions(*versions: str) -> None:
    for version in versions:
        if not VERSION.fullmatch(version):
            raise ValueError(f"{version!r} is not a version")


def _fenced(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n[cut at {MAX_TEXT} characters]"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}\n"


def _follow_ups(numbers: str) -> list:
    found = numbers.split()
    if len(found) > MAX_FOLLOW_UPS or not all(ISSUE_NUMBER.fullmatch(n) for n in found):
        raise ValueError(f"follow-ups must be up to two issue numbers, not {numbers!r}")
    return found


def render(summary: str, current: str, target: str, follow_ups: str = "") -> str:
    _check_versions(current, target)
    numbers = _follow_ups(follow_ups)
    linked = (
        "Proposed follow-ups: " + ", ".join(f"#{n}" for n in numbers) + "\n\n"
        if numbers
        else ""
    )
    return (
        f"Bumps the pinned Lemonade Server from v{current} to v{target}. GAIA's "
        "installer and CI derive the Lemonade download URLs from `LEMONADE_VERSION` "
        "in `src/gaia/version.py`, so `gaia init` and the desktop installer pick up "
        "the new release.\n\n"
        f"Release notes: {RELEASE.format(version=target)}\n\n"
        "The bump agent's summary of those notes, unreviewed:\n\n"
        f"{_fenced(summary)}\n"
        f"{linked}"
        "## Test plan\n"
        "- [x] The bump's changes passed main's checks for what a bump may change, "
        "main's pin tests against the live release, and the docs-version check, "
        "before this PR was opened\n"
        "- [ ] CI green (lint + unit tests). It does not start on its own for a "
        "bot-opened PR; close and reopen the PR, or push to it, to run it\n"
        f"- [ ] `gaia init` downloads Lemonade v{target} successfully\n"
        "- [ ] Review the release notes for breaking changes (default port, min ctx, "
        "API) that need follow-up beyond this bump\n"
    )


def render_proposal(text: str, target: str) -> tuple:
    """A feature proposal's issue title and body. Its first line is the title."""
    _check_versions(target)
    first, _, rest = text.replace("\r\n", "\n").strip().partition("\n")
    title = re.sub(r"[^A-Za-z0-9 ,.:;()'/+-]", "", first.lstrip("# "))
    title = " ".join(title.split())[:MAX_TITLE]
    if not title:
        raise ValueError("a proposal's first line must be its title")
    body = (
        f"The bump agent's proposal, from the Lemonade v{target} release notes, "
        f"unreviewed:\n\n{_fenced(rest)}\n"
        f"_Auto-proposed by the Lemonade release scan (v{target}); maintainer "
        "triage decides scope. cc @kovtcharov-amd_\n"
    )
    return f"feat: {title} (unlocked by Lemonade v{target})", body


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="kind", required=True)
    pr = sub.add_parser("pr")
    pr.add_argument("--summary", type=Path, required=True)
    pr.add_argument("--current", required=True)
    pr.add_argument("--target", required=True)
    pr.add_argument("--follow-ups", default="")
    pr.add_argument("--out", type=Path, required=True)
    proposal = sub.add_parser("proposal")
    proposal.add_argument("--proposal", type=Path, required=True)
    proposal.add_argument("--target", required=True)
    proposal.add_argument("--title-out", type=Path, required=True)
    proposal.add_argument("--body-out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.kind == "pr":
        summary = args.summary.read_text(encoding="utf-8", errors="replace")
        body = render(summary, args.current, args.target, args.follow_ups)
        args.out.write_text(body, encoding="utf-8")
    else:
        text = args.proposal.read_text(encoding="utf-8", errors="replace")
        title, body = render_proposal(text, args.target)
        args.title_out.write_text(title, encoding="utf-8")
        args.body_out.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
