# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Report verifiable source identity without trusting a caller's working directory."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from gaia.version import __version__


def get_build_identity() -> dict:
    identity = {
        "component": "gaia-core",
        "version": __version__,
        "source_commit": None,
        "dirty": None,
        "provenance": "unknown",
    }
    if getattr(sys, "frozen", False):
        return identity
    source = Path(__file__).resolve().parents[3]
    # Installed wheels must not accidentally adopt an unrelated enclosing repo.
    if (
        not (source / "src" / "gaia" / "engineering" / "identity.py").is_file()
        or not (source / ".git").exists()
    ):
        return identity

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()

    try:
        identity.update(
            source_commit=git("rev-parse", "HEAD"),
            dirty=bool(git("status", "--porcelain")),
            provenance="source_checkout",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        identity["reason"] = f"Source identity unavailable: {type(exc).__name__}"
    return identity
