# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The MCP server list and the agents folder follow ``GAIA_HOME``.

A run isolated with ``GAIA_HOME`` must never read the user's own
``~/.gaia/mcp_servers.json`` (it would spawn their MCP servers) nor install into
or discover from the user's ``~/.gaia/agents``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.config import gaia_agents_dir, gaia_home, mcp_servers_path

_REAL_SERVER = {"mcpServers": {"users-own": {"command": "dead-server", "args": []}}}


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A fake user home holding a populated ``.gaia`` that must stay untouched."""
    home = tmp_path / "home"
    user_gaia = home / ".gaia"
    (user_gaia / "agents" / "users-agent").mkdir(parents=True)
    (user_gaia / "agents" / "users-agent" / "agent.py").write_text(
        "raise RuntimeError('user agent must not be loaded')\n", encoding="utf-8"
    )
    (user_gaia / "mcp_servers.json").write_text(
        json.dumps(_REAL_SERVER), encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    return home


@pytest.fixture
def isolated(fake_home, tmp_path, monkeypatch):
    target = tmp_path / "isolated-gaia"
    monkeypatch.setenv("GAIA_HOME", str(target))
    return target


# --- resolvers --------------------------------------------------------------


def test_resolvers_follow_gaia_home(isolated):
    assert mcp_servers_path() == isolated / "mcp_servers.json"
    assert gaia_agents_dir() == isolated / "agents"


def test_resolvers_default_to_user_home(fake_home):
    assert mcp_servers_path() == fake_home / ".gaia" / "mcp_servers.json"
    assert gaia_agents_dir() == fake_home / ".gaia" / "agents"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_gaia_home_falls_back_like_gaia_home(fake_home, monkeypatch, blank):
    monkeypatch.setenv("GAIA_HOME", blank)
    assert gaia_home() == fake_home / ".gaia"
    assert mcp_servers_path() == fake_home / ".gaia" / "mcp_servers.json"
    assert gaia_agents_dir() == fake_home / ".gaia" / "agents"


def test_gaia_home_set_after_import_is_honored(fake_home, tmp_path, monkeypatch):
    from gaia.connectors import mcp_server

    assert mcp_server._mcp_servers_path() == fake_home / ".gaia" / "mcp_servers.json"
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "late"))
    assert mcp_server._mcp_servers_path() == tmp_path / "late" / "mcp_servers.json"
    assert not hasattr(mcp_server, "_MCP_SERVERS_FILE")


# --- MCP server list --------------------------------------------------------


def test_mcp_config_never_reads_user_list(isolated, tmp_path, monkeypatch):
    from gaia.mcp.client.config import MCPConfig

    monkeypatch.chdir(tmp_path)  # no local ./mcp_servers.json
    config = MCPConfig()
    assert config.get_servers() == {}
    assert config.load_report["global"]["path"] == isolated / "mcp_servers.json"
    assert config.config_file == isolated / "mcp_servers.json"


def test_mcp_config_reads_the_isolated_list(isolated, tmp_path, monkeypatch):
    from gaia.mcp.client.config import MCPConfig

    isolated.mkdir(parents=True, exist_ok=True)
    (isolated / "mcp_servers.json").write_text(
        json.dumps({"mcpServers": {"mine": {"command": "x"}}}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    assert set(MCPConfig().get_servers()) == {"mine"}


def test_mcp_config_default_still_reads_user_list(fake_home, tmp_path, monkeypatch):
    from gaia.mcp.client.config import MCPConfig

    monkeypatch.chdir(tmp_path)
    assert set(MCPConfig().get_servers()) == {"users-own"}


def test_connectors_handler_reads_and_writes_isolated_list(isolated, fake_home):
    from gaia.connectors import mcp_server

    assert mcp_server._read_mcp_servers_json() == {}
    mcp_server._write_mcp_servers_json({"new": {"command": "y"}})
    assert json.loads((isolated / "mcp_servers.json").read_text("utf-8")) == {
        "mcpServers": {"new": {"command": "y"}}
    }
    assert (
        json.loads((fake_home / ".gaia" / "mcp_servers.json").read_text("utf-8"))
        == _REAL_SERVER
    )


def test_ui_router_reads_isolated_list(isolated):
    pytest.importorskip("fastapi")
    from gaia.ui.routers import mcp as mcp_router

    config = mcp_router._load_config()
    assert config.config_file == isolated / "mcp_servers.json"
    assert config.get_servers() == {}


def test_mcp_init_creates_list_under_gaia_home(isolated, fake_home):
    from gaia.installer.mcp_init import MCPInitCommand

    assert MCPInitCommand(yes=True).run() == 0
    assert json.loads((isolated / "mcp_servers.json").read_text("utf-8")) == {
        "mcpServers": {}
    }
    assert (
        json.loads((fake_home / ".gaia" / "mcp_servers.json").read_text("utf-8"))
        == _REAL_SERVER
    )


def test_chat_agent_mcp_gate_uses_gaia_home():
    """The chat agent's "is any MCP configured" gate must not probe ``~/.gaia``."""
    src = (
        Path(__file__).resolve().parents[2]
        / "hub/agents/chat/python/gaia_agent_chat/agent.py"
    ).read_text(encoding="utf-8")
    assert '_mcp_config_path = gaia_home() / "mcp_servers.json"' in src
    assert 'Path.home() / ".gaia" / "mcp_servers.json"' not in src


