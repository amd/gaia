# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The ``home-assistant`` skill's own tools: connection status and discovery."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.fixtures.home_assistant.fake_ha import FakeHomeAssistant

TOOLS_PY = (
    Path(__file__).resolve().parents[2]
    / "hub"
    / "skills"
    / "home-assistant"
    / "tools.py"
)


@pytest.fixture
def tools(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("_ha_skill_tools", TOOLS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unwrapped(fn):
    return getattr(fn, "__wrapped__", fn)


def test_loaded_tools_mean_connected_and_point_at_sharing(tools, monkeypatch):
    from gaia.agents.base import tools as registry

    monkeypatch.setitem(
        registry._TOOL_REGISTRY, "mcp_home_assistant_homeassistant__GetLiveContext", {}
    )
    result = _unwrapped(tools.home_assistant_status)()
    assert result["connected"] is True and result["working"] is True
    assert "Expose" in result["message"]


def test_nothing_saved_says_where_to_connect(tools, monkeypatch):
    from gaia.agents.base import tools as registry

    for name in [
        n for n in registry._TOOL_REGISTRY if n.startswith("mcp_home_assistant_")
    ]:
        monkeypatch.delitem(registry._TOOL_REGISTRY, name)
    result = _unwrapped(tools.home_assistant_status)()
    assert result["connected"] is False
    assert "Settings › Smart home" in result["message"] and "/home" in result["message"]


@pytest.mark.allow_network
def test_find_reads_only_the_public_manifest(tools, monkeypatch):
    server = FakeHomeAssistant("secret")
    base = server.start()
    try:
        monkeypatch.setattr(tools, "_CANDIDATES", ("http://nothing.invalid:8123", base))
        result = _unwrapped(tools.find_home_assistant)()
    finally:
        server.stop()
    assert result["found"] == [base]
    assert [r["path"] for r in server.requests] == ["/manifest.json"]
    assert all("Authorization" not in r["headers"] for r in server.requests)


@pytest.mark.allow_network
def test_find_reports_nothing_found(tools, monkeypatch):
    monkeypatch.setattr(tools, "_CANDIDATES", ("http://nothing.invalid:8123",))
    result = _unwrapped(tools.find_home_assistant)()
    assert result["found"] == [] and "Ask the user" in result["message"]
