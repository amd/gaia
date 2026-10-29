# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The daemon starts in its own host dir, never in the launcher's cwd.

It outlives the command that started it. Inheriting that command's cwd pinned
an arbitrary project folder for the daemon's whole life: on Windows the folder
could then not be deleted or renamed ("being used by another process").
"""

from __future__ import annotations

import os

import pytest

from gaia.daemon import client
from gaia.daemon.errors import DaemonStartError

# Captured at import, before tests/unit/conftest.py replaces it per test.
_REAL_SPAWN_AND_WAIT = client._spawn_and_wait


class _ExitedEarly:
    pid = 4242
    returncode = 1

    def poll(self):
        return 1


@pytest.fixture
def spawn_capture(monkeypatch, tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    seen = {}

    def _popen(*args, **kwargs):
        seen.update(kwargs)
        return _ExitedEarly()

    monkeypatch.setattr(client.paths, "ensure_host_dir", lambda: host)
    monkeypatch.setattr(client.paths, "log_path", lambda: tmp_path / "daemon.log")
    monkeypatch.setattr(client, "read_instance", lambda: None)
    monkeypatch.setattr(client.subprocess, "Popen", _popen)
    return host, seen


def _spawn():
    with pytest.raises(DaemonStartError, match="exited early"):
        _REAL_SPAWN_AND_WAIT(timeout=5)


def test_daemon_cwd_is_its_host_dir(spawn_capture):
    host, seen = spawn_capture
    _spawn()
    assert seen["cwd"] == str(host)


def test_daemon_cwd_ignores_where_the_launcher_runs(
    spawn_capture, tmp_path, monkeypatch
):
    host, seen = spawn_capture
    project = tmp_path / "some project"
    project.mkdir()
    monkeypatch.chdir(project)
    _spawn()
    assert seen["cwd"] == str(host)
    assert os.path.normcase(seen["cwd"]) != os.path.normcase(str(project))


# A relative home must name the same folder for the daemon, which starts in its
# host dir, as for the launcher, which polls that folder for instance.json.


@pytest.fixture
def launch_dir(tmp_path, monkeypatch):
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.chdir(launch)
    return launch


def _env_seen(monkeypatch, tmp_path):
    seen = {}

    def _popen(*args, **kwargs):
        seen.update(kwargs)
        return _ExitedEarly()

    monkeypatch.setattr(client.paths, "log_path", lambda: tmp_path / "daemon.log")
    monkeypatch.setattr(client, "read_instance", lambda: None)
    monkeypatch.setattr(client.subprocess, "Popen", _popen)
    _spawn()
    return seen


def _host_dir_as_daemon(monkeypatch, env, cwd) -> str:
    """host_dir() as the spawned daemon resolves it: its env, its cwd."""
    for name in ("GAIA_HOME", client.paths.HOME_ENV_VAR):
        if name in env:
            monkeypatch.setenv(name, env[name])
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(cwd)
    return os.path.normcase(os.path.abspath(client.paths.host_dir()))


def test_relative_gaia_home_resolves_against_the_launcher(
    launch_dir, tmp_path, monkeypatch
):
    monkeypatch.delenv(client.paths.HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("GAIA_HOME", "relhome")
    seen = _env_seen(monkeypatch, tmp_path)

    expected = launch_dir / "relhome" / "host"
    assert seen["env"]["GAIA_HOME"] == str(launch_dir / "relhome")
    # Popen resolves a relative cwd against the launcher's, as here.
    assert os.path.normcase(os.path.abspath(seen["cwd"])) == os.path.normcase(
        str(expected)
    )
    daemon_sees = _host_dir_as_daemon(monkeypatch, seen["env"], seen["cwd"])
    assert daemon_sees == os.path.normcase(str(expected))


def test_relative_daemon_home_resolves_against_the_launcher(
    launch_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv(client.paths.HOME_ENV_VAR, os.path.join("state", "host"))
    seen = _env_seen(monkeypatch, tmp_path)

    expected = launch_dir / "state" / "host"
    assert seen["env"][client.paths.HOME_ENV_VAR] == str(expected)
    daemon_sees = _host_dir_as_daemon(monkeypatch, seen["env"], seen["cwd"])
    assert daemon_sees == os.path.normcase(str(expected))


def test_dot_relative_gaia_home_is_normalised(launch_dir, tmp_path, monkeypatch):
    monkeypatch.delenv(client.paths.HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("GAIA_HOME", os.path.join(".", "a", "..", "home") + os.sep)
    seen = _env_seen(monkeypatch, tmp_path)
    assert seen["env"]["GAIA_HOME"] == str(launch_dir / "home")


def test_absolute_homes_pass_through_unchanged(launch_dir, tmp_path, monkeypatch):
    home = str(tmp_path / "abs home")
    monkeypatch.setenv("GAIA_HOME", home)
    monkeypatch.setenv("GAIA_CONFIG_DIR", home)
    monkeypatch.delenv(client.paths.HOME_ENV_VAR, raising=False)
    seen = _env_seen(monkeypatch, tmp_path)
    assert seen["env"]["GAIA_HOME"] == home
    assert seen["env"]["GAIA_CONFIG_DIR"] == home


def test_tilde_gaia_home_expands_to_the_user_home(launch_dir, tmp_path, monkeypatch):
    user = tmp_path / "user"
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.delenv(client.paths.HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("GAIA_HOME", "~/gh")
    seen = _env_seen(monkeypatch, tmp_path)
    assert seen["env"]["GAIA_HOME"] == os.path.abspath(str(user / "gh"))
    assert "launch" not in seen["env"]["GAIA_HOME"]


def test_blank_gaia_home_is_left_alone(launch_dir, tmp_path, monkeypatch):
    """A blank GAIA_HOME means "unset" to every reader; it must not become cwd."""
    monkeypatch.delenv(client.paths.HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("GAIA_HOME", "  ")
    seen = _env_seen(monkeypatch, tmp_path)
    assert seen["env"]["GAIA_HOME"] == "  "


def test_relative_config_dir_resolves_against_the_launcher(
    launch_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("GAIA_CONFIG_DIR", "cfg")
    seen = _env_seen(monkeypatch, tmp_path)
    assert seen["env"]["GAIA_CONFIG_DIR"] == str(launch_dir / "cfg")


def test_relative_pythonpath_keeps_importing_the_launchers_source(
    launch_dir, tmp_path, monkeypatch
):
    absolute = str(tmp_path / "abs")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["src", absolute]))
    seen = _env_seen(monkeypatch, tmp_path)
    assert seen["env"]["PYTHONPATH"].split(os.pathsep) == [
        str(launch_dir / "src"),
        absolute,
    ]
