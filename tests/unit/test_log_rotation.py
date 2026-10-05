# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gaia.log stays bounded while several handlers and processes share it."""

import argparse
import logging
import os
import subprocess
import sys
import tarfile
import textwrap
import threading
from pathlib import Path

import pytest

from gaia import log_rotation
from gaia.log_rotation import (
    SharedRotatingFileHandler,
    log_family,
    log_limits,
    rotate_if_oversized,
)

SRC = Path(__file__).resolve().parents[2] / "src"
LINE_PAD = "x" * 60


def _logger(name, *handlers):
    lg = logging.getLogger(f"test_log_rotation.{name}")
    lg.handlers = list(handlers)
    lg.propagate = False
    lg.setLevel(logging.INFO)
    return lg


def _fail_on_handle_error(monkeypatch):
    def boom(self, record):
        raise AssertionError(f"handleError called for {record.getMessage()!r}")

    monkeypatch.setattr(SharedRotatingFileHandler, "handleError", boom)


def _all_lines(log):
    lines = []
    for f in log_family(log):
        lines += f.read_text(encoding="utf-8").splitlines()
    return lines


def _over_cap(log, cap):
    """Files in the family larger than ``cap``, by name, so a failure shows sizes."""
    sizes = {f.name: f.stat().st_size for f in log_family(log)}
    return {name: size for name, size in sizes.items() if size > cap}


@pytest.fixture
def windows(monkeypatch):
    """Windows file semantics: CRLF on disk, and no renaming a log that is open."""
    real_replace = os.replace

    def replace(src, dst):
        if os.path.basename(src) == "gaia.log":
            raise PermissionError(32, "being used by another process", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(log_rotation.os, "replace", replace)
    monkeypatch.setattr(log_rotation, "_LINESEP", "\r\n")


@pytest.fixture
def handlers():
    made = []
    yield made
    for h in made:
        h.close()


def test_rotates_at_cap_and_keeps_only_backup_count(tmp_path, handlers, monkeypatch):
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"
    h = SharedRotatingFileHandler(log, max_bytes=1000, backup_count=2)
    handlers.append(h)
    lg = _logger("cap", h)
    for i in range(200):
        lg.info("line %d %s", i, LINE_PAD)

    family = log_family(log)
    assert [f.name for f in family] == ["gaia.log", "gaia.log.1", "gaia.log.2"]
    assert _over_cap(log, 1000) == {}
    assert log.read_text(encoding="utf-8").splitlines()[-1].startswith("line 199")


def test_cap_counts_bytes_on_disk_not_characters(tmp_path, handlers, monkeypatch):
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"
    h = SharedRotatingFileHandler(log, max_bytes=100, backup_count=5)
    handlers.append(h)
    lg = _logger("bytes", h)
    lg.info("a" * 49)  # 50 bytes
    lg.info("\u00e9" * 40)  # 41 characters, 81 bytes: does not fit in the 50 left

    assert _over_cap(log, 100) == {}
    assert _all_lines(log) == ["\u00e9" * 40, "a" * 49]


def test_record_larger_than_the_cap_gets_a_file_to_itself(
    tmp_path, handlers, monkeypatch
):
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"
    h = SharedRotatingFileHandler(log, max_bytes=100, backup_count=5)
    handlers.append(h)
    lg = _logger("oversized", h)
    lg.info("before")
    lg.info("b" * 300)
    lg.info("after")

    eol = len(os.linesep)
    sizes = [f.stat().st_size for f in log_family(log)]
    assert sizes == [5 + eol, 300 + eol, 6 + eol]


def test_windows_line_endings_count_toward_the_cap(
    tmp_path, handlers, monkeypatch, windows
):
    """Two writers, each with its own handle, on a log neither can rename."""
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"
    a = SharedRotatingFileHandler(log, max_bytes=100, backup_count=5)
    b = SharedRotatingFileHandler(log, max_bytes=100, backup_count=5)
    handlers += [a, b]
    la, lb = _logger("win-a", a), _logger("win-b", b)
    la.info("a" * 49)  # 51 bytes with CRLF
    lb.info("b" * 48)  # 50 bytes with CRLF, 49 characters: one byte too many
    la.info("c" * 10)  # a's handle predates b's copy + truncate

    assert _over_cap(log, 100) == {}
    assert log.read_bytes() == b"b" * 48 + b"\r\n" + b"c" * 10 + b"\r\n"
    assert (tmp_path / "gaia.log.1").read_bytes() == b"a" * 49 + b"\r\n"


def test_lock_timeout_still_honours_the_cap(tmp_path, handlers, monkeypatch, capsys):
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"
    h = SharedRotatingFileHandler(log, max_bytes=100, backup_count=5)
    handlers.append(h)
    lg = _logger("nolock", h)
    lg.info("a" * 49)
    monkeypatch.setattr(h._ipc_lock, "acquire", lambda *a, **k: False)
    lg.info("b" * 79)  # would take the file to 130 bytes
    lg.info("c" * 9)

    assert log.read_text(encoding="utf-8").splitlines() == ["a" * 49, "c" * 9]
    assert capsys.readouterr().err.count("timed out waiting") == 1


def test_two_handlers_one_process_lose_nothing(tmp_path, handlers, monkeypatch):
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"
    a = SharedRotatingFileHandler(log, max_bytes=2000, backup_count=500)
    b = SharedRotatingFileHandler(log, max_bytes=2000, backup_count=500)
    handlers += [a, b]
    la, lb = _logger("a", a), _logger("b", b)

    def write(lg, tag):
        for i in range(300):
            lg.info("%s-%d %s", tag, i, LINE_PAD)

    threads = [
        threading.Thread(target=write, args=(la, "A")),
        threading.Thread(target=write, args=(lb, "B")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = sorted(_all_lines(log))
    expected = sorted([f"A-{i} {LINE_PAD}" for i in range(300)])
    expected += [f"B-{i} {LINE_PAD}" for i in range(300)]
    assert lines == sorted(expected)
    assert _over_cap(log, 2000) == {}


_WRITER = textwrap.dedent("""
    import logging, sys
    from gaia.log_rotation import SharedRotatingFileHandler

    log, tag, max_bytes, backups = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    if sys.argv[5] == "windows":
        import os
        from gaia import log_rotation

        real_replace = os.replace

        def replace(src, dst):
            if str(src) == log:
                raise PermissionError(32, "being used by another process", src)
            return real_replace(src, dst)

        log_rotation.os.replace = replace
        log_rotation._LINESEP = "\\r\\n"
    h = SharedRotatingFileHandler(log, max_bytes=max_bytes, backup_count=backups)
    def boom(record):
        raise SystemExit(f"handleError: {record.getMessage()}")
    h.handleError = boom
    lg = logging.getLogger("writer")
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    for i in range(800):
        lg.info("%s-%d %s", tag, i, "x" * (60 - i % 37))
    h.close()
    """)


def _writer_lines():
    return [f"{t}-{i} {'x' * (60 - i % 37)}" for t in ("P", "Q") for i in range(800)]


def _run_writers(tmp_path, log, max_bytes, backups, platform="native"):
    env = dict(os.environ)
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        GAIA_HOME=str(tmp_path / ".gaia"),
        GAIA_DAEMON_HOME=str(tmp_path / "daemon"),
        PYTHONPATH=str(SRC),
    )
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _WRITER,
                str(log),
                tag,
                str(max_bytes),
                str(backups),
                platform,
            ],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        for tag in ("P", "Q")
    ]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err


