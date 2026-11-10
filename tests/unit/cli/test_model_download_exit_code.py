# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia download` must not exit 0 after printing "server not running" (#3829).

A script or CI step branches on the exit code, so a printed error with a
zero exit reads as success and the pipeline moves on without the models.
"""

import sys

import pytest

from gaia import cli


@pytest.fixture
def gaia_home(tmp_path, monkeypatch):
    home = tmp_path / "gaia_home"
    home.mkdir()
    monkeypatch.setenv("GAIA_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_FILE", raising=False)
    return home


def _run_main(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.mark.parametrize(
    "argv", [["download", "--agent", "chat"], ["download", "--clear-cache"]]
)
def test_download_exits_nonzero_when_the_server_is_unreachable(
    argv, gaia_home, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "check_lemonade_health", lambda *a, **k: False)
    code = _run_main(argv, monkeypatch)

    assert code == 1
    assert "not running or not accessible" in capsys.readouterr().err
