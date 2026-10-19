# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A pip/uv run that never finishes must not hold the install forever.

``uv pip install`` waits, with no limit, for a cache lock another session's
``uv`` holds. With no timeout on the child, the install thread waited with it
and the agent's install slot stayed taken: every retry got "already in
progress". The runner now has a deadline, kills the child's whole process tree
(``python -m uv`` runs the real ``uv`` as a grandchild that holds the pipes),
and raises an ``InstallError`` that names the likely lock.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from gaia.hub import installer
from gaia.hub.installer import InstallError

_HANG = [sys.executable, "-c", "import time; time.sleep(60)"]

# A parent that starts a long-lived grandchild sharing its stdout/stderr, the
# shape of ``python -m uv``. Killing only the parent leaves the pipes open.
_HANG_WITH_GRANDCHILD = [
    sys.executable,
    "-c",
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
    "time.sleep(60)",
]


@pytest.fixture(autouse=True)
def _short_timeout(monkeypatch):
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 1.0)
    monkeypatch.delenv(installer.PIP_TIMEOUT_ENV, raising=False)


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


@pytest.mark.parametrize(
    "frontend", [_HANG, _HANG_WITH_GRANDCHILD], ids=["child", "grandchild"]
)
def test_hung_pip_frontend_times_out_with_an_actionable_error(monkeypatch, frontend):
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [frontend])

    started = time.monotonic()
    error = _run_in_thread(lambda: installer._default_run_pip(["pkg"]), 30)

    assert isinstance(error, InstallError), error
    assert time.monotonic() - started < 30
    message = str(error)
    assert "timed out after 1s" in message
    assert "lock" in message
    assert installer.PIP_TIMEOUT_ENV in message


def test_timeout_does_not_fall_through_to_the_next_frontend(monkeypatch):
    calls = []
    real = installer._run_frontend

    def recording(cmd, timeout):
        calls.append(cmd)
        return real(cmd, timeout)

    monkeypatch.setattr(installer, "_pip_frontends", lambda: [_HANG, ["never-run"]])
    monkeypatch.setattr(installer, "_run_frontend", recording)

    with pytest.raises(InstallError, match="timed out"):
        installer._default_run_pip(["pkg"])
    assert len(calls) == 1


def test_the_limit_comes_from_the_environment_when_set(monkeypatch):
    seen = []

    def fake(cmd, timeout):
        seen.append(timeout)
        return installer.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(installer, "_run_frontend", fake)
    installer._default_run_pip(["pkg"])
    monkeypatch.setenv(installer.PIP_TIMEOUT_ENV, "1800")
    installer._default_run_pip(["pkg"])
    assert seen == [1.0, 1800.0]


@pytest.mark.parametrize("raw", ["soon", "0", "-5"])
def test_a_bad_limit_in_the_environment_fails_loudly(monkeypatch, raw):
    monkeypatch.setenv(installer.PIP_TIMEOUT_ENV, raw)
    with pytest.raises(InstallError, match=installer.PIP_TIMEOUT_ENV):
        installer._default_run_pip(["pkg"])
