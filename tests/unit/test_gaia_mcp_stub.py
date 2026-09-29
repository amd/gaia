# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The ``gaia_mcp`` stub servers, driven through GAIA's real MCP client.

The scenarios in ``eval/scenarios/gaia_mcp/`` only prove something if the
staged config actually reaches the flagship: the servers connect through
``MCPClientManager``, their tools surface under the names the scenarios assert
(``mcp_acme_orders_lookup_order`` …), they are active for ``installed:gaia``,
and they return the planted values in ``GAIA_FIXTURE_VALUES.md``. Each of those
is a real subprocess here, not a stub of the stub.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from gaia.connectors.activations import activate_agent, is_agent_active
from gaia.mcp.client.config import MCPConfig
from gaia.mcp.client.mcp_client_manager import MCPClientManager

STUB_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "gaia" / "mcp_stub"
SCENARIOS = Path(__file__).resolve().parents[2] / "eval" / "scenarios" / "gaia_mcp"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, STUB_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = _load("stage_mcp_stub")
stub = _load("acme_mcp")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    # MCPConfig overlays ./mcp_servers.json; keep the repo's cwd out of it.
    monkeypatch.chdir(tmp_path)
    return home


@pytest.fixture
def staged_manager(fake_home):
    stage.install()
    manager = MCPClientManager(config=MCPConfig())
    manager.load_from_config()
    yield manager
    manager.disconnect_all()


def _tool_text(result: dict) -> str:
    return "".join(c["text"] for c in result["content"] if c["type"] == "text")


def test_staged_servers_are_active_for_the_flagship_only(staged_manager):
    assert sorted(staged_manager.servers_for_agent("installed:gaia")) == [
        "acme_orders",
        "acme_shipping",
    ]
    assert staged_manager.servers_for_agent("installed:chat") == []


def test_tools_surface_under_the_names_the_scenarios_assert(staged_manager):
    names = {
        tool.to_gaia_format(client.prefix, client.name)["name"]
        for server in staged_manager.list_servers()
        for client in [staged_manager.get_client(server)]
        for tool in client.list_tools()
    }
    assert names == {
        "mcp_acme_orders_lookup_order",
        "mcp_acme_shipping_track_shipment",
    }
    scenario_text = "".join(
        p.read_text(encoding="utf-8") for p in SCENARIOS.glob("*.yaml")
    )
    for name in names:
        assert name in scenario_text


def test_lookup_order_returns_the_planted_record(staged_manager):
    client = staged_manager.get_client("acme_orders")
    result = client.call_tool("lookup_order", {"order_id": "acme-40417"})
    assert result["isError"] is False
    record = json.loads(_tool_text(result))
    assert record["status"] == "shipped"
    assert record["carrier"] == "Parcelwing"
    assert record["tracking_id"] == "PW-7731-QX"
    assert record["items"] == [{"sku": "WG-220", "name": "Widget Gearbox", "qty": 3}]
    assert record["total_usd"] == 412.50


def test_lookup_order_reports_an_unknown_order_as_an_error(staged_manager):
    result = staged_manager.get_client("acme_orders").call_tool(
        "lookup_order", {"order_id": "ACME-99999"}
    )
    assert result["isError"] is True
    assert "No order found" in _tool_text(result)


def test_track_shipment_always_fails_with_no_tracking_data(staged_manager):
    result = staged_manager.get_client("acme_shipping").call_tool(
        "track_shipment", {"tracking_id": "PW-7731-QX"}
    )
    assert result == {"error": stub.SHIPPING_DOWN_MESSAGE}


def test_install_and_remove_leave_other_servers_and_activations_alone(fake_home):
    config = fake_home / ".gaia" / "mcp_servers.json"
    config.parent.mkdir(parents=True)
    other = {"command": "npx", "args": ["some-server"]}
    config.write_text(json.dumps({"mcpServers": {"mine": other}}), encoding="utf-8")
    activate_agent("mine", "installed:gaia")

    stage.install()
    stage.install()  # idempotent
    servers = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"mine", "acme_orders", "acme_shipping"}

    stage.remove()
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "mcpServers": {"mine": other}
    }
    assert is_agent_active("mine", "installed:gaia")
    assert not is_agent_active("acme_orders", "installed:gaia")
    assert not is_agent_active("acme_shipping", "installed:gaia")


def test_install_refuses_a_malformed_config_instead_of_overwriting_it(fake_home):
    config = fake_home / ".gaia" / "mcp_servers.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit, match="not valid JSON"):
        stage.install()
    assert config.read_text(encoding="utf-8") == "{not json"
