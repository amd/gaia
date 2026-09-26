# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Single-instance schedule daemon and locked store writes (#4208).

Windows takes a different locking primitive (``msvcrt.locking``) from POSIX
(``fcntl.flock``), and three of its differences are load-bearing here: it locks
one byte at the *current offset* rather than the file, it refuses another
handle's read of that byte, and the owner may still truncate and write it. The
bug this guards against — a login-item daemon plus a manual one firing every
schedule twice — is most likely to hit a Windows user, so these run on Windows
too and assert what that platform actually does instead of degrading to a
smoke test.
"""

from __future__ import annotations

import os
import threading

import pytest

from gaia.daemon.lock import try_lock
from gaia.schedule import daemon
from gaia.schedule.lock import (
    ScheduleLockError,
    _read_pid,
    daemon_lock,
    daemon_lock_path,
    store_lock,
    store_lock_path,
)
from gaia.schedule.store import Schedule, TomlScheduleStore

WINDOWS = os.name == "nt"
windows_only = pytest.mark.skipif(WINDOWS is False, reason="msvcrt lock path")
posix_only = pytest.mark.skipif(WINDOWS, reason="flock lock path")


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

    def test_a_second_daemon_is_refused_on_every_platform(self, tmp_path):
        """The refusal itself is the contract; naming the pid is a courtesy."""
        store_path = tmp_path / "schedules.toml"
        with daemon_lock(store_path):
            with pytest.raises(ScheduleLockError) as err:
                with daemon_lock(store_path):
                    pass

        message = str(err.value)
        assert "already running" in message
        assert "Stop the other daemon first" in message

    @posix_only
    def test_error_names_the_running_daemon_pid(self, tmp_path):
        store_path = tmp_path / "schedules.toml"
        with daemon_lock(store_path):
            with pytest.raises(ScheduleLockError, match=f"pid {os.getpid()}"):
                with daemon_lock(store_path):
                    pass

    @windows_only
    def test_windows_omits_the_pid_rather_than_inventing_one(self, tmp_path):
        """Windows refuses another handle's read of the locked byte, so the
        holder's pid is unknowable. Say less; do not guess and do not crash."""
        store_path = tmp_path / "schedules.toml"
        with daemon_lock(store_path):
            with pytest.raises(ScheduleLockError) as err:
                with daemon_lock(store_path):
                    pass

        message = str(err.value)
        assert "(pid " not in message
        assert "already running" in message
        assert "Stop the other daemon first" in message

    def test_a_garbled_lock_file_still_gets_the_actionable_error(self, tmp_path):
        """A pre-existing or half-written lock file must not become a
        UnicodeDecodeError traceback in place of the refusal."""
        store_path = tmp_path / "schedules.toml"
        path = daemon_lock_path(store_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe not a pid")

        with daemon_lock(store_path):
            with pytest.raises(ScheduleLockError, match="already running"):
                with daemon_lock(store_path):
                    pass

    def test_reading_a_holders_pid_never_raises(self, tmp_path):
        path = tmp_path / "garbled.lock"
        path.write_bytes(b"\xff\xfe\x00rubbish")
        fd = os.open(str(path), os.O_RDWR)
        try:
            assert _read_pid(fd) == ""
        finally:
            os.close(fd)

    def test_the_two_locks_never_share_a_path(self, tmp_path):
        """A suffix-less store made both resolve to `<name>.lock`, so the daemon
        would hold the store lock for life and every write would time out."""
        for name in ("schedules.toml", "schedules", "sched.d", "schedules.lock"):
            store_path = tmp_path / name
            assert daemon_lock_path(store_path) != store_lock_path(store_path)
            assert daemon_lock_path(store_path) != store_path

    def test_the_daemon_lock_sits_beside_the_store(self, tmp_path):
        """`~/.gaia/schedules.lock` is the path documented in cli.mdx."""
        store_path = tmp_path / "schedules.toml"
        assert daemon_lock_path(store_path) == tmp_path / "schedules.lock"

    def test_the_daemon_can_still_write_the_store_while_holding_its_own_lock(
        self, tmp_path
    ):
        """The daemon's own mark_run goes through store_lock. If the two locks
        ever collided, this would block for the full timeout and then raise."""
        store_path = tmp_path / "schedules.toml"
        store = TomlScheduleStore(store_path)
        store.add(_schedule("a"))

        with daemon_lock(store_path):
            store.mark_run("a", "2026-01-01T07:00:00+00:00")

        assert store.load()["a"].last_run == "2026-01-01T07:00:00+00:00"

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


@windows_only
class TestWindowsLockingPrimitive:
    """What ``msvcrt.locking`` actually does, pinned rather than reasoned about.

    Each of these is a difference from ``fcntl.flock`` that the code above
    depends on. If a future Windows/Python release changes one, the failure
    should land here and not in a user's duplicated schedule run.
    """

    def test_the_owner_may_truncate_and_write_the_byte_it_locked(self, tmp_path):
        """`daemon_lock` writes its pid through the same handle that holds the
        lock on byte 0. Windows permits that for the lock owner."""
        fd = os.open(str(tmp_path / "x.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            assert try_lock(fd)
            os.ftruncate(fd, 0)
            assert os.write(fd, b"4321") == 4

            # Locked at byte 0 — a contender must still be refused afterwards.
            other = os.open(str(tmp_path / "x.lock"), os.O_RDWR)
            try:
                assert try_lock(other) is False
            finally:
                os.close(other)
        finally:
            os.close(fd)

    def test_unlocking_needs_the_seek_back_that_release_does(self, tmp_path):
        """msvcrt unlocks the byte at the CURRENT offset, and the pid write
        moved it past the locked one — which is why ``_release`` seeks to 0.

        ``unlock()`` swallows the OSError, so without the seek the only thing
        releasing the lock would be the ``os.close`` after it.
        """
        import msvcrt

        fd = os.open(str(tmp_path / "x.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            assert try_lock(fd)
            os.write(fd, b"4321")
            assert os.lseek(fd, 0, os.SEEK_CUR) == 4

            with pytest.raises(OSError):
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # the byte actually locked
        finally:
            os.close(fd)

    def test_another_handle_cannot_read_the_locked_byte(self, tmp_path):
        """The reason the Windows refusal names no pid: the read raises, and
        ``_read_pid`` returns "" rather than letting that escape."""
        path = tmp_path / "x.lock"
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            assert try_lock(fd)
            os.ftruncate(fd, 0)
            os.write(fd, b"4321")

            other = os.open(str(path), os.O_RDWR)
            try:
                with pytest.raises(OSError):
                    os.read(other, 32)
                assert _read_pid(other) == ""
            finally:
                os.close(other)
        finally:
            os.close(fd)
