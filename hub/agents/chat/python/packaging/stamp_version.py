#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Single-source version stamping for the ``gaia-agent-chat`` package.

Unlike the email agent's ``stamp_version.py`` (``hub/agents/email/python/
packaging/stamp_version.py``), chat has no ``version.py`` / no npm side / no
``binaries.lock.json`` / no version-pinned doc links -- it is a single Python
wheel with exactly two version references that must agree:

  * ``pyproject.toml``  -- the ``[project]`` ``version = "<v>"`` (the real
    source of truth: this is what ``python -m build`` stamps onto the wheel
    filename and its dist-info).
  * ``gaia-agent.yaml``  -- the hub catalog manifest's top-level
    ``version: <v>`` (what the Agent Hub Worker records at publish time).

Usage::

  python hub/agents/chat/python/packaging/stamp_version.py
      # read the version from pyproject.toml and stamp gaia-agent.yaml to match

  python hub/agents/chat/python/packaging/stamp_version.py --check
      # verify gaia-agent.yaml matches pyproject.toml; exit non-zero (with
      # both values printed) on any mismatch -- the CI / publish-time gate.

Deliberately hardcoded to THIS package's two files (not a generic multi-agent
tool) -- if a future agent needs the same two-rule check, copy this file into
its own ``packaging/`` directory rather than parameterizing this one, per the
email script's own precedent of being intentionally single-package.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# packaging/ -> python/ : this package's root.
CHAT_ROOT = Path(__file__).resolve().parent.parent

PYPROJECT = CHAT_ROOT / "pyproject.toml"
MANIFEST = CHAT_ROOT / "gaia-agent.yaml"

_PYPROJECT_VERSION_RE = re.compile(r'(?m)^(version\s*=\s*")([^"]+)(")')
_MANIFEST_VERSION_RE = re.compile(r"(?m)^(version:[ \t]*)(\S+)([ \t]*)$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


def _read_pyproject_version() -> str:
    if not PYPROJECT.exists():
        sys.exit(f"ERROR: source of truth not found: {PYPROJECT}")
    text = PYPROJECT.read_text(encoding="utf-8")
    m = _PYPROJECT_VERSION_RE.search(text)
    if not m:
        sys.exit(f"ERROR: could not parse [project] version from {PYPROJECT}")
    version = m.group(2)
    if not _SEMVER_RE.match(version):
        sys.exit(f"ERROR: pyproject version '{version}' is not a valid x.y.z version")
    return version


def _read_manifest_version() -> str | None:
    if not MANIFEST.exists():
        return None
    m = _MANIFEST_VERSION_RE.search(MANIFEST.read_text(encoding="utf-8"))
    return m.group(2) if m else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stamp/verify gaia-agent.yaml's version against pyproject.toml."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify gaia-agent.yaml matches pyproject.toml; exit non-zero on "
        "mismatch (CI / publish gate). Does not modify files.",
    )
    args = parser.parse_args(argv)

    version = _read_pyproject_version()
    manifest_version = _read_manifest_version()
    print(f"pyproject.toml version (source of truth): {version}")

    if args.check:
        if manifest_version is None:
            print(f"  FAIL  gaia-agent.yaml: version field not found in {MANIFEST}")
            return 1
        if manifest_version != version:
            print(
                f"  FAIL  gaia-agent.yaml: version = {manifest_version} -- "
                f"expected {version}"
            )
            print(
                "\nVersion drift detected. Run "
                "`python hub/agents/chat/python/packaging/stamp_version.py` "
                "to sync gaia-agent.yaml to pyproject.toml."
            )
            return 1
        print(f"  OK    gaia-agent.yaml (already {version})")
        print(f"\nAll present targets match pyproject.toml ({version}).")
        return 0

    if manifest_version == version:
        print(f"  OK    gaia-agent.yaml (already {version})")
        print(f"\nDone. 0 file(s) stamped -- gaia-agent.yaml already {version}.")
        return 0

    text = MANIFEST.read_text(encoding="utf-8")
    new_text, count = _MANIFEST_VERSION_RE.subn(
        lambda m: f"{m.group(1)}{version}{m.group(3)}", text
    )
    if count == 0:
        sys.exit(f"ERROR: could not find a 'version:' field to stamp in {MANIFEST}")
    MANIFEST.write_text(new_text, encoding="utf-8")
    old = manifest_version if manifest_version is not None else "(absent)"
    print(f"  STAMP gaia-agent.yaml: {old} -> {version}")
    print(f"\nDone. 1 file(s) stamped to v{version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
