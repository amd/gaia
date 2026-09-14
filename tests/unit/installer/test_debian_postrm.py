# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Exercise Debian removal without touching system or personal state."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Debian maintainer script requires a POSIX shell"
)

POSTRM = Path(__file__).resolve().parents[3] / "installer/debian/postrm"


@pytest.mark.parametrize("action", ["purge", "remove", "upgrade"])
@pytest.mark.parametrize("sudo_user", ["", "desktop-user"])
def test_removal_preserves_user_data(tmp_path, action, sudo_user):
    home = tmp_path / "home"
    data = home / ".gaia" / "chat" / "conversation.json"
    data.parent.mkdir(parents=True)
    data.write_text("important conversation")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "calls"
    for name in ("gaia", "sudo"):
        command = binaries / name
        command.write_text('#!/bin/sh\necho called >> "$CALLS"\nexit 99\n')
        command.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(POSTRM), action],
        env={
            **os.environ,
            "HOME": str(home),
            "SUDO_USER": sudo_user,
            "PATH": str(binaries),
            "CALLS": str(calls),
        },
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert data.read_text() == "important conversation"
    assert not calls.exists(), "Maintainer script invoked per-user cleanup"
    if action == "purge":
        assert "Per-user data remains in ~/.gaia" in result.stdout
        assert "gaia uninstall --purge" in result.stdout
