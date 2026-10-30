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

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.utils import file_watcher
from gaia.utils.file_watcher import (
    FileChangeHandler,
    FileWatcher,
    check_watchdog_available,
)

WINDOW = 0.3
# Longer than any stall, so only the test's clock can close a window.
HOUR = 3600.0


def _event(path: Path) -> MagicMock:
    event = MagicMock()
    event.is_directory = False
    event.src_path = str(path)
    return event


def _wait_until(condition, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what}")
        time.sleep(0.01)


class _Clock:
    """The watcher's monotonic clock, plus an offset only the test moves."""

    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return time.monotonic() + self.offset

    def advance(self, handler, seconds):
        with handler._cond:
            self.offset += seconds
            handler._cond.notify_all()


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(
        file_watcher,
        "time",
        SimpleNamespace(monotonic=fake.monotonic, sleep=time.sleep, time=time.time),
    )
    return fake


def test_a_file_written_in_pieces_is_reported_once_with_its_final_content(
    tmp_path, clock
):
    target = tmp_path / "report.txt"
    seen = []
    handler = FileChangeHandler(
        on_created=lambda p: seen.append(Path(p).read_text()),
        on_modified=lambda p: seen.append(Path(p).read_text()),
        extensions=[".txt"],
        debounce_seconds=HOUR,
    )
    try:
        target.write_text("part 1\n")
        handler.on_created(_event(target))
        for i in range(2, 6):
            # Each piece lands halfway through the window the last one opened.
            clock.advance(handler, HOUR / 2)
            with target.open("a") as f:
                f.write(f"part {i}\n")
            handler.on_modified(_event(target))
            # Nothing is due, so this only gives an early report time to land.
            assert not handler.wait_idle(0.1), f"reported before part {i} settled"
        assert seen == []

        clock.advance(handler, HOUR)
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
        debounce_seconds=0.1,
    )
    test_thread = threading.current_thread()
    writes = 0

    def stat_while_the_copy_grows(path):
        # The copy grows before each of the watcher's checks, with no event.
        nonlocal writes
        if threading.current_thread() is not test_thread and writes < 4:
            writes += 1
            with open(path, "a") as f:
                f.write("a")
        return FileChangeHandler._signature(path)

    handler._signature = stat_while_the_copy_grows
    try:
        handler.on_created(_event(target))
        assert handler.wait_idle(10)
        assert writes == 4
        assert seen == ["aaaaa"]
    finally:
        handler.close()


def test_a_slow_callback_does_not_block_the_observer_thread(tmp_path):
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("a")
    second.write_text("b")
    entered, release = threading.Event(), threading.Event()

    def slow(_path):
        entered.set()
        release.wait(60)

    handler = FileChangeHandler(
        on_created=slow, extensions=[".txt"], debounce_seconds=0
    )
    try:
        handler.on_created(_event(first))
        assert entered.wait(10)

        delivered = threading.Event()

        def observer_thread():
            handler.on_created(_event(second))
            delivered.set()

        threading.Thread(target=observer_thread, daemon=True).start()
        # Only the finally below releases the callback, so a blocked delivery
        # cannot finish in time however long this waits.
        assert delivered.wait(10), "event delivery waited on a running callback"
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
def test_a_file_that_cannot_be_checked_is_logged_not_silently_dropped(
    tmp_path, monkeypatch, caplog
):
    target = tmp_path / "locked.txt"
    target.write_text("x")
    seen = []
    handler = FileChangeHandler(
        on_created=seen.append, extensions=[".txt"], debounce_seconds=WINDOW
    )

    def _denied(path, *a, **k):
        raise PermissionError(13, "Access is denied", str(path))

    monkeypatch.setattr("gaia.utils.file_watcher.os.stat", _denied)
    try:
        with caplog.at_level("WARNING", logger="gaia.utils.file_watcher"):
            handler.on_created(_event(target))
            assert handler.wait_idle(10)
        assert seen == []
        assert any(
            "Cannot check" in r.getMessage() and str(target) in r.getMessage()
            for r in caplog.records
        )
    finally:
        handler.close()


def test_close_stops_the_rest_of_a_batch_already_taken(tmp_path):
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    started, release, seen = threading.Event(), threading.Event(), []

    def on_deleted(path):
        seen.append(path)
        started.set()
        release.wait(10)

    handler = FileChangeHandler(
        on_deleted=on_deleted, extensions=[".txt"], debounce_seconds=WINDOW
    )
    with handler._cond:
        handler._ready.extend([("deleted", str(first)), ("deleted", str(second))])
        handler._ensure_dispatcher()
        handler._cond.notify()
    assert started.wait(10)
    closer = threading.Thread(target=handler.close)
    closer.start()
    deadline = time.monotonic() + 10
    while not handler._closed and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    closer.join(10)

    assert seen == [str(first)]


