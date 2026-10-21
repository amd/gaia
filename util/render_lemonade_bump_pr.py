# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Write the Lemonade bump PR's body from a fixed template.

The bump agent's release-notes summary is untrusted text, so it goes inside a
code fence: GitHub acts on no closing keyword, @mention or HTML comment there.
The fence is longer than any backtick run in the summary, so the summary cannot
close it, and the summary is cut to MAX_SUMMARY characters. A
``Proposed follow-ups: #12, #13`` line is the one thing linked outside it.

    python -I util/render_lemonade_bump_pr.py --summary pr-body.md \\
        --current 2026.42.1 --target 2026.43.1 --out body.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MAX_SUMMARY = 4000
VERSION = re.compile(r"\d+\.\d+\.\d+")
RELEASE = "https://github.com/lemonade-sdk/lemonade/releases/tag/v{version}"
# The only part of the summary shown outside the fence: up to two issue numbers.
FOLLOW_UPS = re.compile(
    r"^Proposed follow-ups: (#\d{1,7}(?:, #\d{1,7})?)[ \t]*$", re.MULTILINE
)


def render(summary: str, current: str, target: str) -> str:
    for version in (current, target):
        if not VERSION.fullmatch(version):
            raise ValueError(f"{version!r} is not a version")
    summary = summary.replace("\r\n", "\n").strip()
    if len(summary) > MAX_SUMMARY:
        summary = summary[:MAX_SUMMARY] + f"\n[cut at {MAX_SUMMARY} characters]"
    longest = max((len(run) for run in re.findall(r"`+", summary)), default=0)
    fence = "`" * max(3, longest + 1)
    follow_ups = FOLLOW_UPS.search(summary)
    linked = f"Proposed follow-ups: {follow_ups.group(1)}\n\n" if follow_ups else ""
    return (
        f"Bumps the pinned Lemonade Server from v{current} to v{target}. GAIA's "
        "installer and CI derive the Lemonade download URLs from `LEMONADE_VERSION` "
        "in `src/gaia/version.py`, so `gaia init` and the desktop installer pick up "
        "the new release.\n\n"
        f"Release notes: {RELEASE.format(version=target)}\n\n"
        "The bump agent's summary of those notes, unreviewed:\n\n"
        f"{fence}text\n{summary}\n{fence}\n\n"
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = args.summary.read_text(encoding="utf-8", errors="replace")
    args.out.write_text(render(summary, args.current, args.target), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
