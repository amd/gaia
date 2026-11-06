# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A one-file agent binary keeps its unpacked files safe from OS temp cleaners."""

import os
import sys
import time
from pathlib import Path

from gaia.sidecar.bundle_freshness import keep_unpacked_bundle_fresh

# macOS deletes files in its per-user temp folder that are older than this.
MACOS_TEMP_CLEANER_DAYS = 3
FIVE_DAYS = 5 * 24 * 3600


def _stale_bundle(root: Path) -> list[Path]:
    (root / "gaia_agent" / "skills" / "summarize").mkdir(parents=True)
    paths = [
        root / "gaia_agent" / "gaia-agent.yaml",
        root / "gaia_agent" / "skills" / "summarize" / "SKILL.md",
        root / "lib.so",
    ]
    for path in paths:
        path.write_text("x", encoding="utf-8")
    old = time.time() - FIVE_DAYS
    for path in [*paths, *(p for p in root.rglob("*") if p.is_dir()), root]:
        os.utime(path, (old, old))
    return paths


def _would_be_cleaned(path: Path) -> bool:
    cutoff = time.time() - MACOS_TEMP_CLEANER_DAYS * 24 * 3600
    st = path.stat()
    return max(st.st_atime, st.st_mtime) < cutoff


def _freeze(monkeypatch, bundle: Path, executable: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))


def test_a_one_file_binary_refreshes_its_unpacked_files(tmp_path, monkeypatch):
    bundle = tmp_path / "T" / "_MEI123"
    paths = _stale_bundle(bundle)
    _freeze(monkeypatch, bundle, tmp_path / "agents" / "gaia" / "gaia-agent")

    keeper = keep_unpacked_bundle_fresh()

    assert keeper is not None
    assert not [p for p in [*paths, bundle] if _would_be_cleaned(p)]


def test_source_runs_and_folder_builds_leave_files_alone(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert keep_unpacked_bundle_fresh() is None

    install = tmp_path / "agents" / "gaia"
    paths = _stale_bundle(install / "_internal")
    _freeze(monkeypatch, install / "_internal", install / "gaia-agent")

    assert keep_unpacked_bundle_fresh() is None
    assert all(_would_be_cleaned(p) for p in paths)
