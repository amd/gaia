# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A pip/uv run that never finishes must not hold the install forever.

``uv pip install`` waits, with no limit, for a cache lock another session's
``uv`` holds. With no timeout on the child, the install thread waited with it
and the agent's install slot stayed taken: every retry got "already in
progress". The runner now has a deadline, kills the child, and raises an
``InstallError`` that names the likely lock; the slot is released.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from gaia.hub import installer
from gaia.hub.installer import InstallError

_HANG = [sys.executable, "-c", "import time; time.sleep(60)"]


def _run_in_thread(fn, limit):
    """Run ``fn`` and return its exception, failing if it is still running."""
    box = {}

    def target():
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - surfaced to the test
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(limit)
    assert not t.is_alive(), f"pip runner still waiting after {limit}s"
    return box.get("error")


def test_hung_pip_frontend_times_out_with_an_actionable_error(monkeypatch):
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [_HANG])
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 1.0)

    started = time.monotonic()
    error = _run_in_thread(lambda: installer._default_run_pip(["pkg"]), 20)

    assert isinstance(error, InstallError)
    assert time.monotonic() - started < 20
    message = str(error)
    assert "did not finish within 1s" in message
    assert "lock" in message


def test_timeout_does_not_fall_through_to_the_next_frontend(monkeypatch):
    calls = []
    real_run = subprocess.run

    def recording_run(cmd, *a, **k):
        calls.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(installer, "_pip_frontends", lambda: [_HANG, ["never-run"]])
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(installer.subprocess, "run", recording_run)

    with pytest.raises(InstallError):
        installer._default_run_pip(["pkg"])
    assert len(calls) == 1


def test_every_pip_run_is_given_a_deadline(monkeypatch):
    seen = {}

    def fake_run(cmd, *a, **k):
        seen["timeout"] = k.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    installer._default_run_pip(["pkg"])
    assert seen["timeout"] == installer.PIP_TIMEOUT_SECONDS
