# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The per-test thread guard joins what a test started and names what won't stop (#2786)."""

from __future__ import annotations

import threading
import time

import pytest
from email_thread_guard import threads_left_running


def _start(target, *, daemon=True, name=None):
    t = threading.Thread(target=target, daemon=daemon, name=name)
    t.start()
    return t


def test_short_lived_thread_is_joined_not_reported():
    before = threading.enumerate()
    t = _start(lambda: time.sleep(0.05))
    assert threads_left_running(before, timeout=5) == []
    assert not t.is_alive()


@pytest.mark.parametrize("daemon", [True, False])
def test_thread_that_outlives_the_timeout_is_named(daemon):
    release = threading.Event()
    before = threading.enumerate()
    t = _start(release.wait, daemon=daemon, name="stuck-worker")
    try:
        assert threads_left_running(before, timeout=0.05) == ["stuck-worker"]
    finally:
        release.set()
        t.join(timeout=5)


def test_threads_started_before_the_snapshot_are_ignored():
    release = threading.Event()
    older = _start(release.wait, name="older-worker")
    try:
        before = threading.enumerate()
        started = time.monotonic()
        assert threads_left_running(before, timeout=5) == []
        assert time.monotonic() - started < 1, "must not wait on pre-existing threads"
        assert older.is_alive()
    finally:
        release.set()
        older.join(timeout=5)


def test_worker_that_died_from_an_escaping_exception_is_not_reported(monkeypatch):
    deaths = []
    monkeypatch.setattr(threading, "excepthook", deaths.append)

    def _die():
        raise KeyboardInterrupt("boom")

    before = threading.enumerate()
    _start(_die)
    assert threads_left_running(before, timeout=5) == []
    assert [type(d.exc_value) for d in deaths] == [KeyboardInterrupt]


def test_empty_state_returns_nothing():
    assert threads_left_running(threading.enumerate(), timeout=0) == []


def test_timeout_is_shared_across_stuck_threads():
    release = threading.Event()
    before = threading.enumerate()
    stuck = [_start(release.wait, name=f"stuck-{i}") for i in range(4)]
    try:
        started = time.monotonic()
        names = threads_left_running(before, timeout=0.2)
        elapsed = time.monotonic() - started
        assert sorted(names) == [f"stuck-{i}" for i in range(4)]
        assert elapsed < 0.6, f"waited {elapsed:.2f}s for 4 threads at a 0.2s budget"
    finally:
        release.set()
        for t in stuck:
            t.join(timeout=5)
