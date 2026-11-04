# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The .deb prerm stops each user's GAIA daemon (#6572), with fake system tools."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Debian maintainer script requires a POSIX shell"
)

PRERM = Path(__file__).resolve().parents[3] / "installer/debian/prerm"

DAEMON = "/home/x/.gaia/venv/bin/python -m gaia.daemon"


def _fake(bin_dir, name, body):
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _run(tmp_path, action, *, processes, accounts, gaia_users=(), stop_exit=0):
    """Run prerm with ps/getent/runuser/pkill faked; returns (result, calls)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls"
    (tmp_path / "ps.txt").write_text("".join(f"{line}\n" for line in processes))
    _fake(fake_bin, "ps", f'cat "{tmp_path}/ps.txt"\n')
    passwd = []
    for uid, user in accounts.items():
        home = tmp_path / "home" / user
        home.mkdir(parents=True)
        passwd.append(f"{uid}) echo '{user}:x:{uid}:{uid}::{home}:/bin/sh' ;;")
        if user in gaia_users:
            gaia = home / ".gaia/venv/bin/gaia"
            gaia.parent.mkdir(parents=True)
            gaia.write_text("#!/bin/sh\n")
            gaia.chmod(0o755)
    _fake(
        fake_bin,
        "getent",
        'case "$2" in\n' + "\n".join(passwd) + "\n*) exit 2 ;;\nesac\n",
    )
    _fake(fake_bin, "runuser", f'echo "$*" >> "{calls}"\nexit {stop_exit}\n')
    _fake(fake_bin, "pkill", "exit 1\n")
    result = subprocess.run(
        ["/bin/sh", str(PRERM), action],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    stops = calls.read_text().splitlines() if calls.exists() else []
    return result, stops


@pytest.mark.parametrize("action", ["remove", "upgrade"])
def test_each_users_daemon_is_stopped_as_that_user(tmp_path, action):
    result, stops = _run(
        tmp_path,
        action,
        processes=[f"1000 {DAEMON}", f"1000 {DAEMON}", f"1001 {DAEMON}"],
        accounts={"1000": "ana", "1001": "bo"},
        gaia_users=("ana", "bo"),
    )

    assert result.returncode == 0, result.stderr
    assert len(stops) == 2
    for user in ("ana", "bo"):
        home = tmp_path / "home" / user
        assert (
            f"-u {user} -- env HOME={home} PYTHONIOENCODING=utf-8 timeout 120 "
            f"{home}/.gaia/venv/bin/gaia daemon stop"
        ) in stops


def test_other_processes_are_left_alone(tmp_path):
    result, stops = _run(
        tmp_path,
        "remove",
        processes=[
            "1000 /home/x/.gaia/venv/bin/python -m gaia.ui.server",
            "1000 /home/x/.gaia/venv/bin/gaia chat --ui",
            "1000 vim gaia.daemon",
        ],
        accounts={"1000": "ana"},
        gaia_users=("ana",),
    )

    assert result.returncode == 0
    assert stops == []


def test_a_stop_that_fails_is_reported_and_removal_continues(tmp_path):
    result, stops = _run(
        tmp_path,
        "remove",
        processes=[f"1000 {DAEMON}"],
        accounts={"1000": "ana"},
        gaia_users=("ana",),
        stop_exit=1,
    )

    assert result.returncode == 0
    assert len(stops) == 1
    assert "Could not stop ana's GAIA daemon (exit 1)" in result.stderr


def test_a_user_without_a_gaia_venv_is_reported(tmp_path):
    result, stops = _run(
        tmp_path,
        "remove",
        processes=[f"1000 {DAEMON}", f"4242 {DAEMON}"],
        accounts={"1000": "ana"},
    )

    assert result.returncode == 0
    assert stops == []
    assert "ana's GAIA daemon was not stopped" in result.stderr
    assert "uid 4242, which has no account" in result.stderr


def test_failed_upgrade_stops_nothing(tmp_path):
    result, stops = _run(
        tmp_path,
        "failed-upgrade",
        processes=[f"1000 {DAEMON}"],
        accounts={"1000": "ana"},
        gaia_users=("ana",),
    )

    assert result.returncode == 0
    assert stops == []
