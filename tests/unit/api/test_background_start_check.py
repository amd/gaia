# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A background start reports a child that died at once, not success."""

import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from gaia import cli
from gaia.api import app as api_app


def _child(exit_code):
    """A Popen stand-in that exits with *exit_code*, or keeps running if None."""
    proc = MagicMock(pid=4242)
    if exit_code is None:
        proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 0)
    else:
        proc.wait.return_value = exit_code
    return proc


@pytest.fixture
def api_start(mocker, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(api_app, "api_unavailable_reason", return_value=None)
    mocker.patch.object(api_app, "warn_if_unconfirmed_tools_allowed")
    mocker.patch.object(api_app, "assert_bind_is_authenticated")
    monkeypatch.setattr(api_app, "STARTUP_CHECK_SECONDS", 0)
    for name in ("GAIA_API_DEBUG", "GAIA_API_SHOW_PROMPTS", "GAIA_API_STREAMING"):
        monkeypatch.delenv(name, raising=False)

    def start(exit_code):
        mocker.patch.object(api_app.subprocess, "Popen", return_value=_child(exit_code))
        api_app.start_server("localhost", 8080, background=True)

    return start


class TestApiBackgroundStart:
    def test_a_child_that_exits_is_reported_with_the_log(self, api_start, capsys):
        with pytest.raises(SystemExit) as exc:
            api_start(1)
        out = capsys.readouterr().out
        assert exc.value.code == 1
        assert "exited during startup (exit code 1)" in out
        assert "gaia_api.log" in out
        assert "started in background" not in out

    def test_a_child_still_running_is_reported_as_started(self, api_start, capsys):
        api_start(None)
        assert "✅ GAIA API server started in background (PID: 4242)" in (
            capsys.readouterr().out
        )


@pytest.fixture
def mcp_start(mocker, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "MCP_STARTUP_CHECK_SECONDS", 0)
    stub = MagicMock()
    monkeypatch.setitem(sys.modules, "gaia.mcp.mcp_bridge", stub)
    # Nothing listens on the port, so the already-running check passes.
    sock = MagicMock()
    sock.connect_ex.return_value = 1
    mocker.patch("socket.socket", return_value=sock)

    def start(exit_code):
        mocker.patch.object(cli.subprocess, "Popen", return_value=_child(exit_code))
        monkeypatch.setattr(
            sys,
            "argv",
            ["gaia", "mcp", "start", "--background", "--no-lemonade-check"],
        )
        try:
            cli.main()
        except SystemExit as e:
            return e.code or 0
        return 0

    return start


class TestMcpBackgroundStart:
    def test_a_child_that_exits_is_reported_with_the_log(
        self, mcp_start, capsys, tmp_path
    ):
        assert mcp_start(2) == 1
        out = capsys.readouterr().out
        assert "MCP bridge exited during startup (exit code 2)" in out
        assert "gaia.mcp.log for the error" in out
        assert "started in background" not in out
        assert not (tmp_path / "gaia.mcp.pid").exists()

    def test_a_child_still_running_is_reported_as_started(
        self, mcp_start, capsys, tmp_path
    ):
        assert mcp_start(None) == 0
        assert "✅ MCP bridge started in background" in capsys.readouterr().out
        assert (tmp_path / "gaia.mcp.pid").read_text(encoding="utf-8") == "4242"
