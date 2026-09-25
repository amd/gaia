# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Single-instance schedule daemon and locked store writes (#4208)."""

from __future__ import annotations

import threading

import pytest

from gaia.schedule import daemon
from gaia.schedule.lock import ScheduleLockError, daemon_lock, store_lock
from gaia.schedule.store import Schedule, TomlScheduleStore


def _schedule(name: str) -> Schedule:
    return Schedule(name=name, cron="0 9 * * *", prompt="say hello")


class TestDaemonLock:

    def test_second_daemon_fails_fast_without_arming(self, mocker, tmp_path):
        store_path = tmp_path / "schedules.toml"
        build = mocker.patch.object(
            daemon, "build_scheduler", side_effect=AssertionError("armed timers")
        )

        with daemon_lock(store_path):
            with pytest.raises(ScheduleLockError) as err:
                daemon.run_daemon(store_path)

        build.assert_not_called()
        message = str(err.value)
        assert "already running" in message
        assert str(store_path) in message
        assert "Stop the other daemon" in message

    def test_error_names_the_running_daemon_pid(self, tmp_path):
        import os

        store_path = tmp_path / "schedules.toml"
        with daemon_lock(store_path):
            with pytest.raises(ScheduleLockError, match=f"pid {os.getpid()}"):
                with daemon_lock(store_path):
                    pass

    def test_lock_is_released_when_the_daemon_exits(self, tmp_path):
        store_path = tmp_path / "schedules.toml"
        with daemon_lock(store_path):
            pass
        with daemon_lock(store_path):
            pass

    def test_daemons_on_different_stores_do_not_collide(self, tmp_path):
        with daemon_lock(tmp_path / "a" / "schedules.toml"):
            with daemon_lock(tmp_path / "b" / "schedules.toml"):
                pass


class TestStoreLock:

    def test_concurrent_add_and_mark_run_keep_both_changes(self, tmp_path):
        path = tmp_path / "schedules.toml"
        daemon_store = TomlScheduleStore(path)
        daemon_store.add(_schedule("a"))
        cli_store = TomlScheduleStore(path)

        loaded = threading.Event()
        go = threading.Event()
        real_load = daemon_store.load

        def slow_load():
            result = real_load()
            loaded.set()
            go.wait(5)
            return result

        daemon_store.load = slow_load
        errors = []

        def run(fn):
            try:
                fn()
            except Exception as exc:  # surfaced via the assert below
                errors.append(exc)

        marker = threading.Thread(
            target=run,
            args=(lambda: daemon_store.mark_run("a", "2026-01-01T07:00:00+00:00"),),
        )
        marker.start()
        assert loaded.wait(5)
        adder = threading.Thread(
            target=run, args=(lambda: cli_store.add(_schedule("b")),)
        )
        adder.start()
        # Unlocked, the add lands inside mark_run's read-modify-write window.
        adder.join(0.5)
        go.set()
        marker.join(5)
        adder.join(5)

        assert errors == []
        final = TomlScheduleStore(path).load()
        assert set(final) == {"a", "b"}
        assert final["a"].last_run == "2026-01-01T07:00:00+00:00"

    def test_store_lock_times_out_loudly(self, tmp_path):
        path = tmp_path / "schedules.toml"
        with store_lock(path):
            with pytest.raises(ScheduleLockError, match="could not lock"):
                with store_lock(path, timeout=0.1):
                    pass
