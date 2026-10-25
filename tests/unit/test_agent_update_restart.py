# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An install that replaces a running agent's binary stops that agent (#5723).

The running sidecar keeps executing the old binary, so an upgrade answered
with the previous release's agent until the daemon happened to restart it.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia import cli
from gaia.daemon import client
from gaia.daemon.errors import DaemonError
from gaia.hub import installer
from gaia.installer.init_command import InitCommand


class _Response:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(self._body)

    def json(self):
        return self._body


@pytest.fixture
def daemon(monkeypatch):
    """A running daemon whose agent list and stop route the test controls."""
    import requests

    state = {"agents": [], "stop_status": 200, "stopped": []}
    monkeypatch.setattr(
        client, "attach", lambda: SimpleNamespace(base_url="http://d", token="t")
    )

    def get(url, headers=None, timeout=None):
        assert url == "http://d/daemon/v1/agents"
        assert headers == {"Authorization": "Bearer t"}
        return _Response(200, {"agents": state["agents"]})

    def post(url, headers=None, timeout=None):
        state["stopped"].append(url)
        return _Response(state["stop_status"], {"detail": "pid survived"})

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    return state


def test_a_running_agent_is_stopped(daemon):
    daemon["agents"] = [{"agent_id": "gaia", "state": "running"}]

    assert client.stop_running_agent("gaia") is True
    assert daemon["stopped"] == ["http://d/daemon/v1/agents/gaia/stop"]


def test_a_stopped_agent_is_left_alone(daemon):
    daemon["agents"] = [{"agent_id": "gaia", "state": "stopped"}]

    assert client.stop_running_agent("gaia") is False
    assert daemon["stopped"] == []


def test_no_daemon_means_nothing_to_stop(monkeypatch):
    monkeypatch.setattr(client, "attach", lambda: None)

    assert client.stop_running_agent("gaia") is False


def test_a_stop_that_fails_says_the_old_version_is_still_running(daemon):
    daemon["agents"] = [{"agent_id": "gaia", "state": "running"}]
    daemon["stop_status"] = 500

    with pytest.raises(DaemonError, match="previous version"):
        client.stop_running_agent("gaia")


def _result(updated):
    return SimpleNamespace(
        id="gaia",
        version="0.3.1",
        path=Path.home() / ".gaia" / "agents" / "gaia",
        language="python",
        updated=updated,
        hot_registered=False,
    )


def _args():
    return Namespace(
        agent_action="install", agent_id="gaia", version=None, trusted=True
    )


def test_cli_update_stops_the_running_agent(monkeypatch, capsys):
    monkeypatch.setattr(installer, "install", lambda *a, **k: _result(updated=True))
    monkeypatch.setattr(client, "stop_running_agent", lambda agent_id: True)

    cli.handle_agent_install(_args())

    out = capsys.readouterr().out
    assert "Stopped the running 'gaia' agent; your next chat uses v0.3.1." in out
    assert "start-agent" not in out


def test_cli_first_install_never_touches_the_daemon(monkeypatch, capsys):
    monkeypatch.setattr(installer, "install", lambda *a, **k: _result(updated=False))

    def must_not_run(agent_id):
        raise AssertionError("a first install has no old agent to stop")

    monkeypatch.setattr(client, "stop_running_agent", must_not_run)

    cli.handle_agent_install(_args())

    assert "gaia daemon start-agent gaia" in capsys.readouterr().out


def test_cli_exits_loudly_when_the_old_agent_keeps_running(monkeypatch, capsys):
    monkeypatch.setattr(installer, "install", lambda *a, **k: _result(updated=True))

    def refuse(agent_id):
        raise DaemonError("the daemon could not stop the 'gaia' agent")

    monkeypatch.setattr(client, "stop_running_agent", refuse)

    with pytest.raises(SystemExit) as exc:
        cli.handle_agent_install(_args())

    assert exc.value.code == 1
    assert "could not stop" in capsys.readouterr().err


def _init():
    cmd = InitCommand.__new__(InitCommand)
    cmd.console = None
    cmd.printed = []
    cmd._print = cmd.printed.append
    return cmd


def test_init_stops_the_agent_an_update_replaced(monkeypatch):
    monkeypatch.setattr(client, "stop_running_agent", lambda agent_id: True)
    cmd = _init()

    cmd._restart_replaced_agent("gaia", _result(updated=True))

    assert any("next chat uses 0.3.1" in line for line in cmd.printed)


def test_init_fails_the_setup_when_the_old_agent_keeps_running(monkeypatch):
    def refuse(agent_id):
        raise DaemonError("the daemon could not stop the 'gaia' agent")

    monkeypatch.setattr(client, "stop_running_agent", refuse)

    with pytest.raises(installer.InstallError, match="could not stop"):
        _init()._restart_replaced_agent("gaia", _result(updated=True))