def test_two_processes_lose_nothing_when_backups_suffice(tmp_path):
    log = tmp_path / "shared" / "gaia.log"
    log.parent.mkdir()
    _run_writers(tmp_path, log, max_bytes=4000, backups=1000)

    assert sorted(_all_lines(log)) == sorted(_writer_lines())
    assert _over_cap(log, 4000) == {}


def test_two_processes_on_windows_semantics_stay_under_the_cap(tmp_path):
    """Rename refused and CRLF on disk in both writers; a small cap rotates often."""
    log = tmp_path / "shared" / "gaia.log"
    log.parent.mkdir()
    # Each rotation shifts every existing backup up a slot, so an uncapped backup
    # count makes per-rotation cost grow with how many have piled up. 1500 keeps
    # dozens of rotations (still exercising the CRLF byte-boundary math) without
    # that shift cost alone outrunning the lock's 5 s timeout.
    _run_writers(tmp_path, log, max_bytes=1500, backups=1000, platform="windows")

    assert sorted(_all_lines(log)) == sorted(_writer_lines())
    assert _over_cap(log, 1500) == {}
    assert b"\r\n" in log.read_bytes()


def test_two_processes_stay_bounded(tmp_path):
    log = tmp_path / "shared" / "gaia.log"
    log.parent.mkdir()
    _run_writers(tmp_path, log, max_bytes=4000, backups=2)

    family = log_family(log)
    assert len(family) <= 3
    assert sum(f.stat().st_size for f in family) <= 3 * 4000


