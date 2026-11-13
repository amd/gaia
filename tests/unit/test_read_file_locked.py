# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``read_file`` tells a briefly locked file from one it may never read (#8095).

Windows reports a file another program holds open without sharing as a bare
``[Errno 13] Permission denied``. The agent read that as a hard denial and
left the file out, though the lock lifted a minute later. The tool now waits
a few seconds for it, and says "in use" rather than "denied" when it stays
locked.
"""

from __future__ import annotations

import sys

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import path_access
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator

on_windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows locking")


@pytest.fixture
def read_file(tmp_path):
    mixin = FileIOToolsMixin()
    mixin.console = None
    mixin.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        mixin.register_file_io_tools()
        yield _TOOL_REGISTRY["read_file"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def waits(monkeypatch):
    """The retry's sleeps, recorded instead of slept."""
    slept = []
    monkeypatch.setattr(path_access.time, "sleep", slept.append)
    return slept


@pytest.fixture
def csv(tmp_path):
    path = tmp_path / "s06.csv"
    path.write_text("t,v\n1,26.4\n", encoding="utf-8")
    return path


def _hold_without_sharing(path):
    """Open *path* the way the stress test's injector did: no sharing at all."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    return lambda: kernel32.CloseHandle(handle)


@on_windows
def test_a_file_locked_throughout_is_reported_in_use_not_denied(read_file, csv, waits):
    release = _hold_without_sharing(csv)
    try:
        result = read_file(file_path=str(csv))
    finally:
        release()

    assert result["status"] == "error"
    assert result["error_type"] == "file_in_use"
    assert "in use by another program" in result["error"]
    assert "Permission denied" not in result["error"]
    assert "Errno" not in result["error"]
    assert str(csv) in result["error"]
    assert waits == list(path_access.IN_USE_RETRY_DELAYS)


@on_windows
def test_a_lock_lifted_during_the_wait_is_read(read_file, csv, monkeypatch):
    release = _hold_without_sharing(csv)
    monkeypatch.setattr(path_access.time, "sleep", lambda _s: release())

    result = read_file(file_path=str(csv))

    assert result["status"] == "success", result
    assert "26.4" in result["content"]


def test_an_access_denial_is_not_retried_and_says_so(
    read_file, csv, waits, monkeypatch
):
    def denied(_path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(path_access, "_ON_WINDOWS", True)
    monkeypatch.setattr(path_access, "_open_for_read", denied)
    monkeypatch.setattr(path_access, "_open_winerror", lambda _p: 5)

    result = read_file(file_path=str(csv))

    assert result["status"] == "error"
    assert result["error_type"] == "access_denied"
    assert "in use" not in result["error"]
    assert str(csv) in result["error"]
    assert waits == []


@pytest.mark.parametrize("winerror", [32, 33])
def test_a_sharing_or_lock_violation_is_retried_a_bounded_number_of_times(
    read_file, csv, waits, monkeypatch, winerror
):
    def locked(_path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(path_access, "_ON_WINDOWS", True)
    monkeypatch.setattr(path_access, "_open_for_read", locked)
    monkeypatch.setattr(path_access, "_open_winerror", lambda _p: winerror)

    result = read_file(file_path=str(csv))

    assert result["error_type"] == "file_in_use"
    assert waits == list(path_access.IN_USE_RETRY_DELAYS)
    assert sum(waits) <= 5


def test_an_unlocked_file_reads_without_waiting(read_file, csv, waits):
    result = read_file(file_path=str(csv))

    assert result["status"] == "success"
    assert waits == []
