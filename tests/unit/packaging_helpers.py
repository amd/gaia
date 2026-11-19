# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared walker for tests that compare on-disk packages to ``setup.py``."""

from __future__ import annotations

import os
from pathlib import Path

# Build output, caches, and vendored trees that can hold stray ``__init__.py``
# files (e.g. npm's node-gyp under apps/webui/node_modules) but never ship.
NON_SOURCE_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".venv",
        "venv",
        ".git",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
    }
)


def find_source_packages(src_dir: Path, excluded: frozenset[str] = frozenset()):
    """Dotted names of every package under ``src_dir``, skipping non-source trees.

    Prunes during the walk, so a populated ``node_modules`` is never descended.
    """
    skip = NON_SOURCE_DIRS | excluded
    packages = set()
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        if "__init__.py" in files:
            packages.add(".".join(Path(root).relative_to(src_dir).parts))
    return packages
