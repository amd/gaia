# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A slow first daemon start is visible, and a timeout says what to do.

The first start on a fresh install compiled every module before it could
answer, overran the 30 s wait, and the first `gaia hub install gaia` failed
with "did not become healthy" while a retry worked.
"""

from __future__ import annotations

import pytest

from gaia.daemon import client
from gaia.daemon.errors import DaemonStartError

# Captured at import, before tests/unit/conftest.py replaces it per test.
_REAL_SPAWN_AND_WAIT = client._spawn_and_wait


class _StillStarting:
    pid = 4242
    returncode = None

    def poll(self):
        return None


class _Clock:
    """monotonic() that advances one second per call; sleep() is free."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 1.0
        return self.now

    def sleep(self, _seconds):
        pass


@pytest.fixture
def slow_start(monkeypatch, tmp_path):
    log = tmp_path / "daemon.log"
    log.write_text("line one\nimporting gaia.daemon.app\n", encoding="utf-8")
    monkeypatch.setattr(client.paths, "ensure_host_dir", lambda: None)
    monkeypatch.setattr(client.paths, "log_path", lambda: log)
    monkeypatch.setattr(client, "read_instance", lambda: None)
    monkeypatch.setattr(client.subprocess, "Popen", lambda *a, **k: _StillStarting())
    clock = _Clock()
    monkeypatch.setattr(client.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(client.time, "sleep", clock.sleep)
    return log


def test_progress_is_reported_while_the_daemon_starts(slow_start):
    seen = []

    with pytest.raises(DaemonStartError):
        _REAL_SPAWN_AND_WAIT(30.0, on_progress=seen.append)

    assert seen, "a slow start reported no progress"
    assert seen[0] >= client._PROGRESS_AFTER
    gaps = [b - a for a, b in zip(seen, seen[1:])]
    assert all(gap >= client._PROGRESS_EVERY for gap in gaps)


def test_timeout_says_the_daemon_is_still_starting_and_shows_the_log(slow_start):
    with pytest.raises(DaemonStartError) as exc:
        _REAL_SPAWN_AND_WAIT(10.0)

    message = str(exc.value)
    assert "within 10s" in message
    assert "still running (pid 4242)" in message
    assert "run the command again" in message
    assert "gaia daemon logs" in message
    assert str(slow_start) in message
    assert "importing gaia.daemon.app" in message


@pytest.mark.parametrize(
    "go_file, const",
    [
        ("tui/internal/daemon/client.go", "defaultStartTimeout"),
        ("tui/internal/ui/preflight/model.go", "startTimeout"),
    ],
)
def test_the_tui_waits_longer_than_a_python_daemon_start(go_file, const):
    """The TUI runs `gaia daemon start` under its own deadline; one under the
    Python wait kills a start that was about to succeed."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / go_file).read_text(encoding="utf-8")
    match = re.search(rf"\b{const}\s*=\s*(\d+)\s*\*\s*time\.Second", source)
    assert match, f"{go_file} no longer declares {const} in seconds"
    assert int(match.group(1)) > client._START_TIMEOUT + 10
