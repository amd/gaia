# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The cross-process file lock every ``~/.gaia`` read-modify-write goes through."""

from __future__ import annotations

import os
import threading

import pytest

from gaia.utils.file_lock import (
    FileLockError,
    FileLockTimeout,
    file_lock,
    lock_path_for,
    write_text_atomic,
)


def test_lock_file_sits_beside_its_target(tmp_path):
    assert lock_path_for(tmp_path / "config.json") == tmp_path / "config.json.lock"


def test_reentrant_within_a_thread(tmp_path):
    target = tmp_path / "state.json"
    with file_lock(target):
        with file_lock(target, timeout=0):
            pass
        # Still held after the inner block exits.
        held_elsewhere = []

        def _try():
            try:
                with file_lock(target, timeout=0):
                    held_elsewhere.append(False)
            except FileLockTimeout:
                held_elsewhere.append(True)

        thread = threading.Thread(target=_try)
        thread.start()
        thread.join()
        assert held_elsewhere == [True]


def test_released_lock_can_be_taken_again(tmp_path):
    target = tmp_path / "state.json"
    with file_lock(target):
        pass
    with file_lock(target, timeout=0):
        pass


def test_timeout_names_the_lock_file(tmp_path):
    target = tmp_path / "state.json"
    errors = []
    taken = threading.Event()
    release = threading.Event()

    def _holder():
        with file_lock(target):
            taken.set()
            release.wait(10)

    holder = threading.Thread(target=_holder)
    holder.start()
    taken.wait(10)
    try:
        with pytest.raises(FileLockTimeout) as exc:
            with file_lock(target, timeout=0.1):
                errors.append("acquired a held lock")
    finally:
        release.set()
        holder.join()
    assert not errors
    assert exc.value.lock_path == lock_path_for(target)
    assert str(lock_path_for(target)) in str(exc.value)


def test_unwritable_location_is_a_lock_error_not_a_raw_oserror(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(FileLockError, match="Cannot create the lock file"):
        with file_lock(blocker / "config.json"):
            pass


def test_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "out.json"
    write_text_atomic(target, "{}\n")
    write_text_atomic(target, '{"a": 1}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


@pytest.mark.skipif(
    os.name != "nt", reason="Windows refuses a rename onto an open file"
)
def test_replace_waits_out_a_brief_hold_by_another_program(tmp_path, monkeypatch):
    from gaia.utils import file_lock as mod

    real_replace = os.replace
    denials = []

    def held_twice(src, dst):
        if len(denials) < 2:
            denials.append(dst)
            raise PermissionError(13, "Access is denied", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", held_twice)
    write_text_atomic(tmp_path / "out.json", "{}\n")
    assert len(denials) == 2
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == "{}\n"


@pytest.mark.skipif(
    os.name != "nt", reason="Windows refuses a rename onto an open file"
)
def test_replace_held_too_long_names_the_file(tmp_path, monkeypatch):
    from gaia.utils import file_lock as mod

    def always_held(src, dst):
        raise PermissionError(13, "Access is denied", str(dst))

    monkeypatch.setattr(mod, "LOCK_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(mod.os, "replace", always_held)
    target = tmp_path / "out.json"
    with pytest.raises(PermissionError, match="another program has it open"):
        write_text_atomic(target, "{}\n")
    assert list(tmp_path.iterdir()) == []
