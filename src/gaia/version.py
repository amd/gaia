# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import logging
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_package_version_metadata
from pathlib import Path
from typing import Dict, Optional, Tuple

__version__ = "0.25.4"

# Lemonade version used across CI and installer
LEMONADE_VERSION = "2026.44.1"

# Oldest Lemonade Server GAIA runs against. Anything older is refused, and
# GAIA's own embedded server is upgraded on start. Raised by hand, never by a
# LEMONADE_VERSION bump.
LEMONADE_MIN_VERSION = "2026.44.1"


def parse_version(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Parse a dotted version into a comparable int tuple, or None.

    Keeps each component's leading digits so Lemonade's CalVer development
    builds (``2026.39.0~12.abc1234``) compare instead of reading as unknown.

    The frozen sidecars vendor their own copy — they cannot import this — and
    ``tests/unit/test_lemonade_calver.py`` holds them to it.
    """
    if not version:
        return None
    try:
        parts = str(version).strip().lstrip("v").split(".")[:3]
        return tuple(int(re.match(r"\s*(\d+)", p).group(1)) for p in parts)
    except (ValueError, IndexError, AttributeError):
        return None


def get_package_version() -> str:
    """Return the installed ``amd-gaia`` version, else this tree's ``__version__``.

    A source checkout or frozen binary has no package metadata; ``setup.py``
    builds the wheel version from ``__version__``, so it is the same answer.
    """
    try:
        return get_package_version_metadata("amd-gaia")
    except PackageNotFoundError:
        # stdlib logger on purpose: importing gaia.logger here installs handlers.
        logging.getLogger(__name__).debug(
            "amd-gaia package metadata not found; using source version %s",
            __version__,
        )
        return __version__


def get_git_hash(hash_length: int = 8, cwd: Optional[str] = None) -> str:
    """Get the current git hash.

    Args:
        hash_length: Length of the hash to return
        cwd: The checkout to ask; the current directory when None

    Returns:
        str: The git hash, or "" when this is not a git checkout or git is missing
    """
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            cwd=cwd,
        ).strip()
        return git_hash[:hash_length]
    except (OSError, subprocess.SubprocessError) as e:
        logging.warning("Failed to get Git hash: %s", e)
        return ""


def build_info() -> Dict[str, Optional[str]]:
    """Which GAIA is running: a published ``release`` or ``release_candidate``,
    a ``local`` build installed from a wheel file, or ``source`` run from a
    checkout (with its commit). Read from what pip recorded at install time."""
    import json
    from importlib.metadata import distribution

    current = get_package_version()
    rc = re.search(r"rc(\d+)$", current)
    kind = "release_candidate" if rc else "release"
    try:
        recorded = distribution("amd-gaia").read_text("direct_url.json")
    except PackageNotFoundError:
        # No package metadata at all: running straight from a checkout.
        recorded, kind = None, "source"
    if recorded:
        origin = json.loads(recorded)
        if (origin.get("dir_info") or {}).get("editable"):
            kind = "source"
        elif str(origin.get("url", "")).startswith("file:"):
            kind = "local"
    # Code outside any site-packages runs from a checkout, whatever metadata says;
    # a frozen binary is a published build.
    here = {part.lower() for part in Path(__file__).resolve().parts}
    if not getattr(sys, "frozen", False) and not here & {
        "site-packages",
        "dist-packages",
    }:
        kind = "source"
    commit = None
    if kind == "source":
        commit = get_git_hash(cwd=str(Path(__file__).resolve().parent)) or None
    return {
        "version": current,
        "kind": kind,
        "rc": rc.group(1) if rc else None,
        "commit": commit,
    }


# For regular package usage - just version number
version = get_package_version()
