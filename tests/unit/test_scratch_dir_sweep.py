# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scratch folders do not pile up in the temp folder (#8101).

A scratch folder was removed only when its agent's ``cleanup()`` ran, which a
sidecar killed with the app, a crash or a one-shot run never reached: one PC
held 2,945 ``gaia-scratch-*`` folders. A process now removes the folders it
still holds when it exits, and the first agent in a process clears folders no
run has touched for a day.
"""

from __future__ import annotations

import os
import tempfile
import time

import pytest

from gaia import security
from gaia.security import SCRATCH_PREFIX, acquire_scratch_dir, release_scratch_dir

_DAY = 24 * 3600


@pytest.fixture
def temp(tmp_path, monkeypatch):
    """A private temp folder, and no holds or sweep left over from other tests."""
    root = tmp_path / "temp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    monkeypatch.setattr(security, "_scratch_holders", {})
    monkeypatch.setattr(security, "_scratch_swept", True)
    return root


def _aged(path, seconds):
    stamp = time.time() - seconds
    for entry in [*path.iterdir(), path] if path.is_dir() else [path]:
        os.utime(entry, (stamp, stamp))
    return path


def _scratch(temp, name, *files):
    path = temp / f"{SCRATCH_PREFIX}{name}"
    path.mkdir()
    for file in files:
        (path / file).write_text("print(1)\n", encoding="utf-8")
    return path


def test_a_folder_untouched_for_a_day_is_removed(temp):
    stale = _aged(_scratch(temp, "old", "make_inventory.py"), 2 * _DAY)
    empty = _aged(_scratch(temp, "empty"), 2 * _DAY)

    assert security.sweep_stale_scratch_dirs() == 2

    assert not stale.exists() and not empty.exists()


def test_a_recent_folder_is_kept(temp):
    recent = _aged(_scratch(temp, "recent", "a.py"), 3600)

    assert security.sweep_stale_scratch_dirs() == 0

    assert recent.is_dir()


def test_an_old_folder_with_a_fresh_script_is_kept(temp):
    folder = _aged(_scratch(temp, "busy", "old.py"), 2 * _DAY)
    (folder / "new.py").write_text("print(2)\n", encoding="utf-8")
    os.utime(folder, (time.time() - 2 * _DAY,) * 2)

    assert security.sweep_stale_scratch_dirs() == 0

    assert folder.is_dir()


def test_a_folder_this_process_holds_is_kept_however_old(temp, tmp_path):
    held = acquire_scratch_dir(str(tmp_path / "proj"))
    _aged(held, 2 * _DAY)

    assert security.sweep_stale_scratch_dirs() == 0

    assert held.is_dir()
    release_scratch_dir(held)


def test_only_scratch_folders_are_touched(temp):
    other = temp / "gaia-run-x"
    other.mkdir()
    _aged(other, 2 * _DAY)
    lookalike = temp / f"{SCRATCH_PREFIX}file"
    lookalike.write_text("not a folder", encoding="utf-8")
    _aged(lookalike, 2 * _DAY)

    assert security.sweep_stale_scratch_dirs() == 0

    assert other.is_dir() and lookalike.is_file()


def test_taking_a_folder_marks_it_in_use_for_other_processes(temp, tmp_path):
    held = acquire_scratch_dir(str(tmp_path / "proj"))
    release_scratch_dir(held)
    _aged(held, 2 * _DAY)

    again = acquire_scratch_dir(str(tmp_path / "proj"))
    security._scratch_holders.clear()  # as another process sees it

    assert security.sweep_stale_scratch_dirs() == 0
    assert again.is_dir()


def test_the_folders_a_process_still_holds_are_removed_when_it_exits(temp, tmp_path):
    first = acquire_scratch_dir(str(tmp_path / "a"))
    acquire_scratch_dir(str(tmp_path / "a"))
    second = acquire_scratch_dir(str(tmp_path / "b"))
    (second / "analyze.py").write_text("print(1)\n", encoding="utf-8")

    security._remove_held_scratch_dirs()

    assert not first.exists() and not second.exists()
    assert security._scratch_holders == {}


def test_the_first_agent_in_a_process_starts_the_sweep(temp, tmp_path, monkeypatch):
    monkeypatch.setattr(security, "_scratch_swept", False)
    stale = _aged(_scratch(temp, "old"), 2 * _DAY)

    held = acquire_scratch_dir(str(tmp_path / "proj"))
    security._scratch_sweeper.join(timeout=30)

    assert not stale.exists()
    assert held.is_dir()
    release_scratch_dir(held)
