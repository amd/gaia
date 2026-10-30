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
POSTINST = Path(__file__).resolve().parents[3] / "installer/debian/postinst"


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


def _run_script(script, action, tmp_path, *, target=None):
    """Run a maintainer script with fake absolute paths redirected by sh."""
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # These commands are only used by the scripts' optional cache refreshes.
    for name in ("update-desktop-database", "gtk-update-icon-cache"):
        command = fake_bin / name
        command.write_text("#!/bin/sh\nexit 0\n")
        command.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
    }
    # The test runs on POSIX CI. Pass a temporary root by substituting the
    # command path in a copy, so no real /usr/bin entry is touched.
    text = script.read_text()
    root = tmp_path / "root"
    (root / "usr/bin").mkdir(parents=True)
    text = text.replace("/usr/bin/gaia-desktop", str(root / "usr/bin/gaia-desktop"))
    script_copy = tmp_path / script.name
    script_copy.write_text(text)
    if target is not None:
        link = root / "usr/bin/gaia-desktop"
        link.symlink_to(target)
    return (
        subprocess.run(
            ["/bin/sh", str(script_copy), action],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ),
        root / "usr/bin/gaia-desktop",
    )


def test_postinst_creates_documented_desktop_command(tmp_path):
    result, link = _run_script(POSTINST, "configure", tmp_path)
    assert result.returncode == 0
    assert link.is_symlink()
    assert os.readlink(link) == "/opt/GAIA/gaia-desktop"


@pytest.mark.parametrize("action", ["remove", "purge"])
def test_postrm_removes_only_gaia_desktop_link(tmp_path, action):
    result, link = _run_script(
        POSTRM, action, tmp_path, target="/opt/GAIA/gaia-desktop"
    )
    assert result.returncode == 0
    assert not link.exists()
    assert not link.is_symlink()


def test_postrm_preserves_replacement_link(tmp_path):
    result, link = _run_script(
        POSTRM, "remove", tmp_path, target="/usr/local/bin/custom-desktop"
    )
    assert result.returncode == 0
    assert link.is_symlink()
    assert os.readlink(link) == "/usr/local/bin/custom-desktop"
