# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Exercise the installed CLI from a clean profile, including explicit refusal."""

import os
import subprocess
import sys
from pathlib import Path

from gaia.engineering.store import JobStore


def cli(*args, text=""):
    return subprocess.run(
        [str(Path(sys.executable).parent / "gaia"), "engineering", *args],
        input=text,
        text=True,
        capture_output=True,
        timeout=25,
        env={**os.environ, "GAIA_DEVELOPER_MODE": "1"},
    )


def test_cli_requires_own_explicit_flag_even_with_mode_environment(tmp_path):
    result = cli("--root", str(tmp_path / "profile"), "connect", "codex")
    assert result.returncode != 0
    assert "developer mode" in result.stderr
    assert not (tmp_path / "profile").exists()


def test_cli_denial_then_snapshot_share_and_revoke(tmp_path):
    context = tmp_path / "selected.txt"
    context.write_text("violet-otter-92")
    root = tmp_path / "profile"
    args = (
        "--developer-mode",
        "--root",
        str(root),
        "share",
        "codex",
        "--summary",
        "Synthetic CLI test",
        "--context-file",
        str(context),
    )
    denied = cli(*args, text="NO\n")
    assert denied.returncode != 0
    assert list((root / "jobs").iterdir()) == []
    approved = cli(*args, text="SHARE\n")
    assert approved.returncode == 0, approved.stderr
    jobs = list((root / "jobs").iterdir())
    assert len(jobs) == 1
    store = JobStore(root)
    assert (
        store.context(jobs[0].name, "codex")["evidence"][0]["text"] == "violet-otter-92"
    )
    revoked = cli("--developer-mode", "--root", str(root), "revoke", jobs[0].name)
    assert revoked.returncode == 0
    assert store.read(jobs[0].name)["grant"]["revoked"]
