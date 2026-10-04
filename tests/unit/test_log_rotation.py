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
    assert all(f.stat().st_size <= 1000 for f in family)
    assert log.read_text(encoding="utf-8").splitlines()[-1].startswith("line 199")


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
    assert all(f.stat().st_size <= 2000 for f in log_family(log))


_WRITER = textwrap.dedent("""
    import logging, sys
    from gaia.log_rotation import SharedRotatingFileHandler

    log, tag, max_bytes, backups = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    h = SharedRotatingFileHandler(log, max_bytes=max_bytes, backup_count=backups)
    def boom(record):
        raise SystemExit(f"handleError: {record.getMessage()}")
    h.handleError = boom
    lg = logging.getLogger("writer")
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    for i in range(800):
        lg.info("%s-%d %s", tag, i, "x" * 60)
    h.close()
    """)


def _run_writers(tmp_path, log, max_bytes, backups):
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

    lines = sorted(_all_lines(log))
    expected = [f"{t}-{i} {LINE_PAD}" for t in ("P", "Q") for i in range(800)]
    assert lines == sorted(expected)
    assert all(f.stat().st_size <= 4000 for f in log_family(log))


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
    assert all(f.stat().st_size <= 1000 for f in family)
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
