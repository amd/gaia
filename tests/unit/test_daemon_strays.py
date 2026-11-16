# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia kill --daemons`` lists GAIA daemons and stops the orphaned ones (#5293)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from gaia import cli
from gaia.daemon import paths, strays
from gaia.daemon.strays import DaemonProcess

OLD = time.time() - 3 * 86400


def _registered_home(tmp_path, name):
    home = tmp_path / name
    home.mkdir()
    (home / "instance.json").write_text("{}", encoding="utf-8")
    return home


# -- recognising a daemon --------------------------------------------------------


@pytest.mark.parametrize(
    "cmdline, expected",
    [
        (["python", "-m", "gaia.daemon"], True),
        (["C:\\Py\\python.exe", "-X", "utf8", "-m", "gaia.daemon"], True),
        (["python", "-m", "gaia.daemon.server"], False),
        (["python", "-m", "gaia.cli", "daemon", "start"], False),
        (["pytest", "-k", "gaia.daemon"], False),
        (["bash", "-c", "python -m gaia.daemon"], False),
        (["-m", "gaia.daemon"], False),
        ([], False),
    ],
)
def test_is_daemon_cmdline(cmdline, expected):
    assert strays.is_daemon_cmdline(cmdline) is expected


@pytest.mark.parametrize(
    "env",
    [
        {"GAIA_DAEMON_HOME": "{tmp}/dh"},
        {"GAIA_HOME": "{tmp}/gh"},
        {"GAIA_DAEMON_HOME": "{tmp}/dh", "GAIA_HOME": "{tmp}/gh"},
        {"GAIA_HOME": "  {tmp}/gh  "},
        {},
    ],
)
def test_home_from_environ_matches_the_daemons_own_resolution(
    tmp_path, monkeypatch, env
):
    env = {k: v.format(tmp=tmp_path) for k, v in env.items()}
    for name in ("GAIA_DAEMON_HOME", "GAIA_HOME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert strays.home_from_environ(dict(os.environ)) == paths.host_dir()


# -- orphaned or not ---------------------------------------------------------------


def test_a_daemon_whose_home_is_gone_is_orphaned(tmp_path):
    assert DaemonProcess(1, OLD, tmp_path / "deleted").is_orphaned()


def test_a_daemon_without_instance_json_is_orphaned(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "daemon.log").write_text("held open", encoding="utf-8")
    assert DaemonProcess(1, OLD, home).is_orphaned()


def test_a_registered_daemon_is_not_orphaned(tmp_path):
    assert not DaemonProcess(1, OLD, _registered_home(tmp_path, "h")).is_orphaned()


def test_a_daemon_still_starting_up_is_never_orphaned(tmp_path):
    assert not DaemonProcess(1, time.time() - 5, tmp_path / "x").is_orphaned()


def test_a_daemon_with_an_unknown_home_is_never_orphaned():
    assert not DaemonProcess(1, OLD, None).is_orphaned()


@pytest.mark.parametrize(
    "seconds, text",
    [
        (0, "0s"),
        (45, "45s"),
        (423, "7m 3s"),
        (7500, "2h 5m"),
        (3 * 86400 + 4 * 3600, "3d 4h"),
    ],
)
def test_format_age(seconds, text):
    assert strays.format_age(seconds) == text


# -- finding daemons -----------------------------------------------------------------


class _FakeProc:
    def __init__(self, pid, ppid, cmdline, started, env):
        self.info = {
            "pid": pid,
            "ppid": ppid,
            "cmdline": cmdline,
            "create_time": started,
        }
        self._env = env

    def environ(self):
        if isinstance(self._env, Exception):
            raise self._env
        return self._env


DAEMON = ["python", "-m", "gaia.daemon"]


def test_find_daemons_lists_only_the_outer_process_of_a_launcher_pair(
    monkeypatch, tmp_path
):
    env = {"GAIA_DAEMON_HOME": str(tmp_path / "a")}
    procs = [
        _FakeProc(10, 1, DAEMON, OLD + 5, env),  # venv launcher
        _FakeProc(11, 10, DAEMON, OLD + 6, env),  # the real interpreter
        _FakeProc(20, 1, DAEMON, OLD, {"GAIA_DAEMON_HOME": str(tmp_path / "b")}),
        _FakeProc(30, 1, ["python", "-m", "gaia.cli"], OLD, env),
        _FakeProc(40, 1, None, OLD, env),  # cmdline unreadable
        _FakeProc(50, 1, DAEMON, OLD + 9, psutil.AccessDenied(50)),
        _FakeProc(60, 1, DAEMON, OLD + 9, psutil.NoSuchProcess(60)),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(procs))
    found = strays.find_daemons()
    assert [d.pid for d in found] == [20, 10, 50]
    assert found[0].home == tmp_path / "b"
    assert found[1].pids == (10, 11)
    assert found[0].pids == (20,)
    assert found[2].home is None


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_stop_daemon_stops_the_process():
    proc = _sleeper()
    try:
        started = psutil.Process(proc.pid).create_time()
        assert strays.stop_daemon(DaemonProcess(proc.pid, started, None))
        assert proc.wait(timeout=30) is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stop_daemon_leaves_its_other_children_alone():
    # Such as the Lemonade Server it started, which the user's GAIA may now use.
    code = "; ".join(
        [
            "import subprocess, sys, time",
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
            "print(c.pid, flush=True)",
            "time.sleep(60)",
        ]
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True
    )
    child_pid = int(parent.stdout.readline())
    try:
        started = psutil.Process(parent.pid).create_time()
        assert strays.stop_daemon(DaemonProcess(parent.pid, started, None))
        assert parent.wait(timeout=30) is not None
        assert psutil.pid_exists(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.stdout.close()
        try:
            psutil.Process(child_pid).kill()
        except psutil.NoSuchProcess:
            pass


def test_stop_daemon_never_kills_a_reused_pid():
    proc = _sleeper()
    try:
        started = psutil.Process(proc.pid).create_time()
        assert strays.stop_daemon(DaemonProcess(proc.pid, started - 3600, None))
        assert proc.poll() is None, "killed a process that only shares the pid"
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_stop_daemon_of_a_gone_process_is_done():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    assert strays.stop_daemon(DaemonProcess(proc.pid, OLD, None))


# -- gaia kill --daemons ---------------------------------------------------------------


def _run_kill(monkeypatch, capsys, daemons, stop_ok=True):
    stopped = []

    def fake_stop(daemon, timeout=10.0):
        stopped.append(daemon.pid)
        return stop_ok

    monkeypatch.setattr(strays, "find_daemons", lambda: daemons)
    monkeypatch.setattr(strays, "stop_daemon", fake_stop)
    monkeypatch.setattr(sys, "argv", ["gaia", "kill", "--daemons"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code, capsys.readouterr().out, stopped


def test_kill_daemons_with_none_running(monkeypatch, capsys):
    code, out, stopped = _run_kill(monkeypatch, capsys, [])
    assert code == 0 and "No GAIA daemons are running" in out and stopped == []


def test_kill_daemons_stops_only_the_orphans(monkeypatch, capsys, tmp_path):
    own = _registered_home(tmp_path, "own")
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(own))
    other = _registered_home(tmp_path, "other")
    daemons = [
        DaemonProcess(101, OLD, tmp_path / "deleted-home"),
        DaemonProcess(102, OLD, other),
        DaemonProcess(103, OLD, own),
        DaemonProcess(104, OLD, None),
        DaemonProcess(105, time.time() - 2, tmp_path / "starting"),
    ]
    code, out, stopped = _run_kill(monkeypatch, capsys, daemons)
    assert code == 0
    assert stopped == [101]
    assert "GAIA daemons running: 5" in out
    for pid in (101, 102, 103, 104, 105):
        assert f"pid {pid}" in out
    assert "orphaned" in out and str(tmp_path / "deleted-home") in out
    assert "this home, left running" in out
    assert "home unknown" in out
    assert "up 3d 0h" in out
    assert "Stopped orphaned daemon pid 101" in out


def test_kill_daemons_never_stops_this_homes_daemon_even_unregistered(
    monkeypatch, capsys, tmp_path
):
    own = tmp_path / "own-without-instance"
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(own))
    code, out, stopped = _run_kill(monkeypatch, capsys, [DaemonProcess(7, OLD, own)])
    assert code == 0 and stopped == [] and "No orphaned daemons" in out


def test_kill_daemons_never_stops_the_daemon_this_home_registered(
    monkeypatch, capsys, tmp_path
):
    # An older daemon ignored the GAIA_HOME it inherited and registered in the
    # real home, so its environment points at a deleted test folder.
    from gaia.daemon import instance

    own = tmp_path / "own"
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(own))
    instance.write_instance(instance.DaemonInstance(pid=4321, port=1, token="t"))
    daemons = [DaemonProcess(4320, OLD, tmp_path / "deleted-test-home", (4321,))]
    code, out, stopped = _run_kill(monkeypatch, capsys, daemons)
    assert code == 0 and stopped == []
    assert "this home, left running" in out and str(own) in out


def test_kill_daemons_fails_when_an_orphan_will_not_stop(monkeypatch, capsys, tmp_path):
    code, out, stopped = _run_kill(
        monkeypatch, capsys, [DaemonProcess(9, OLD, tmp_path / "gone")], stop_ok=False
    )
    assert code == 1 and stopped == [9]
    assert "did not exit" in out and "gaia kill --daemons" in out


def test_kill_without_a_target_mentions_daemons(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gaia", "kill"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "--daemons" in capsys.readouterr().out


def test_home_from_environ_uses_user_home_without_overrides(tmp_path):
    key = "USERPROFILE" if os.name == "nt" else "HOME"
    assert strays.home_from_environ({key: str(tmp_path)}) == Path(
        tmp_path, ".gaia", "host"
    )


def test_same_home_ignores_spelling(tmp_path):
    assert strays.same_home(tmp_path / "a" / ".." / "b", tmp_path / "b")
    assert not strays.same_home(tmp_path / "a", tmp_path / "b")