# --- agents folder ----------------------------------------------------------


def test_hub_install_root_follows_gaia_home(isolated):
    from gaia.hub.installer import agent_install_dir, default_install_root

    assert default_install_root() == isolated / "agents"
    assert agent_install_dir("email") == isolated / "agents" / "email"


def test_sidecar_cache_dir_follows_gaia_home(isolated):
    from gaia.daemon.sidecars.fetch import default_cache_dir

    assert default_cache_dir("email") == isolated / "agents" / "email"


def test_export_import_root_follows_gaia_home(isolated):
    from gaia.installer.export_import import _agents_root

    assert _agents_root() == (isolated / "agents").resolve()


def test_compatibility_checks_disk_under_gaia_home(isolated, monkeypatch):
    from gaia.hub import compatibility

    seen = []
    monkeypatch.setattr(
        compatibility, "detect_free_disk_gb", lambda p: seen.append(Path(p))
    )
    compatibility.check_compatibility({})
    assert seen == [isolated / "agents"]


def test_registry_never_discovers_user_agents(isolated, monkeypatch):
    from gaia.agents import registry as registry_mod

    loaded = []
    reg = registry_mod.AgentRegistry()
    monkeypatch.setattr(reg, "_load_from_dir", lambda d: loaded.append(d))
    monkeypatch.setattr(reg, "_prime_installed_wheel_agents_path", lambda: None)
    monkeypatch.setattr(reg, "_discover_installed_agents", lambda: None)
    monkeypatch.setattr(reg, "_discover_native_agents", lambda: None)
    (isolated / "agents" / "isolated-agent").mkdir(parents=True)
    reg.discover()
    assert [d.name for d in loaded] == ["isolated-agent"]


def test_registry_native_manifest_not_read_from_user_home(
    isolated, fake_home, monkeypatch
):
    from gaia.agents import registry as registry_mod

    (fake_home / ".gaia" / "agent-manifest.json").write_text(
        json.dumps({"agents": [{"id": "users-native", "name": "U"}]}),
        encoding="utf-8",
    )
    isolated.mkdir(parents=True, exist_ok=True)
    (isolated / "agent-manifest.json").write_text(
        json.dumps({"agents": [{"id": "iso-native", "name": "I"}]}),
        encoding="utf-8",
    )
    reg = registry_mod.AgentRegistry()
    reg._discover_native_agents()
    assert reg.get("users-native") is None
    native = reg.get("iso-native")
    assert native is not None
    assert native.agent_dir == isolated / "agents" / "iso-native"


def test_sidecar_log_dir_follows_gaia_home(isolated):
    from gaia import cli

    log_dir = isolated / "agents" / "email" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "sidecar-1234.log").write_text("x", encoding="utf-8")
    found = cli._resolve_sidecar_log("email")
    assert found is not None and found.parent == log_dir