def test_real_watcher_reports_a_copied_file_once_complete(tmp_path, clock):
    seen, arrived = [], []

    def only_the_copy(path):
        arrived.append(Path(path).name)
        return Path(path).name == "doc.txt"

    def mark(name):
        # One watch delivers its events in order: once this marker's event is
        # in, every earlier write's event is too.
        marker = tmp_path / name

        def marker_arrived():
            if name in arrived:
                return True
            marker.write_text(str(time.monotonic()))  # each write is an event
            return False

        _wait_until(marker_arrived, f"the watcher to see {name}")

    watcher = FileWatcher(
        tmp_path,
        on_created=lambda p: seen.append(Path(p).read_text()),
        on_modified=lambda p: seen.append(Path(p).read_text()),
        filter_func=only_the_copy,
        debounce_seconds=HOUR,
    )
    with watcher:
        mark("ready.txt")
        target = tmp_path / "doc.txt"
        with target.open("w") as f:
            for i in range(5):
                f.write(f"line {i}\n")
                f.flush()
        mark("done.txt")
        assert seen == []

        clock.advance(watcher._handler, HOUR)
        assert watcher._handler.wait_idle(10)
    assert seen == ["".join(f"line {i}\n" for i in range(5))]


def test_a_file_still_locked_by_its_writer_is_retried(tmp_path):
    """An exclusive open (Windows) makes the reader's read fail with
    PermissionError even though the file has settled."""
    target = tmp_path / "locked.txt"
    target.write_text("final")
    attempts = []

    def read(path):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError(13, "being used by another process", path)

    handler = FileChangeHandler(
        on_created=read, extensions=[".txt"], debounce_seconds=0.1
    )
    try:
        handler.on_created(_event(target))
        assert handler.wait_idle(10)
        assert len(attempts) == 3
        assert handler.telemetry["files_created"] == 1
    finally:
        handler.close()


def test_a_file_locked_for_good_is_given_up_on_loudly(tmp_path, caplog):
    target = tmp_path / "locked.txt"
    target.write_text("final")
    attempts = []

    def read(path):
        attempts.append(path)
        raise PermissionError(13, "being used by another process", path)

    handler = FileChangeHandler(
        on_created=read, extensions=[".txt"], debounce_seconds=0.05
    )
    try:
        handler.on_created(_event(target))
        assert handler.wait_idle(10)
        assert len(attempts) == 4  # the first try and three retries
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        assert "still locked" in errors[0].getMessage()
        assert "Close the program using locked.txt" in errors[0].getMessage()
        assert errors[0].exc_info is not None
    finally:
        handler.close()


def _always_locked(attempts):
    def read(path):
        attempts.append(path)
        raise PermissionError(13, "being used by another process", path)

    return read


def _wait_for_a_retry(handler):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with handler._cond:
            if handler._locked_retries:
                return
        time.sleep(0.01)
    pytest.fail("no locked-file retry was scheduled")


def test_a_new_change_to_a_locked_file_restarts_its_retries(tmp_path):
    target = tmp_path / "locked.txt"
    target.write_text("final")
    attempts = []
    read = _always_locked(attempts)
    handler = FileChangeHandler(
        on_created=read, on_modified=read, extensions=[".txt"], debounce_seconds=0.05
    )
    try:
        handler.on_created(_event(target))
        _wait_for_a_retry(handler)
        handler.on_modified(_event(target))
        assert handler.wait_idle(30)
        assert len(attempts) == 5  # the first try, then a fresh try and 3 retries
    finally:
        handler.close()


def test_a_change_queued_during_a_locked_read_is_not_counted_as_a_retry(
    tmp_path, caplog
):
    """The newer change runs on its own; it is not a retry with a back-off."""
    target = tmp_path / "locked.txt"
    target.write_text("final")
    attempts = []

    def read(path):
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            handler.on_modified(_event(target))  # the writer saved again
            raise PermissionError(13, "being used by another process", path)

    handler = FileChangeHandler(
        on_created=read, on_modified=read, extensions=[".txt"], debounce_seconds=0.05
    )
    try:
        with caplog.at_level("WARNING", logger="gaia.utils.file_watcher"):
            handler.on_created(_event(target))
            assert handler.wait_idle(30)
        assert len(attempts) == 2
        assert "retrying" not in caplog.text
        assert handler._locked_retries == {}
    finally:
        handler.close()


