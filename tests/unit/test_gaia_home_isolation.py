# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests never write the developer's real ``~/.gaia`` (see tests/unit/conftest.py)."""

import os
from pathlib import Path

from gaia import security
from gaia.config import gaia_home
from gaia.logger import LOG_FILE_ENV, log_manager

# Read at collection, before any test can patch the home.
_REAL_GAIA_HOME = Path.home() / ".gaia"


def _under(path: Path, root: Path) -> bool:
    path = os.path.normcase(os.path.realpath(path))
    root = os.path.normcase(os.path.realpath(root))
    return path == root or path.startswith(root + os.sep)


def test_gaia_home_is_not_the_real_one(monkeypatch):
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    assert not _under(gaia_home(), _REAL_GAIA_HOME)


def test_an_edit_backup_lands_outside_the_real_home(monkeypatch, tmp_path):
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    edited = tmp_path / "notes.txt"
    edited.write_text("before", encoding="utf-8")
    backup = security.backup_file(str(edited))
    assert backup is not None and Path(backup).exists()
    assert not _under(Path(backup), _REAL_GAIA_HOME)


def test_gaia_home_set_by_a_test_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "mine"))
    assert gaia_home() == tmp_path / "mine"


def test_a_moved_home_still_gets_its_own_dot_gaia(monkeypatch, tmp_path):
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert gaia_home() == tmp_path / ".gaia"


def test_the_log_file_is_not_the_real_one():
    assert os.environ.get(LOG_FILE_ENV)
    assert not _under(log_manager.log_file, _REAL_GAIA_HOME)
