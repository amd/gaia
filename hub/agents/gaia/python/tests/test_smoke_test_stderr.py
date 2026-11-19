# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Every failed stdio handshake in ``packaging/smoke_test.py`` reports the child's stderr.

The traceback behind a release-gate failure lives only on the child's stderr, so a
failure path that drops it leaves CI with a one-line verdict and no cause. Each test
runs a real child (this interpreter standing in for the frozen binary) that writes a
marker to stderr and then fails the handshake one way.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location(
    "gaia_smoke_test", PACKAGING / "smoke_test.py"
)
assert _spec and _spec.loader
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

MARKER = "Traceback: the cause lives only on stderr"
# Anchored to the header: the logged command line also contains MARKER.
STDERR_TAIL = f"stdio stderr tail:\n{MARKER}"


def _child(stdout_line: str, then: str = "") -> list[str]:
    script = (
        "import sys, time\n"
        f"print({MARKER!r}, file=sys.stderr, flush=True)\n"
        f"print({stdout_line!r}, flush=True)\n"
        f"{then}\n"
    )
    return ["-c", script]


def _handshake(argv: list[str]) -> bool:
    return smoke.check_stdio_handshake(Path(sys.executable), argv=argv)


def test_non_json_first_line_logs_stderr(capsys):
    assert _handshake(_child("not json", then="time.sleep(60)")) is False
    out = capsys.readouterr().out
    assert "is not JSON" in out
    assert STDERR_TAIL in out


def test_event_without_type_logs_stderr(capsys):
    assert _handshake(_child('{"detail": "x"}', then="time.sleep(60)")) is False
    out = capsys.readouterr().out
    assert "carries no 'type'" in out
    assert STDERR_TAIL in out


def test_no_exit_after_stdin_close_logs_stderr(capsys, monkeypatch):
    class _QuickWaitPopen(subprocess.Popen):
        # The harness allows 60s for a clean exit; the test should not.
        def wait(self, timeout=None):
            return super().wait(0.5 if timeout == 60 else timeout)

    monkeypatch.setattr(smoke.subprocess, "Popen", _QuickWaitPopen)
    argv = _child('{"type": "status"}', then="time.sleep(60)")
    assert _handshake(argv) is False
    out = capsys.readouterr().out
    assert "did not exit within 60s" in out
    assert STDERR_TAIL in out


def test_backend_mismatch_logs_stderr(capsys):
    argv = _child('{"type": "status", "model_backend": "cpu"}', then="time.sleep(60)")
    assert (
        smoke.check_stdio_handshake(Path(sys.executable), argv=argv, backend="vulkan")
        is False
    )
    out = capsys.readouterr().out
    assert "expected model_backend='vulkan'" in out
    assert STDERR_TAIL in out
