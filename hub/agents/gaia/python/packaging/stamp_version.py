#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Check or stamp the flagship agent's version fields from core's version.

The flagship ships at ``__version__`` in ``src/gaia/version.py``.
``util/release_versions.py`` owns every field that follows it; this runs that
script's ``flagship`` group, so ``release_agent_gaia.yml`` keeps its old entry
point until Phase 7 of the release train (#5172) deletes it.

Usage::

  python hub/agents/gaia/python/packaging/stamp_version.py           # stamp
  python hub/agents/gaia/python/packaging/stamp_version.py --check   # verify only

``API_VERSION`` in ``gaia_agent/server.py`` is not a target: it is the wire
contract's version, independent of the package version.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "util"))

import release_versions  # noqa: E402  pylint: disable=wrong-import-position


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Stamp or verify the flagship agent's version fields."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify every flagship field; exit non-zero on drift. No writes.",
    )
    args = parser.parse_args(argv)
    mode = ["--check"] if args.check else ["--stamp"]
    return release_versions.main(
        [*mode, "--only", "flagship", "--root", str(REPO_ROOT)]
    )


if __name__ == "__main__":
    sys.exit(main())
