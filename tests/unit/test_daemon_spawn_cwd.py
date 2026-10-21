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
