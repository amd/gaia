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
    """Verify builtin agents have Hub metadata populated."""

    @pytest.fixture()
    def registry(self):
        r = AgentRegistry()
        r._register_builtin_agents()
        return r

    def test_chat_agent_metadata(self, registry):
        reg = registry.get("chat")
        assert reg is not None
        assert reg.category == "conversation"
        assert "chat" in reg.tags
        assert reg.icon == "message-circle"
        assert reg.language == "python"

    def test_gaia_lite_metadata(self, registry):
        reg = registry.get("gaia-lite")
        assert reg is not None
        assert reg.category == "documents"
        assert "lightweight" in reg.tags
        assert reg.icon == "zap"
        assert reg.tools_count > 0

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
        (tmp_path / ".gaia" / "agent-manifest.json").write_text(
            json.dumps(manifest)
        )

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
