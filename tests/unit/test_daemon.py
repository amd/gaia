# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the headless custody daemon skeleton (issue #2018).

Pure instance.json logic — atomic write, mode, round-trip, corruption handling,
and the pid-guarded remove. No subprocesses, no network: those cross-system
lifecycle tests (start/attach, 401 auth, kill-9 reclaim, the `gaia daemon` CLI)
live in tests/integration/test_daemon.py.
"""

from __future__ import annotations

import json
import os

import pytest

from gaia.daemon import instance, paths
from gaia.daemon.constants import SERVICE_ID
from gaia.daemon.instance import DaemonInstance


@pytest.fixture()
def daemon_home(tmp_path, monkeypatch):
    """Isolate all daemon on-disk state under a tmp dir for one test."""
    home = tmp_path / "host"
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# instance.json: atomic write, mode, round-trip, corruption handling
# ---------------------------------------------------------------------------


def test_write_instance_atomic_mode_and_roundtrip(daemon_home):
    inst = DaemonInstance(pid=1234, port=55555, token="tok-abc", started_at=100.0)
    instance.write_instance(inst)

    path = paths.instance_path()
    assert path.exists()

    # 0600 is only meaningful on POSIX; Windows does not honor the mode bits.
    if os.name != "nt":
        assert (os.stat(path).st_mode & 0o777) == 0o600

    # No temp file left behind (temp-then-rename completed).
    leftovers = list(daemon_home.glob(".instance.*.tmp"))
    assert leftovers == []

    back = instance.read_instance()
    assert back is not None
    assert back.pid == 1234
    assert back.port == 55555
    assert back.token == "tok-abc"
    assert back.service == SERVICE_ID
    assert back.base_url == f"http://127.0.0.1:{back.port}"


def test_write_instance_overwrites_atomically(daemon_home):
    instance.write_instance(DaemonInstance(pid=1, port=2, token="a"))
    instance.write_instance(DaemonInstance(pid=9, port=8, token="z"))
    back = instance.read_instance()
    assert back.pid == 9 and back.port == 8 and back.token == "z"
    # The overwrite is a single rename — the target is always complete JSON.
    data = json.loads(paths.instance_path().read_text())
    assert set(("pid", "port", "token", "service", "api_version")).issubset(data)


def test_read_instance_missing_returns_none(daemon_home):
    assert instance.read_instance() is None


def test_read_instance_corrupt_returns_none(daemon_home):
    paths.ensure_host_dir()
    paths.instance_path().write_text("{ not valid json ", encoding="utf-8")
    # A corrupt registry is treated as "no trustworthy instance", not an exception.
    assert instance.read_instance() is None


def test_remove_instance_only_pid_guard(daemon_home):
    instance.write_instance(DaemonInstance(pid=42, port=1, token="t"))
    # Wrong pid → must NOT delete a newer daemon's registry.
    instance.remove_instance(only_pid=99)
    assert instance.read_instance() is not None
    # Matching pid → deletes.
    instance.remove_instance(only_pid=42)
    assert instance.read_instance() is None
