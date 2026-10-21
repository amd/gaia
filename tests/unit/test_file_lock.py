# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The cross-process file lock every ``~/.gaia`` read-modify-write goes through."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gaia.utils.file_lock import (
    FileLockError,
    FileLockTimeout,
    file_lock,
    lock_dir_unwritable,
    lock_path_for,
    write_text_atomic,
)
from tests.unit.denied_folder import cannot_create_files_in

REPO_SRC = Path(__file__).resolve().parents[2] / "src"


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

    monkeypatch.setattr(mod, "REPLACE_RETRY_SECONDS", 0.1)
    monkeypatch.setattr(mod.os, "replace", always_held)
    target = tmp_path / "out.json"
    with pytest.raises(PermissionError, match="another program has it open"):
        write_text_atomic(target, "{}\n")
    assert list(tmp_path.iterdir()) == []


def test_a_holder_waiting_out_renames_never_outlasts_a_waiter():
    """A skill swap does three renames under the lock; waiters must outlast it."""
    from gaia.utils import file_lock as mod

    assert 3 * mod.REPLACE_RETRY_SECONDS < mod.LOCK_TIMEOUT_SECONDS


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only attribute")
def test_a_read_only_target_fails_at_once_instead_of_waiting(tmp_path):
    import stat
    import time

    from gaia.utils import file_lock as mod

    target = tmp_path / "out.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, stat.S_IREAD)
    try:
        started = time.monotonic()
        with pytest.raises(PermissionError, match="marked read-only"):
            write_text_atomic(target, '{"a": 1}\n')
        assert time.monotonic() - started < mod.REPLACE_RETRY_SECONDS
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)


