# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The documented preview must show a real cleanup plan without deleting data."""

import shlex
from pathlib import Path

from gaia.cli import main

ROOT = Path(__file__).resolve().parents[3]


def test_documented_preview_lists_targets_and_preserves_files(
    tmp_path, monkeypatch, capsys
):
    guide = (ROOT / "docs/guides/install.mdx").read_text(encoding="utf-8")
    command = next(
        line
        for line in guide.splitlines()
        if line.startswith("gaia uninstall") and "--dry-run" in line
    )
    args = shlex.split(command, comments=True)
    assert "--purge" in args
    target = tmp_path / ".gaia" / "documents" / "keep.txt"
    target.parent.mkdir(parents=True)
    target.write_text("keep this document", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.argv", args)
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "documents" in output
    assert "Nothing to do" not in output
    assert target.read_text(encoding="utf-8") == "keep this document"
