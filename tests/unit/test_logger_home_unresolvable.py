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


def _run_child(code, tmp_path, drop_home_vars=False):
    env = dict(os.environ)
    if drop_home_vars:
        for key in list(env):
            if key.upper() in {"USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME"}:
                del env[key]
    for key in ("TMP", "TEMP", "TMPDIR"):
        env[key] = str(tmp_path)
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


def test_home_log_file_is_none_when_home_unresolvable(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(_no_home))
    assert gaia_logger._home_log_file() is None


def test_logger_writes_to_tempdir_and_says_so(monkeypatch, clean_root, capsys):
    monkeypatch.setattr(Path, "home", classmethod(_no_home))
    gl = gaia_logger.GaiaLogger()
    assert gl.log_file == Path(tempfile.gettempdir()) / "gaia.log"
    assert "Home directory is not resolvable" in capsys.readouterr().err


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