# ---------------------------------------------------------------------------
# Unwritable folders, found by trying to create a file rather than os.access
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")
def test_an_acl_denied_folder_is_reported_unwritable(tmp_path):
    folder = tmp_path / "denied"
    folder.mkdir()
    with cannot_create_files_in(folder):
        # os.access reads only the read-only attribute, so it calls this writable.
        assert os.access(folder, os.W_OK)
        with pytest.raises(FileLockError) as exc:
            with file_lock(folder / "state.json"):
                pass
    assert lock_dir_unwritable(exc.value)
    assert f"cannot create files in {folder}" in str(exc.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_a_read_only_posix_folder_is_reported_unwritable(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    with cannot_create_files_in(folder):
        with pytest.raises(FileLockError) as exc:
            with file_lock(folder / "state.json"):
                pass
    assert lock_dir_unwritable(exc.value)


def test_an_unopenable_lock_file_in_a_writable_folder_is_not_unwritable(
    tmp_path, monkeypatch
):
    """Another process could still replace the target there: no unlocked read."""
    import errno

    from gaia.utils import file_lock as mod

    target = tmp_path / "state.json"
    lock = lock_path_for(target)
    lock.write_text("", encoding="utf-8")
    real_open = os.open

    def lock_file_denied(name, flags, *args):
        if str(name) == str(lock):
            raise PermissionError(errno.EACCES, "Permission denied", str(name))
        return real_open(name, flags, *args)

    monkeypatch.setattr(mod.os, "open", lock_file_denied)
    with pytest.raises(FileLockError) as exc:
        with file_lock(target):
            pass
    assert not lock_dir_unwritable(exc.value)
    assert not list(tmp_path.glob(".gaia-write-probe-*"))


# ---------------------------------------------------------------------------
# Waiters get the lock in arrival order
# ---------------------------------------------------------------------------


def _queue_files(target):
    lock = lock_path_for(target)
    return sorted(p.name for p in lock.parent.glob(lock.name + ".q*"))


def test_a_released_lock_leaves_no_queue_entry(tmp_path):
    target = tmp_path / "state.json"
    with file_lock(target):
        assert len(_queue_files(target)) == 1
    assert _queue_files(target) == []


def test_a_crashed_waiters_entry_does_not_block_the_queue(tmp_path):
    target = tmp_path / "state.json"
    lock = lock_path_for(target)
    # Left by a process killed while it waited: nobody holds its lock.
    stale = lock.with_name(lock.name + ".q0000000000")
    stale.write_text("", encoding="utf-8")
    with file_lock(target, timeout=0):
        pass
    assert not stale.exists()


def test_an_entry_that_cannot_be_checked_is_named_in_the_timeout(tmp_path, monkeypatch):
    """An entry nobody can test (another user's, or NFS) is counted and named."""
    from gaia.utils import file_lock as mod

    target = tmp_path / "state.json"
    lock = lock_path_for(target)
    unknown = lock.with_name(lock.name + ".q0000000000")
    unknown.write_text("", encoding="utf-8")
    monkeypatch.setattr(mod, "_probe_lock", lambda fd: None)
    with pytest.raises(FileLockTimeout) as exc:
        with file_lock(target, timeout=0.1):
            pass
    assert exc.value.ahead == 1
    assert str(unknown) in str(exc.value)
    assert "delete them" in str(exc.value)
    assert unknown.exists()


def test_timeout_says_how_many_operations_were_ahead(tmp_path):
    target = tmp_path / "state.json"
    taken, release = threading.Event(), threading.Event()

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
                pass
    finally:
        release.set()
        holder.join()
    assert exc.value.ahead == 1
    assert "1 other GAIA operation was ahead" in str(exc.value)
    assert "stuck" not in str(exc.value)


def _held_elsewhere(target, check):
    """Run *check* while another thread holds *target*'s lock."""
    taken, release = threading.Event(), threading.Event()

    def _holder():
        with file_lock(target):
            taken.set()
            release.wait(10)

    holder = threading.Thread(target=_holder)
    holder.start()
    assert taken.wait(10)
    try:
        return check()
    finally:
        release.set()
        holder.join()


def test_a_folder_that_refuses_new_files_still_locks_an_existing_lock_file(
    tmp_path,
):
    """No queue entry can be made there, but the lock still excludes."""
    folder = tmp_path / "ro"
    folder.mkdir()
    target = folder / "state.json"
    lock_path_for(target).write_text("", encoding="utf-8")
    with cannot_create_files_in(folder):

        def second_try():
            with pytest.raises(FileLockTimeout):
                with file_lock(target, timeout=0.1):
                    pass

        _held_elsewhere(target, second_try)
        with file_lock(target, timeout=0):
            pass
    assert _queue_files(target) == []


@pytest.mark.skipif(
    not (os.name == "nt" or sys.platform == "darwin"),
    reason="case-insensitive file names",
)
def test_two_spellings_of_one_name_share_a_queue(tmp_path):
    def other_spelling():
        with pytest.raises(FileLockTimeout) as exc:
            with file_lock(tmp_path / "state.json", timeout=0.1):
                pass
        return exc.value

    error = _held_elsewhere(tmp_path / "State.json", other_spelling)
    assert error.ahead == 1


def test_a_held_lock_can_be_released_from_another_thread(tmp_path):
    from gaia.utils.file_lock import acquire_file_lock

    target = tmp_path / "state.json"
    held = acquire_file_lock(target)
    with pytest.raises(FileLockTimeout):
        with file_lock(target, timeout=0):
            pass
    releaser = threading.Thread(target=held.release)
    releaser.start()
    releaser.join()
    held.release()  # a second release does nothing
    with file_lock(target, timeout=0):
        pass
    assert _queue_files(target) == []


_WAITER = """
import sys, time
from gaia.utils.file_lock import FileLockTimeout, file_lock

target, name, timeout, order = sys.argv[1:5]
try:
    with file_lock(target, timeout=float(timeout)):
        with open(order, "a", encoding="utf-8") as f:
            f.write(name + "\\n")
        time.sleep(0.05)
except FileLockTimeout as e:
    print(str(e))
"""


def _spawn_waiter(tmp_path, target, name, timeout):
    """Start a GAIA process waiting on *target*; return once it has queued."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["GAIA_HOME"] = str(tmp_path / "gaia-home")
    env["HOME"] = env["USERPROFILE"] = str(tmp_path / "home")
    queued = len(_queue_files(target)) + 1
    proc = subprocess.Popen(
        [sys.executable, "-c", _WAITER, str(target), name, str(timeout)]
        + [str(tmp_path / "order.txt")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 120
    while len(_queue_files(target)) < queued:
        assert proc.poll() is None, proc.communicate()
        assert time.monotonic() < deadline, f"waiter {name} never queued"
        time.sleep(0.01)
    return proc


def test_processes_get_the_lock_in_the_order_they_asked(tmp_path):
    """Four GAIA processes wait on one lock; none may jump the queue."""
    target = tmp_path / "state.json"
    with file_lock(target):
        waiters = [_spawn_waiter(tmp_path, target, str(i), 120) for i in range(4)]
        impatient = _spawn_waiter(tmp_path, target, "impatient", 1)
        out, err = impatient.communicate(timeout=120)
    results = [w.communicate(timeout=120) for w in waiters]
    assert all(p.returncode == 0 for p in waiters + [impatient]), results
    order = (tmp_path / "order.txt").read_text(encoding="utf-8").split()
    assert order == ["0", "1", "2", "3"]
    # The holder plus the four that queued before it.
    assert "5 other GAIA operations were ahead" in out, out + err
    assert _queue_files(target) == []


# ---------------------------------------------------------------------------
# Crashes and races that must not lose data or reorder the queue
# ---------------------------------------------------------------------------

_HOLD_THEN_HANG = """
import sys, time
from pathlib import Path
from gaia.utils.file_lock import file_lock

target, marker = sys.argv[1:3]
with file_lock(target):
    Path(marker).write_text("held", encoding="utf-8")
    time.sleep(120)
"""

_REWRITE_FOREVER = """
import json, sys
from gaia.utils.file_lock import file_lock, write_text_atomic

target = sys.argv[1]
i = 0
while True:
    with file_lock(target):
        write_text_atomic(target, json.dumps({"n": i, "pad": "x" * 200_000}))
    i += 1
"""


def _spawn(tmp_path, script, *args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["GAIA_HOME"] = str(tmp_path / "gaia-home")
    env["HOME"] = env["USERPROFILE"] = str(tmp_path / "home")
    return subprocess.Popen(
        [sys.executable, "-c", script, *map(str, args)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_a_process_killed_while_holding_the_lock_frees_it(tmp_path):
    """The OS drops a dead holder's lock; its queue entry is cleared by the next waiter."""
    target = tmp_path / "state.json"
    marker = tmp_path / "held"
    holder = _spawn(tmp_path, _HOLD_THEN_HANG, target, marker)
    try:
        deadline = time.monotonic() + 120
        while not marker.exists():
            assert holder.poll() is None, holder.communicate()
            assert time.monotonic() < deadline, "holder never took the lock"
            time.sleep(0.01)
        with pytest.raises(FileLockTimeout):
            with file_lock(target, timeout=0.1):
                pass
    finally:
        holder.kill()
        holder.communicate(timeout=30)
    assert len(_queue_files(target)) == 1  # the dead holder's entry
    with file_lock(target, timeout=5):
        pass
    assert _queue_files(target) == []


def test_a_writer_killed_mid_rewrite_leaves_a_whole_file(tmp_path):
    """Killing a writer at an arbitrary point never leaves a torn target."""
    import json

    target = tmp_path / "state.json"
    write_text_atomic(target, json.dumps({"n": -1}))
    for _ in range(3):
        writer = _spawn(tmp_path, _REWRITE_FOREVER, target)
        time.sleep(1.0)
        assert writer.poll() is None, writer.communicate()
        writer.kill()
        writer.communicate(timeout=30)
        with file_lock(target, timeout=5):
            doc = json.loads(target.read_text(encoding="utf-8"))
        assert isinstance(doc["n"], int)


def test_leaving_never_deletes_a_newer_entry_with_the_same_name(tmp_path, monkeypatch):
    """A newcomer that reuses a leaving waiter's entry name keeps its entry."""
    from gaia.utils import file_lock as mod

    target = tmp_path / "state.json"
    held = mod.acquire_file_lock(target)
    entry = held._entry
    newcomer = []
    real_release = mod._release_fd

    def release_then_newcomer_takes_the_name(fd):
        real_release(fd)
        if fd == entry.fd and not entry.path.exists():
            fd2 = os.open(str(entry.path), os.O_RDWR | os.O_CREAT | os.O_EXCL)
            newcomer.append(fd2)

    monkeypatch.setattr(mod, "_release_fd", release_then_newcomer_takes_the_name)
    try:
        held.release()
        if newcomer:
            assert entry.path.exists(), "a newcomer's queue entry was deleted"
            assert mod._same_file(newcomer[0], entry.path)
    finally:
        for fd in newcomer:
            os.close(fd)
    if os.name != "nt":
        # POSIX unlinks while still locked, so the name was free for the newcomer.
        assert newcomer


def test_probing_a_new_entry_never_strands_its_owner(tmp_path, monkeypatch):
    """An owner that locks its entry just after a probe must not lose the file.

    A waiter can probe an entry between its owner's create and lock. If the
    owner then locks it and sees its file in place, the probe must not delete
    it, or later waiters would stop counting the owner and jump ahead.
    """
    from gaia.daemon.lock import try_lock
    from gaia.utils import file_lock as mod

    target = tmp_path / "state.json"
    entry = mod._create_entry(lock_path_for(target), target)
    owner_fd = entry.fd
    seen = {}
    real_release = mod._release_fd

    def owner_locks_right_after_the_probe(fd):
        real_release(fd)
        if "same" not in seen:
            seen["locked"] = try_lock(owner_fd)
            seen["same"] = mod._same_file(owner_fd, entry.path)

    monkeypatch.setattr(mod, "_release_fd", owner_locks_right_after_the_probe)
    try:
        assert mod._entry_alive(entry.path) is False
        if seen.get("locked") and seen["same"]:
            assert entry.path.exists(), "an owner's locked entry was deleted"
    finally:
        monkeypatch.setattr(mod, "_release_fd", real_release)
        real_release(owner_fd)
        mod._discard_entry(entry.path)
