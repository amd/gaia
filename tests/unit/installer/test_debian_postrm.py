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


def _run_script(
    script, action, tmp_path, *, target=None, regular_file=False, npu=False
):
    """Run a maintainer script with fake absolute paths redirected by sh."""
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # These commands are only used by the scripts' optional cache refreshes.
    for name in ("update-desktop-database", "gtk-update-icon-cache", "systemctl"):
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
    text = text.replace("/sys/module/amdxdna", str(root / "sys/module/amdxdna"))
    text = text.replace("/etc/", str(root / "etc") + "/")
    if npu:
        (root / "sys/module/amdxdna").mkdir(parents=True)
    script_copy = tmp_path / script.name
    script_copy.write_text(text)
    if target is not None:
        link = root / "usr/bin/gaia-desktop"
        link.symlink_to(target)
    if regular_file:
        (root / "usr/bin/gaia-desktop").write_text("admin's own launcher\n")
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


def test_postinst_reconfigure_keeps_our_link(tmp_path):
    result, link = _run_script(
        POSTINST, "configure", tmp_path, target="/opt/GAIA/gaia-desktop"
    )
    assert result.returncode == 0
    assert os.readlink(link) == "/opt/GAIA/gaia-desktop"


def test_postinst_preserves_replacement_link(tmp_path):
    result, link = _run_script(
        POSTINST, "configure", tmp_path, target="/usr/local/bin/custom-desktop"
    )
    assert result.returncode == 0
    assert os.readlink(link) == "/usr/local/bin/custom-desktop"


def test_postinst_preserves_dangling_replacement_link(tmp_path):
    # A broken link fails `-e` but is still someone else's; it must survive.
    result, link = _run_script(
        POSTINST, "configure", tmp_path, target=str(tmp_path / "missing")
    )
    assert result.returncode == 0
    assert os.readlink(link) == str(tmp_path / "missing")


@pytest.mark.parametrize("script,action", [(POSTINST, "configure"), (POSTRM, "purge")])
def test_scripts_preserve_regular_file(tmp_path, script, action):
    result, link = _run_script(script, action, tmp_path, regular_file=True)
    assert result.returncode == 0
    assert not link.is_symlink()
    assert link.read_text() == "admin's own launcher\n"


def test_postinst_lifts_memlock_for_the_npu_where_its_driver_is_loaded(tmp_path):
    """#6632: FastFlowLM can't pin a model in a desktop session's 8 MB."""
    from gaia.llm.npu_linux import MEMLOCK_DROPINS, memlock_fix_installed

    _run_script(POSTINST, "configure", tmp_path, npu=True)
    root = tmp_path / "root"
    for path, text in MEMLOCK_DROPINS.items():
        assert (root / path.lstrip("/")).read_text() == text
    # The app reads the same files to know the fix waits only for a restart.
    assert memlock_fix_installed(root)


def test_postinst_upgrade_never_writes_memlock_again(tmp_path):
    """An administrator who deleted the drop-ins keeps that across upgrades."""
    root = tmp_path / "root"
    (root / "sys/module/amdxdna").mkdir(parents=True)
    (root / "usr/bin").mkdir(parents=True)
    text = POSTINST.read_text()
    for path in ("/usr/bin/gaia-desktop", "/sys/module/amdxdna"):
        text = text.replace(path, str(root) + path)
    text = text.replace("/etc/", str(root / "etc") + "/")
    script = tmp_path / "postinst"
    script.write_text(text)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("update-desktop-database", "gtk-update-icon-cache", "systemctl"):
        (fake_bin / name).write_text("#!/bin/sh\nexit 0\n")
        (fake_bin / name).chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}
    subprocess.run(
        ["/bin/sh", str(script), "configure", "0.25.3"], env=env, check=True, timeout=5
    )
    assert not (root / "etc").exists()


def test_postinst_leaves_memlock_alone_without_an_npu(tmp_path):
    _run_script(POSTINST, "configure", tmp_path)
    assert not (tmp_path / "root" / "etc").exists()


def test_postrm_purge_removes_only_our_memlock_dropins(tmp_path):
    from gaia.llm.npu_linux import MEMLOCK_DROPINS

    root = tmp_path / "root"
    (ours, ours_text), (theirs, _) = MEMLOCK_DROPINS.items()
    for path, text in ((ours, ours_text), (theirs, "admin's own setting")):
        target = root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    script = tmp_path / "postrm"
    script.write_text(POSTRM.read_text().replace("/etc/", str(root / "etc") + "/"))
    # The runner's real cache refreshes scan /usr/share and can outlast the timeout.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("update-desktop-database", "gtk-update-icon-cache"):
        command = fake_bin / name
        command.write_text("#!/bin/sh\nexit 0\n")
        command.chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}
    for action, ours_kept in (("remove", True), ("purge", False)):
        subprocess.run(["/bin/sh", str(script), action], env=env, check=True, timeout=5)
        assert (root / ours.lstrip("/")).exists() == ours_kept
    assert (root / theirs.lstrip("/")).read_text() == "admin's own setting"