def test_rename_refused_falls_back_to_copy_truncate(
    tmp_path, handlers, monkeypatch, capsys
):
    """Windows: another process holding gaia.log open makes the rename fail."""
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"
    real_replace = os.replace

    def replace(src, dst):
        if str(src) == str(log):
            raise PermissionError(32, "being used by another process", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(log_rotation.os, "replace", replace)
    h = SharedRotatingFileHandler(log, max_bytes=1000, backup_count=3)
    handlers.append(h)
    lg = _logger("copytrunc", h)
    for i in range(100):
        lg.info("line %d %s", i, LINE_PAD)

    family = log_family(log)
    assert len(family) == 4
    assert _over_cap(log, 1000) == {}
    assert log.read_text(encoding="utf-8").splitlines()[-1].startswith("line 99")
    err = capsys.readouterr().err
    assert err.count("copy + truncate") == 1


def test_rotation_impossible_pauses_file_logging_and_says_so_once(
    tmp_path, handlers, monkeypatch, capsys
):
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"

    def refuse(*_a, **_k):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(log_rotation.os, "replace", refuse)
    monkeypatch.setattr(log_rotation.shutil, "copyfile", refuse)
    h = SharedRotatingFileHandler(log, max_bytes=1000, backup_count=3)
    handlers.append(h)
    lg = _logger("stuck", h)
    for i in range(100):
        lg.info("line %d %s", i, LINE_PAD)

    assert log.stat().st_size <= 1000
    assert log_family(log) == [log]
    assert capsys.readouterr().err.count("cannot rotate") == 1


def test_oversized_existing_log_is_rotated_aside_not_deleted(
    tmp_path, handlers, capsys
):
    log = tmp_path / "gaia.log"
    legacy = b"old line\n" * 2000
    log.write_bytes(legacy)

    h = SharedRotatingFileHandler(log, max_bytes=1000, backup_count=3)
    handlers.append(h)
    _logger("legacy", h).info("fresh")

    assert (tmp_path / "gaia.log.1").read_bytes() == legacy
    assert log.read_text(encoding="utf-8").strip().endswith("fresh")
    assert "kept for bug reports" in capsys.readouterr().err


def test_huge_existing_log_held_open_is_not_copied(
    tmp_path, handlers, monkeypatch, capsys
):
    """Copying a multi-GB log under the lock would stall every writer."""
    _fail_on_handle_error(monkeypatch)
    log = tmp_path / "gaia.log"
    log.write_bytes(b"old line\n" * 2000)
    copied = []

    def refuse(*_a, **_k):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(log_rotation.os, "replace", refuse)
    monkeypatch.setattr(log_rotation.shutil, "copyfile", lambda *a: copied.append(a))
    h = SharedRotatingFileHandler(log, max_bytes=1000, backup_count=3)
    handlers.append(h)
    _logger("huge", h).info("not written while over the cap")

    assert not copied
    assert log.stat().st_size == 18000
    assert "cannot rotate" in capsys.readouterr().err


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "models a bare os.replace() by another writer, which Windows itself "
        "refuses while this handler has the file open (WinError 32) -- the "
        "same refusal the copy+truncate path exists for, covered instead by "
        "test_rename_refused_falls_back_to_copy_truncate"
    ),
)
def test_handler_follows_a_rotation_done_by_another_writer(tmp_path, handlers):
    log = tmp_path / "gaia.log"
    h = SharedRotatingFileHandler(log, max_bytes=10_000, backup_count=3)
    handlers.append(h)
    lg = _logger("follow", h)
    lg.info("before")
    os.replace(log, tmp_path / "gaia.log.1")
    lg.info("after")

    assert log.read_text(encoding="utf-8").strip() == "after"


def test_rotate_if_oversized(tmp_path):
    log = tmp_path / "daemon.log"
    assert rotate_if_oversized(log, max_bytes=10) is False
    log.write_bytes(b"12345")
    assert rotate_if_oversized(log, max_bytes=10) is False
    log.write_bytes(b"x" * 20)
    assert rotate_if_oversized(log, max_bytes=10) is True
    assert not log.exists()
    assert (tmp_path / "daemon.log.1").read_bytes() == b"x" * 20


def test_limits_from_env(monkeypatch):
    monkeypatch.delenv("GAIA_LOG_MAX_MB", raising=False)
    monkeypatch.delenv("GAIA_LOG_BACKUPS", raising=False)
    assert log_limits() == (10 * 1024 * 1024, 3)
    monkeypatch.setenv("GAIA_LOG_MAX_MB", "25")
    monkeypatch.setenv("GAIA_LOG_BACKUPS", "5")
    assert log_limits() == (25 * 1024 * 1024, 5)


@pytest.mark.parametrize("value", ["abc", "0", "-3", "1.5"])
def test_invalid_limit_env_is_a_loud_error(monkeypatch, value):
    monkeypatch.setenv("GAIA_LOG_MAX_MB", value)
    with pytest.raises(ValueError, match="GAIA_LOG_MAX_MB"):
        log_limits()


def test_diagnostics_bundles_rotated_logs(tmp_path, monkeypatch):
    from gaia import cli

    gaia_dir = tmp_path / ".gaia"
    gaia_dir.mkdir()
    (gaia_dir / "gaia.log").write_text("live\n", encoding="utf-8")
    (gaia_dir / "gaia.log.1").write_text("older\n", encoding="utf-8")
    (gaia_dir / "gaia.log.2").write_bytes(b"a" * 50 + b"TAIL")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cli, "_DIAG_MAX_LOG_BYTES", 20)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    out = tmp_path / "bundle.tgz"
    cli.handle_diagnostics_command(argparse.Namespace(output=str(out), no_logs=False))

    with tarfile.open(out) as tar:
        names = set(tar.getnames())
        assert {"gaia.log", "gaia.log.1", "gaia.log.2.tail"} <= names
        tail = tar.extractfile("gaia.log.2.tail").read()
    assert tail == b"a" * 16 + b"TAIL"
