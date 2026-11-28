# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dev mode (``GAIA_GAIA_AGENT_MODE=dev``) with gaiacpp chosen as the agent.

Dev mode runs the Python agent from a checkout's source. gaiacpp is a program of
its own with no source launch, so the docs promise dev mode changes nothing for
it. Every caller must then start or attach to gaiacpp, wherever it runs from.
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import pytest

import gaia.cli as cli
import gaia.config as config_mod
import gaia.ui.sidecar.daemon_client as daemon_client
from gaia.config import GaiaConfig
from gaia.daemon.instance import DaemonInstance
from gaia.daemon.sidecars.errors import DevSrcDirResolutionError
from tests.unit.test_daemon_agents_routes import _TOY_A, _make_registry

ENSURE_PAYLOAD = {
    "agent_id": "gaia",
    "state": "running",
    "mode": "user",
    "pid": 111,
    "port": 55001,
    "base_url": "http://127.0.0.1:55001",
    "api_version": "2.4",
    "agent_version": "0.3.0",
    "dev_src_dir": None,
    "token": "sidecar-tok",
}


class _RecordingPost:
    def __init__(self, payload):
        self.status_code = 200
        self.headers = {}
        self._payload = payload
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(kwargs.get("json"))
        return self

    def json(self):
        return self._payload


def _inst():
    return DaemonInstance(pid=999, port=54321, token="daemon-tok", api_version="1.1")


@pytest.fixture(autouse=True)
def _outside_any_checkout(tmp_path, monkeypatch):
    """The Agent UI backend's cwd is the home folder, not a git work tree."""
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_GAIA_AGENT_MODE", "dev")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(home)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def _choose(harness: str) -> None:
    GaiaConfig.save_key("flagship_agent", harness)


def test_agent_ui_starts_gaiacpp_in_dev_mode_outside_a_checkout(monkeypatch):
    _choose("gaiacpp")
    monkeypatch.setattr(daemon_client, "start_or_attach", lambda **k: _inst())
    post = _RecordingPost(ENSURE_PAYLOAD)
    monkeypatch.setattr(daemon_client.requests, "post", post)

    handle = daemon_client.acquire_handle("gaia")

    assert handle.token == "sidecar-tok"
    assert post.calls == [{"mode": "dev"}]


def test_agent_ui_still_refuses_python_dev_mode_outside_a_checkout(monkeypatch):
    _choose("gaiapy")
    monkeypatch.setattr(daemon_client, "start_or_attach", lambda **k: _inst())

    with pytest.raises(DevSrcDirResolutionError, match="checkout root"):
        daemon_client.acquire_handle("gaia")


def test_cli_start_agent_dev_attaches_to_gaiacpp_outside_a_checkout(
    monkeypatch, capsys
):
    import requests

    import gaia.daemon.client as client_mod

    _choose("gaiacpp")
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    post = _RecordingPost(ENSURE_PAYLOAD)
    monkeypatch.setattr(requests, "post", post)

    cli._handle_daemon_start_agent(
        argparse.Namespace(agent_id="gaia", mode="dev", dev_src_dir=None)
    )

    assert post.calls == [{"mode": "dev"}]
    assert "❌" not in capsys.readouterr().out


def _flagship_registry(tmp_path):
    daemon_checkout = tmp_path / "checkout-a" / "hub" / "agents" / "gaia" / "python"
    spec = dataclasses.replace(
        _TOY_A,
        agent_id="gaia",
        mode_env_var="GAIA_GAIA_AGENT_MODE",
        dev_src_dir=daemon_checkout,
    )
    return _make_registry({"gaia": spec})


def test_daemon_does_not_compare_checkouts_for_gaiacpp(tmp_path):
    _choose("gaiacpp")
    other = tmp_path / "checkout-b" / "hub" / "agents" / "gaia" / "python"

    body = _flagship_registry(tmp_path).ensure(
        "gaia", mode="dev", dev_src_dir=str(other)
    )

    assert body["state"] == "running"


def test_daemon_still_compares_checkouts_for_the_python_agent(tmp_path):
    from gaia.daemon.sidecars.errors import ModeConflictError

    _choose("gaiapy")
    other = tmp_path / "checkout-b" / "hub" / "agents" / "gaia" / "python"

    with pytest.raises(ModeConflictError, match=str(Path(other).resolve())):
        _flagship_registry(tmp_path).ensure("gaia", mode="dev", dev_src_dir=str(other))
