# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for Agent Hub metadata in the /api/agents endpoint."""

import json
from pathlib import Path

import pytest

from gaia.agents.registry import AgentRegistration, AgentRegistry


class TestAgentRegistrationHubMetadata:
    """Verify that AgentRegistration carries Hub metadata fields."""

    def test_default_hub_fields(self):
        reg = AgentRegistration(
            id="test",
            name="Test Agent",
            description="A test agent",
            source="builtin",
            conversation_starters=[],
            factory=lambda **kw: None,
            agent_dir=None,
            models=[],
        )
        assert reg.category == "general"
        assert reg.tags == []
        assert reg.icon == ""
        assert reg.tools_count == 0
        assert reg.language == "python"

    def test_custom_hub_fields(self):
        reg = AgentRegistration(
            id="my-agent",
            name="My Agent",
            description="Does things",
            source="custom_python",
            conversation_starters=["Hello"],
            factory=lambda **kw: None,
            agent_dir=None,
            models=["Qwen3.5-35B"],
            category="productivity",
            tags=["email", "calendar"],
            icon="mail",
            tools_count=5,
            language="python",
        )
        assert reg.category == "productivity"
        assert reg.tags == ["email", "calendar"]
        assert reg.icon == "mail"
        assert reg.tools_count == 5

    def test_native_source_type(self):
        reg = AgentRegistration(
            id="native-agent",
            name="Native Agent",
            description="C++ agent",
            source="native",
            conversation_starters=[],
            factory=lambda **kw: None,
            agent_dir=None,
            models=[],
            language="cpp",
        )
        assert reg.source == "native"
        assert reg.language == "cpp"


class TestBuiltinAgentHubMetadata:
    """Verify framework + installed agents have Hub metadata populated.

    chat/doc migrated to the gaia-agent-chat wheel (#1102), so they are now
    discovered via the ``gaia.agent`` entry point rather than registered as
    builtins — the fixture runs full discovery and the chat/doc cases skip when
    the wheel is absent.
    """

    @pytest.fixture()
    def registry(self):
        r = AgentRegistry()
        r.discover()
        return r

    def test_chat_agent_metadata(self, registry):
        pytest.importorskip("gaia_agent_chat")
        reg = registry.get("chat")
        assert reg is not None
        assert reg.category == "conversation"
        assert "chat" in reg.tags
        assert reg.icon == "message-circle"
        assert reg.language == "python"

    def test_gaia_lite_resolves_to_doc_with_lite_tier(self, registry):
        pytest.importorskip("gaia_agent_chat")
        # #1162: gaia-lite is a legacy alias for the doc agent on the lite tier,
        # not a standalone registration.
        assert registry.canonical_id("gaia-lite") == "doc"
        reg = registry.get("gaia-lite")
        assert reg is not None
        assert reg.id == "doc"
        assert reg.category == "documents"
        lite = next(t for t in reg.model_tiers if t.name == "lite")
        assert lite.models
        assert lite.min_memory_gb == 5.0

    def test_builder_metadata(self, registry):
        reg = registry.get("builder")
        if reg is None:
            pytest.skip("BuilderAgent not available")
        assert reg.category == "infrastructure"
        assert reg.icon == "wrench"
        assert reg.tools_count == 1


class TestNativeAgentDiscovery:
    """Verify native agent discovery from agent-manifest.json."""

    def test_discover_native_agents_from_manifest(self, tmp_path, monkeypatch):
        manifest = {
            "manifest_version": 1,
            "agents": [
                {
                    "id": "health-agent",
                    "name": "Health Agent",
                    "description": "Monitors system health",
                    "version": "1.0.0",
                    "language": "cpp",
                    "toolsCount": 3,
                    "categories": ["monitoring", "system"],
                },
            ],
        }
        (tmp_path / ".gaia").mkdir(exist_ok=True)
        (tmp_path / ".gaia" / "agent-manifest.json").write_text(json.dumps(manifest))

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        registry = AgentRegistry()
        registry._discover_native_agents()

        reg = registry.get("health-agent")
        assert reg is not None
        assert reg.source == "native"
        assert reg.language == "cpp"
        assert reg.tools_count == 3
        assert reg.category == "monitoring"
        assert "system" in reg.tags
        assert reg.namespaced_agent_id == "native:health-agent"

    def test_no_manifest_file_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        registry = AgentRegistry()
        registry._discover_native_agents()
        assert len(registry.list()) == 0

    def test_native_factory_raises(self):
        with pytest.raises(RuntimeError, match="Native agents require"):
            AgentRegistry._noop_factory()


class TestAgentInfoApiModel:
    """Verify the Pydantic AgentInfo model includes Hub fields."""

    def test_hub_fields_serialize(self):
        from gaia.ui.models import AgentInfo

        info = AgentInfo(
            id="test",
            name="Test",
            description="desc",
            source="builtin",
            category="documents",
            tags=["rag"],
            icon="message-circle",
            tools_count=10,
            language="python",
        )
        data = info.model_dump()
        assert data["category"] == "documents"
        assert data["tags"] == ["rag"]
        assert data["icon"] == "message-circle"
        assert data["tools_count"] == 10
        assert data["language"] == "python"

    def test_native_source_type(self):
        from gaia.ui.models import AgentInfo

        info = AgentInfo(
            id="native",
            name="Native",
            description="desc",
            source="native",
            language="cpp",
        )
        assert info.source == "native"
        assert info.language == "cpp"


class TestConsumesMcpServersExposure:
    """The /api/agents serializer surfaces ``consumes_mcp_servers`` so the
    Settings "Active for" panel can list dynamic MCP consumers."""

    def test_reg_to_info_exposes_flag_for_chat(self):
        pytest.importorskip("gaia_agent_chat")
        from gaia.ui.routers.agents import _reg_to_info

        registry = AgentRegistry()
        registry.discover()
        info = _reg_to_info(registry.get("chat"))
        assert info.consumes_mcp_servers is True

    def test_reg_to_info_defaults_false_for_non_consumer(self):
        pytest.importorskip("gaia_agent_chat")
        from gaia.ui.routers.agents import _reg_to_info

        registry = AgentRegistry()
        registry.discover()
        info = _reg_to_info(registry.get("doc"))
        assert info.consumes_mcp_servers is False
