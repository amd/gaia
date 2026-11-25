# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests never write the developer's real ``~/.gaia`` (see tests/unit/conftest.py)."""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from gaia import security
from gaia.config import gaia_home
from gaia.logger import LOG_FILE_ENV, configure
from tests.unit.real_home_guard import is_temporary_home

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


#: A run started with a temporary ``HOME`` has no developer install to protect,
#: so tests that hold out the *real* ``~/.gaia`` have no real home to hold out.
_HOME_IS_TEMP = is_temporary_home(Path.home())


@pytest.mark.skipif(
    _HOME_IS_TEMP,
    reason="a run with a temporary HOME has no real GAIA home to hold out",
)
def test_session_env_never_points_at_the_real_home():
    """Leaves the environment alone so a GAIA_HOME inherited from the shell is
    what the session fixture has to handle."""
    for var in ("GAIA_HOME", "GAIA_CONFIG_DIR"):
        value = os.environ.get(var, "").strip()
        if value:
            assert not _under(Path(os.path.expanduser(value)), _REAL_GAIA_HOME)
    assert not _under(gaia_home(), _REAL_GAIA_HOME)


@pytest.mark.parametrize("var", ["GAIA_HOME", "GAIA_CONFIG_DIR"])
@pytest.mark.skipif(
    _HOME_IS_TEMP,
    reason="a run with a temporary HOME has no real GAIA home to hold out",
)
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


@pytest.mark.skipif(
    _HOME_IS_TEMP,
    reason="a run with a temporary HOME has no real GAIA home to hold out",
)
def test_a_broken_realpath_still_keeps_tests_out_of_the_real_home(monkeypatch):
    monkeypatch.setattr(os.path, "realpath", _unresolvable)
    with pytest.raises(pytest.fail.Exception, match="real home"):
        sqlite3.connect(str(_REAL_GAIA_HOME / "never-created.db"))
    assert not (_REAL_GAIA_HOME / "never-created.db").exists()


def test_a_temporary_folder_is_recognized_as_a_test_home():
    temp = Path(tempfile.gettempdir())
    assert is_temporary_home(temp)
    assert is_temporary_home(temp / "some-test-home")
    assert not is_temporary_home(temp.parent)


_ROOT_CONFTEST_PROBE = """
import importlib.util
import sys

from pathlib import Path

spec = importlib.util.spec_from_file_location("gaia_root_conftest", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module._is_real_gaia_home(Path.home() / ".gaia"))
"""


def test_the_root_guard_leaves_a_temporary_home_alone(tmp_path):
    """#9021: a run started with a temporary HOME has no developer install."""
    home = tmp_path / "home"
    home.mkdir()
    repo = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, "-c", _ROOT_CONFTEST_PROBE, str(repo / "conftest.py")],
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.split() == ["False"]


def _run_child_suite(tmp_path, env):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "src"
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(
        f"""
import importlib.util
import sys

sys.path.insert(0, {str(repo)!r})
sys.path.insert(0, {str(src)!r})
from tests.unit.conftest import (  # noqa: F401
    _guard_real_gaia_home_writes,
    _isolate_gaia_home,
    _restore_environ,
    _session_gaia_home,
)

spec = importlib.util.spec_from_file_location("gaia_root_conftest", {str(repo)!r} + "/conftest.py")
gaia_root_conftest = importlib.util.module_from_spec(spec)
sys.modules["gaia_root_conftest"] = gaia_root_conftest
spec.loader.exec_module(gaia_root_conftest)
_guard_real_gaia_home = gaia_root_conftest._guard_real_gaia_home
""",
        encoding="utf-8",
    )
    (suite / "test_child.py").write_text(
        """
import sqlite3

import pytest
from pathlib import Path

from gaia.config import gaia_home
from tests.unit import conftest as unit_conftest
from tests.unit.real_home_guard import account_home


def test_a_write_under_the_home_a_run_started_with_is_allowed(monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(Path.home() / ".gaia"))
    assert gaia_home() == Path.home() / ".gaia"
    (gaia_home() / "cache").mkdir(parents=True, exist_ok=True)


def test_a_write_to_the_persons_real_gaia_home_is_still_refused():
    real_home = account_home()
    assert real_home is not None
    with pytest.raises(pytest.fail.Exception):
        sqlite3.connect(str(real_home / ".gaia" / "cache" / "probe.db"))
    assert not (real_home / ".gaia" / "cache" / "probe.db").exists()
""",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "."],
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def test_a_gaia_home_inside_a_temporary_home_is_kept(tmp_path):
    """#9021: a GAIA_HOME inside a temporary HOME is a test home, and stays writable."""
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    env.pop("GAIA_HOME", None)
    env.pop("GAIA_CONFIG_DIR", None)
    run = _run_child_suite(tmp_path, env)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "2 passed" in run.stdout
    assert (home / ".gaia" / "cache").exists()
