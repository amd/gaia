#!/usr/bin/env python3
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Classify ``__version__`` on main against the last final release tag.

The nightly patch-release check (``.github/workflows/nightly-patch-release.yml``)
only drafts a patch on top of a main that still reads the last release. It
prints one state:

- ``steady``: main reads the last release (``0.25.4`` after ``v0.25.4``).
- ``staged-not-tagged``: main reads the next patch, a release merged but untagged.
- ``next-line``: main reads a later minor or major (``0.26.0`` after ``v0.25.4``)
  and that version has RC tags, so its release is under way and main is not
  patch-shaped.
- ``mismatch``: anything else; a human must look.

Usage, after a checkout with tags::

    python3 util/release_version_state.py v0.25.4 0.26.0
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Iterable

_FINAL = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse(version: str) -> tuple[int, int, int] | None:
    match = _FINAL.match(version)
    return tuple(int(part) for part in match.groups()) if match else None


def classify(prev_tag: str, main_version: str, tags: Iterable[str]) -> str:
    """The state of *main_version* given the last final tag and all tag names."""
    prev = _parse(prev_tag.removeprefix("v"))
    if prev is None:
        raise ValueError(f"last release tag {prev_tag!r} is not vX.Y.Z")
    main = _parse(main_version)
    if main is None:
        return "mismatch"
    if main == prev:
        return "steady"
    if main == (prev[0], prev[1], prev[2] + 1):
        return "staged-not-tagged"
    next_line = main in ((prev[0], prev[1] + 1, 0), (prev[0] + 1, 0, 0))
    rc = re.compile(rf"^v{re.escape(main_version)}-rc\d+$")
    if next_line and any(rc.match(tag) for tag in tags):
        return "next-line"
    return "mismatch"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: release_version_state.py <last-release-tag> <main-version>",
            file=sys.stderr,
        )
        return 2
    tags = subprocess.run(
        ["git", "tag", "--list", "v*"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    print(classify(argv[0], argv[1], tags))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
