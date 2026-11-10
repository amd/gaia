# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The pre-install scan for processes running out of an agent's folder.

It walks every process on the machine, so one it cannot read must not decide
the install. psutil's macOS cmdline read can raise ``SystemError`` for a live
process, which used to fail the install outright.
"""

import psutil
import pytest

import gaia.daemon.sidecars.install as mod
from gaia.daemon.sidecars.errors import StopFailedError

_MACOS_ERROR = SystemError(
    "<built-in function proc_cmdline> returned a result with an exception set"
)


class _Proc:
    """Enough of ``psutil.Process`` for the scan; ``process_iter`` fills ``info``."""

    def __init__(self, pid, exe="", cmdline=(), cmdline_error=None):
        self.pid = pid
        self._exe, self._cmdline, self._cmdline_error = (
            exe,
            list(cmdline),
            cmdline_error,
        )

    def exe(self):
        return self._exe

    def cmdline(self):
        if self._cmdline_error is not None:
            raise self._cmdline_error
        return self._cmdline

    @property
    def info(self):
        # What process_iter(attrs) does: read each attribute, raising what it raises.
        return {"pid": self.pid, "exe": self.exe(), "cmdline": self.cmdline()}


def _machine(monkeypatch, procs):
    def process_iter(attrs=None):
        for proc in procs:
            if attrs:
                proc.info  # noqa: B018 - a read error ends the iteration, as in psutil
            yield proc

    monkeypatch.setattr(psutil, "process_iter", process_iter)


def test_a_process_whose_cmdline_cannot_be_read_does_not_fail_the_install(
    tmp_path, monkeypatch, caplog
):
    install_dir = tmp_path / "agents" / "email"
    _machine(
        monkeypatch,
        [
            _Proc(100, exe="/usr/bin/other", cmdline_error=_MACOS_ERROR),
            _Proc(101, exe="/usr/bin/editor", cmdline=["/usr/bin/editor"]),
        ],
    )

    with caplog.at_level("WARNING", logger="gaia.daemon.sidecars.install"):
        mod.assert_no_live_process_in(install_dir, "email")

    assert "pid 100" in caplog.text


def test_a_sidecar_with_an_unreadable_cmdline_is_still_found_by_its_executable(
    tmp_path, monkeypatch
):
    install_dir = tmp_path / "agents" / "email"
    _machine(
        monkeypatch,
        [
            _Proc(100, exe="/usr/bin/other", cmdline_error=_MACOS_ERROR),
            _Proc(
                4242,
                exe=str(install_dir / "email-agent"),
                cmdline_error=_MACOS_ERROR,
            ),
        ],
    )

    with pytest.raises(StopFailedError, match="4242"):
        mod.assert_no_live_process_in(install_dir, "email")


def test_a_script_run_from_the_folder_is_found_by_its_cmdline(tmp_path, monkeypatch):
    install_dir = tmp_path / "agents" / "email"
    _machine(
        monkeypatch,
        [
            _Proc(
                77,
                exe="/usr/bin/python3",
                cmdline=["python3", str(install_dir / "run.py")],
            )
        ],
    )

    with pytest.raises(StopFailedError, match="77"):
        mod.assert_no_live_process_in(install_dir, "email")
