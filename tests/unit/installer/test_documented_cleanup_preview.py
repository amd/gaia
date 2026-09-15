# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The documented preview must show a real cleanup plan without deleting data."""

import shlex
from pathlib import Path

import pytest

from gaia.cli import main

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "doc",
    [
        "docs/guides/install.mdx",
        "docs/reference/cli.mdx",
        "docs/plans/desktop-installer.mdx",
    ],
)
def test_documented_preview_lists_targets_and_preserves_files(
    tmp_path, monkeypatch, capsys, doc
):
    guide = (ROOT / doc).read_text(encoding="utf-8")
    command = next(
        (
            line
            for line in guide.splitlines()
            if line.startswith("gaia uninstall") and "--dry-run" in line
        ),
        None,
    )
    assert command, f"{doc} must document a cleanup preview"
    args = shlex.split(command, comments=True)
    assert "--purge" in args
    target = tmp_path / ".gaia" / "documents" / "keep.txt"
    target.parent.mkdir(parents=True)
    target.write_text("keep this document", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.setattr("sys.argv", args)
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert str(target.parent) in output
    assert "Nothing to do" not in output
    assert target.read_text(encoding="utf-8") == "keep this document"
