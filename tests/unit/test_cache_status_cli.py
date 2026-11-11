# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia cache status` presents Context7 as optional (#1090)."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import pytest

from gaia import cli


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / ".gaia"))


def _status(monkeypatch, available, caplog):
    from gaia.mcp.external_services import Context7Service

    monkeypatch.setattr(
        Context7Service, "check_availability", classmethod(lambda cls: available)
    )
    with caplog.at_level(logging.DEBUG):
        cli.handle_cache_command(argparse.Namespace(cache_action="status"))


def test_unavailable_context7_is_marked_optional(
    isolated_cache, monkeypatch, capsys, caplog
):
    _status(monkeypatch, False, caplog)
    out = capsys.readouterr().out
    assert "Context7 (optional) is not available" in out
    assert "UNAVAILABLE" not in out
    assert "To enable Context7" in out
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_available_context7_status_unchanged(
    isolated_cache, monkeypatch, capsys, caplog
):
    _status(monkeypatch, True, caplog)
    out = capsys.readouterr().out
    assert "Context7 is AVAILABLE" in out
    assert "To enable Context7" not in out


def test_missing_npx_logs_below_warning(monkeypatch, caplog):
    from gaia.mcp import external_services
    from gaia.mcp.external_services import Context7Service

    monkeypatch.setattr(Context7Service, "_availability_checked", False)
    monkeypatch.setattr(external_services.shutil, "which", lambda _name: None)
    with caplog.at_level(logging.DEBUG):
        assert Context7Service.check_availability() is False
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _write(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def _cache_lines(out):
    """The rows of the GAIA caches section, without the header."""
    section = out.split("=== GAIA caches", 1)[1].split("===", 2)[1]
    return [line.split() for line in section.strip().splitlines() if line.strip()]


def test_status_lists_every_cache_folder_with_its_size(
    isolated_cache, tmp_path, monkeypatch, capsys, caplog
):
    """#3221: status named only Context7 and printed no sizes."""
    cache = tmp_path / ".gaia" / "cache"
    _write(cache / "rag" / "doc.bin", 2048)
    _write(cache / "rag" / "nested" / "more.bin", 1024)
    _write(cache / "context7" / "library_x.json", 10)

    _status(monkeypatch, True, caplog)
    out = capsys.readouterr().out

    assert f"=== GAIA caches ({cache}) ===" in out
    assert _cache_lines(out) == [
        ["rag", "3.0", "KB", "2", "files"],
        ["context7", "10", "B", "1", "file"],
        ["Total", "3.0", "KB", "3", "files"],
    ]
    assert "Context7 is AVAILABLE" in out, "the Context7 status still follows"


def test_status_says_so_when_there_are_no_caches_yet(
    isolated_cache, monkeypatch, capsys, caplog
):
    _status(monkeypatch, True, caplog)

    assert "No caches yet." in capsys.readouterr().out


def test_status_does_not_follow_a_link_out_of_the_cache(
    isolated_cache, tmp_path, monkeypatch, capsys, caplog
):
    elsewhere = tmp_path / "models"
    _write(elsewhere / "huge.bin", 4096)
    cache = tmp_path / ".gaia" / "cache"
    _write(cache / "rag" / "doc.bin", 100)
    try:
        (cache / "models-link").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this platform cannot create a symlink without privileges")

    _status(monkeypatch, True, caplog)

    assert _cache_lines(capsys.readouterr().out) == [
        ["rag", "100", "B", "1", "file"],
        ["Total", "100", "B", "1", "file"],
    ]


def test_a_folder_that_cannot_be_read_is_counted_not_dropped(
    isolated_cache, tmp_path, monkeypatch, capsys, caplog
):
    cache = tmp_path / ".gaia" / "cache"
    _write(cache / "rag" / "doc.bin", 100)
    (cache / "rag" / "locked").mkdir()
    real_walk = os.walk

    # Root can read any folder, so refuse one the way os.walk reports it.
    def walk(top, onerror=None, **kwargs):
        for dirpath, dirnames, filenames in real_walk(top, onerror=onerror, **kwargs):
            if "locked" in dirnames:
                dirnames.remove("locked")
                onerror(PermissionError(13, "Permission denied", dirpath))
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(os, "walk", walk)

    _status(monkeypatch, True, caplog)

    assert _cache_lines(capsys.readouterr().out) == [
        ["rag", "100", "B", "1", "file", "(1", "unreadable)"],
        ["Total", "100", "B", "1", "file"],
    ]


def test_a_file_removed_while_listing_is_reported_and_the_rest_still_shows(
    isolated_cache, tmp_path, monkeypatch, capsys, caplog
):
    cache = tmp_path / ".gaia" / "cache"
    _write(cache / "rag" / "doc.bin", 100)
    _write(cache / "index.lock", 10)
    real_lstat = Path.lstat

    def lstat(self):
        if self.name == "index.lock":
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", lstat)

    _status(monkeypatch, True, caplog)

    out = capsys.readouterr().out
    assert _cache_lines(out) == [
        ["rag", "100", "B", "1", "file"],
        ["index.lock", "0", "B", "0", "files", "(1", "unreadable)"],
        ["Total", "100", "B", "1", "file"],
    ]
    assert "Context7" in out
