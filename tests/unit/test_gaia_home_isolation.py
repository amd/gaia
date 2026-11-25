# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests never write the developer's real ``~/.gaia`` (see tests/unit/conftest.py)."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from gaia import security
from gaia.config import gaia_home
from gaia.logger import LOG_FILE_ENV, configure

# The owning process (the CLI) installs logging; under these unit tests this
# module does the equivalent so the log-file location can be asserted.
log_manager = configure()

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


def test_session_env_never_points_at_the_real_home():
    """Leaves the environment alone so a GAIA_HOME inherited from the shell is
    what the session fixture has to handle."""
    for var in ("GAIA_HOME", "GAIA_CONFIG_DIR"):
        value = os.environ.get(var, "").strip()
        if value:
            assert not _under(Path(os.path.expanduser(value)), _REAL_GAIA_HOME)
    assert not _under(gaia_home(), _REAL_GAIA_HOME)


@pytest.mark.parametrize("var", ["GAIA_HOME", "GAIA_CONFIG_DIR"])
def test_real_home_exported_in_the_shell_is_dropped(var):
    """#8868: a shell export of the real ~/.gaia must not reach the tests."""
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env[var] = str(_REAL_GAIA_HOME)
    env.pop("PYTEST_ADDOPTS", None)
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/unit/test_gaia_home_isolation.py::"
            "test_session_env_never_points_at_the_real_home",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout


def test_mock_home_clears_inherited_gaia_home(mock_home):
    """mock_home fixture isolates gaia_home() even if GAIA_HOME was set."""
    assert gaia_home() == mock_home / ".gaia"


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


def _unresolvable(path, *_a, **_k):
    raise OSError(1920, "The file cannot be accessed by the system", path)


def test_a_broken_realpath_does_not_break_the_database_guard(monkeypatch, tmp_path):
    # Coverage opens its data file through the same guard; a raise here left the
    # test's patch in place for every later test on the worker.
    monkeypatch.setattr(os.path, "realpath", _unresolvable)
    sqlite3.connect(str(tmp_path / "scratch.db")).close()


def test_a_broken_realpath_still_keeps_tests_out_of_the_real_home(monkeypatch):
    monkeypatch.setattr(os.path, "realpath", _unresolvable)
    with pytest.raises(pytest.fail.Exception, match="real home"):
        sqlite3.connect(str(_REAL_GAIA_HOME / "never-created.db"))
    assert not (_REAL_GAIA_HOME / "never-created.db").exists()
