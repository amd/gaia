# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A watched file is reported once it has finished being written.

``FileChangeHandler`` ran its callback (a reindex, for the chat agent) on the
watchdog observer thread, at the first event for a path, then ignored every
event for that path for ``debounce_seconds``. So a file being copied in was
indexed half-written, the final content never was, and a slow reindex stalled
delivery of every other event. Callbacks now run on a dispatch thread once the
file has gone a full window with no events and no change in size or mtime.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.utils.file_watcher import (
    FileChangeHandler,
    FileWatcher,
    check_watchdog_available,
)

WINDOW = 0.3


def _event(path: Path) -> MagicMock:
    event = MagicMock()
    event.is_directory = False
    event.src_path = str(path)
    return event


def test_a_file_written_in_pieces_is_reported_once_with_its_final_content(tmp_path):
    target = tmp_path / "report.txt"
    seen = []
    handler = FileChangeHandler(
        on_created=lambda p: seen.append(Path(p).read_text()),
        on_modified=lambda p: seen.append(Path(p).read_text()),
        extensions=[".txt"],
        debounce_seconds=WINDOW,
    )
    try:
        target.write_text("part 1\n")
        handler.on_created(_event(target))
        for i in range(2, 6):
            time.sleep(WINDOW / 3)
            with target.open("a") as f:
                f.write(f"part {i}\n")
            handler.on_modified(_event(target))

        assert handler.wait_idle(10)
        assert seen == ["".join(f"part {i}\n" for i in range(1, 6))]
    finally:
        handler.close()


def test_a_file_still_growing_without_events_is_not_reported_yet(tmp_path):
    """Size and mtime must hold still too; event delivery can lag the writes."""
    target = tmp_path / "copy.txt"
    target.write_text("a")
    seen = []
    handler = FileChangeHandler(
        on_created=lambda p: seen.append(Path(p).read_text()),
        extensions=[".txt"],
        debounce_seconds=WINDOW,
    )
    try:
        handler.on_created(_event(target))
        for _ in range(4):
            time.sleep(WINDOW * 0.8)
            with target.open("a") as f:
                f.write("a")
        assert seen == []
        assert handler.wait_idle(10)
        assert seen == ["aaaaa"]
    finally:
        handler.close()


def test_a_slow_callback_does_not_block_the_observer_thread(tmp_path):
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("a")
    second.write_text("b")
    release = threading.Event()
    handler = FileChangeHandler(
        on_created=lambda p: release.wait(10),
        extensions=[".txt"],
        debounce_seconds=0,
    )
    try:
        handler.on_created(_event(first))
        time.sleep(0.2)  # let the dispatcher enter the slow callback

        delivered = threading.Event()

        def observer_thread():
            handler.on_created(_event(second))
            delivered.set()

        threading.Thread(target=observer_thread, daemon=True).start()
        assert delivered.wait(2), "event delivery waited on a running callback"
    finally:
        release.set()
        handler.wait_idle(10)
        handler.close()


def test_a_file_deleted_before_it_settles_is_not_reported(tmp_path):
    target = tmp_path / "tmp.txt"
    target.write_text("x")
    created, deleted = [], []
    handler = FileChangeHandler(
        on_created=created.append,
        on_deleted=deleted.append,
        extensions=[".txt"],
        debounce_seconds=WINDOW,
    )
    try:
        handler.on_created(_event(target))
        target.unlink()
        handler.on_deleted(_event(target))
        assert handler.wait_idle(10)
        assert created == []
        assert deleted == [str(target)]
    finally:
        handler.close()


@pytest.mark.skipif(not check_watchdog_available(), reason="watchdog not installed")
def test_real_watcher_reports_a_copied_file_once_complete(tmp_path):
    seen = []
    watcher = FileWatcher(
        tmp_path,
        on_created=lambda p: seen.append(Path(p).read_text()),
        on_modified=lambda p: seen.append(Path(p).read_text()),
        extensions=[".txt"],
        debounce_seconds=WINDOW,
    )
    with watcher:
        target = tmp_path / "doc.txt"
        with target.open("w") as f:
            for i in range(5):
                f.write(f"line {i}\n")
                f.flush()
                time.sleep(WINDOW / 3)
        deadline = time.monotonic() + 10
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)
        assert watcher._handler.wait_idle(10)
    assert seen == ["".join(f"line {i}\n" for i in range(5))]
