# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`import gaia` must survive a process with no resolvable home directory."""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from gaia import logger as gaia_logger

REPO_ROOT = Path(__file__).resolve().parents[2]

# Runs in a fresh interpreter so the import-time logger is really exercised.
_IMPORT_AND_LOG = (
    "from gaia.logger import get_logger, log_manager\n"
    "get_logger('gaia.probe').warning('probe-c38')\n"
    "print(log_manager.log_file)\n"
)
_HOME_RAISES = (
    "import pathlib\n"
    "def _no_home(cls):\n"
    "    raise RuntimeError('Could not determine home directory.')\n"
    "pathlib.Path.home = classmethod(_no_home)\n"
    "import gaia\n"
)


@pytest.fixture()
def clean_root():
    root = logging.getLogger()
    before = root.handlers[:]
    yield root
    for handler in root.handlers[:]:
        if handler not in before:
            handler.close()
    root.handlers[:] = before


def _no_home(cls):
    raise RuntimeError("Could not determine home directory.")


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
        os.path.realpath(b)
    )


def _run_child(code, tmp_path, drop_home_vars=False, extra_env=None):
    env = dict(os.environ)
    # The suite moves the log file; these tests are about the home fallback.
    env.pop(gaia_logger.LOG_FILE_ENV, None)
    if drop_home_vars:
        for key in list(env):
            if key.upper() in {"USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME"}:
                del env[key]
    for key in ("TMP", "TEMP", "TMPDIR"):
        env[key] = str(tmp_path)
    for key in ("GAIA_HOME", "GAIA_CONFIG_DIR"):
        env.pop(key, None)
    env.update(extra_env or {})
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(REPO_ROOT / "src"), env.get("PYTHONPATH")])
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _assert_logged_to_tempdir(result, tmp_path):
    assert result.returncode == 0, result.stderr
    reported = result.stdout.strip().splitlines()[-1]
    assert _same_path(reported, tmp_path / "gaia.log"), reported
    assert "probe-c38" in (tmp_path / "gaia.log").read_text(encoding="utf-8")
    assert "Home directory is not resolvable" in result.stderr


@pytest.fixture(autouse=True)
def _no_gaia_home_env(monkeypatch):
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)


def test_home_log_file_is_none_when_home_unresolvable(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(_no_home))
    assert gaia_logger._home_log_file() is None


def test_logger_writes_to_tempdir_and_says_so(monkeypatch, clean_root, capsys):
    monkeypatch.delenv(gaia_logger.LOG_FILE_ENV, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(_no_home))
    gl = gaia_logger.GaiaLogger()
    assert gl.log_file == Path(tempfile.gettempdir()) / "gaia.log"
    assert "Home directory is not resolvable" in capsys.readouterr().err


def test_gaia_log_file_moves_the_log_off_the_home(monkeypatch, clean_root, tmp_path):
    """No home lookup at all: the test suite relies on this to spare ~/.gaia."""
    monkeypatch.setenv(gaia_logger.LOG_FILE_ENV, str(tmp_path / "logs" / "g.log"))
    monkeypatch.setattr(Path, "home", classmethod(_no_home))
    gl = gaia_logger.GaiaLogger()
    logging.getLogger("gaia.probe").warning("probe-log-file")
    gl.file_handler.flush()
    assert gl.log_file == tmp_path / "logs" / "g.log"
    assert "probe-log-file" in gl.log_file.read_text(encoding="utf-8")


def test_import_gaia_succeeds_when_path_home_raises(tmp_path):
    result = _run_child(_HOME_RAISES + _IMPORT_AND_LOG, tmp_path)
    _assert_logged_to_tempdir(result, tmp_path)


@pytest.mark.skipif(
    sys.platform != "win32", reason="POSIX resolves home from pwd without env vars"
)
def test_import_gaia_without_windows_home_env_vars(tmp_path):
    """The real-world trigger: a service or curated subprocess environment."""
    result = _run_child("import gaia\n" + _IMPORT_AND_LOG, tmp_path, True)
    _assert_logged_to_tempdir(result, tmp_path)


def test_gaia_home_relocates_the_log(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "portable"))
    assert gaia_logger._home_log_file() == tmp_path / "portable" / "gaia.log"


def test_gaia_home_needs_no_resolvable_home_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(_no_home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    assert gaia_logger._home_log_file() == tmp_path / "gaia.log"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_gaia_home_is_ignored(monkeypatch, tmp_path, blank):
    monkeypatch.setenv("GAIA_HOME", blank)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert gaia_logger._home_log_file() == tmp_path / ".gaia" / "gaia.log"


def test_import_gaia_logs_under_gaia_home(tmp_path):
    """A fresh interpreter: the import-time logger resolves GAIA_HOME without a cycle."""
    home = tmp_path / "gh"
    result = _run_child(_IMPORT_AND_LOG, tmp_path, extra_env={"GAIA_HOME": str(home)})
    assert result.returncode == 0, result.stderr
    reported = result.stdout.strip().splitlines()[-1]
    assert _same_path(reported, home / "gaia.log"), reported
    assert "probe-c38" in (home / "gaia.log").read_text(encoding="utf-8")