def test_deleting_a_locked_file_forgets_its_retries(tmp_path):
    """No delete callback is set, so nothing else would clear the count."""
    target = tmp_path / "locked.txt"
    target.write_text("final")
    attempts = []
    handler = FileChangeHandler(
        on_created=_always_locked(attempts), extensions=[".txt"], debounce_seconds=0.05
    )
    try:
        handler.on_created(_event(target))
        _wait_for_a_retry(handler)
        target.unlink()
        handler.on_deleted(_event(target))
        assert handler.wait_idle(30)
        assert attempts == [str(target)]
        assert handler._locked_retries == {}
    finally:
        handler.close()


def test_a_successful_move_clears_both_paths_retry_counts(tmp_path):
    src, dest = str(tmp_path / "old.txt"), str(tmp_path / "doc.txt")
    handler = FileChangeHandler(
        on_moved=lambda s, d: None, extensions=[".txt"], debounce_seconds=0.05
    )
    try:
        handler._locked_retries.update({src: 1, dest: 2})
        handler.on_moved(MagicMock(is_directory=False, src_path=src, dest_path=dest))
        assert handler.wait_idle(10)
        assert handler._locked_retries == {}
        assert handler.telemetry["files_moved"] == 1
    finally:
        handler.close()


def test_a_locked_move_destination_is_retried_as_a_modification(tmp_path):
    target = tmp_path / "locked.txt"
    target.write_text("final")
    moved, modified = [], []

    def on_moved(src, dest):
        moved.append(dest)
        raise PermissionError(13, "being used by another process", dest)

    handler = FileChangeHandler(
        on_moved=on_moved,
        on_modified=modified.append,
        extensions=[".txt"],
        debounce_seconds=0.05,
    )
    try:
        handler.on_moved(
            MagicMock(
                is_directory=False,
                src_path=str(tmp_path / "~WRD0001.tmp"),
                dest_path=str(target),
            )
        )
        assert handler.wait_idle(10)
        assert moved == [str(target)]
        assert modified == [str(target)]
    finally:
        handler.close()


def test_a_dispatch_bug_is_logged_and_dispatch_continues(tmp_path, monkeypatch, caplog):
    target = tmp_path / "a.txt"
    target.write_text("x")
    seen = []
    handler = FileChangeHandler(
        on_created=seen.append, extensions=[".txt"], debounce_seconds=0
    )
    real_settle = handler._settle
    calls = []

    def broken_once(checked, now):
        calls.append(threading.current_thread())
        if len(calls) == 1:
            raise RuntimeError("bug in settle")
        return real_settle(checked, now)

    monkeypatch.setattr(handler, "_settle", broken_once)
    try:
        # A second event would restart a dead dispatcher; the retry must not need one.
        handler.on_created(_event(target))
        assert handler.wait_idle(10)
        assert seen == [str(target)]
        assert "bug in settle" in caplog.text
        assert len(calls) == 2 and calls[0] is calls[1]
        assert handler._dispatcher is calls[0] and calls[0].is_alive()
    finally:
        handler.close()


def test_a_delete_event_for_a_file_still_on_disk_is_a_change(tmp_path):
    target = tmp_path / "report.docx"
    target.write_text("new contents")
    modified, deleted = [], []
    handler = FileChangeHandler(
        on_modified=modified.append,
        on_deleted=deleted.append,
        extensions=[".docx"],
        debounce_seconds=WINDOW,
    )
    try:
        handler.on_deleted(_event(target))
        assert handler.wait_idle(10)
        assert deleted == []
        assert modified == [str(target)]
    finally:
        handler.close()


@pytest.mark.skipif(not check_watchdog_available(), reason="watchdog not installed")
def test_saving_by_rename_over_a_watched_file_never_reports_it_deleted(tmp_path):
    from watchdog.observers import Observer

    root = tmp_path.resolve()
    target = root / "report.docx"
    target.write_text("old contents")
    deleted, changed = [], []
    lock = threading.Lock()

    def record(bucket):
        def callback(*paths):
            with lock:
                bucket.append(paths[-1])

        return callback

    handler = FileChangeHandler(
        on_modified=record(changed),
        on_moved=record(changed),
        on_deleted=record(deleted),
        extensions=[".docx", ".tmp"],
        debounce_seconds=WINDOW,
    )
    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)
    observer.start()
    try:
        time.sleep(0.5)  # FSEvents drops events from before its stream starts
        staged = root / "~WRL0001.tmp"
        staged.write_text("new contents")
        os.replace(staged, target)
        _wait_until(lambda: str(target) in changed, "the save to be reported")
        time.sleep(1.0)  # let any trailing delete event arrive
        assert handler.wait_idle(10)
        assert str(target) not in deleted
    finally:
        observer.stop()
        observer.join(5)
        handler.close()
