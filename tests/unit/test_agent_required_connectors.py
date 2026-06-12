# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-X1: ``REQUIRED_CONNECTORS`` discovery + agent integration smoke tests.

Two distinct paths exercised:

- **Built-in path**: a synthetic ``Agent`` subclass registered through
  ``_register_builtin_agents`` carries its scope claims through the registry
  with a ``builtin:`` namespaced id.

- **Custom-agent path**: an ``agent.py`` written under ``~/.gaia/agents/<id>/``
  is loaded via ``_load_python_agent``. Its ``REQUIRED_CONNECTORS`` survive
  the round-trip into ``AgentRegistration``, and the namespaced id is
  ``custom:<sha256-prefix>:<id>`` derived from the ``agent.py`` bytes.

Plus a security check: a custom agent claiming a built-in's reserved id raises
``ValueError`` (plan amendment A9).

Bridge tests live in ``tests/unit/connectors/test_agent_bridge.py``; this
file owns the registry-and-class-attribute path.
"""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from gaia.agents.base.agent import Agent
from gaia.agents.registry import _RESERVED_BUILTIN_IDS, AgentRegistry
from gaia.connectors.providers.base import ConnectorRequirement
from gaia.ui.models import AgentInfo

CUSTOM_AGENT_TEMPLATE = textwrap.dedent("""
    from typing import ClassVar, List
    from gaia.agents.base.agent import Agent
    from gaia.connectors.providers.base import ConnectorRequirement


    class FakeInboxAgent(Agent):
        AGENT_ID = "{agent_id}"
        AGENT_NAME = "Fake Inbox"
        AGENT_DESCRIPTION = "test fixture"
        CONVERSATION_STARTERS = []
        REQUIRED_CONNECTORS: ClassVar[List[ConnectorRequirement]] = [
            ConnectorRequirement(
                connector_id="google",
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                reason="needed to triage your Gmail inbox",
            ),
        ]

        def __init__(self, **kwargs):
            # Skip the heavy parent __init__ — this is a fixture for the
            # registry-and-class-attribute round-trip; we never run a query.
            pass

        def _register_tools(self):
            pass

        def get_system_prompt(self) -> str:
            return "fake"

        def step(self, *a, **k):
            return {{}}
    """)


class TestAgentBaseClassDefault:
    """The base Agent class declares an empty REQUIRED_CONNECTORS so any
    subclass that doesn't override it has a deterministic empty list."""

    def test_base_default_is_empty_list(self):
        assert Agent.REQUIRED_CONNECTORS == []
        assert isinstance(Agent.REQUIRED_CONNECTORS, list)


# Agents that intentionally declare REQUIRED_CONNECTORS — they exist to
# demonstrate or exercise the connectors framework and are exempt from the
# "no connector requirements for built-ins" invariant.
# - ``connectors-demo`` is the framework's reference consumer.
# - ``email`` (#962) is the first concrete provider — Gmail + Calendar.
_CONNECTOR_DEMO_AGENTS: frozenset[str] = frozenset({"connectors-demo", "email"})


class TestBuiltinPath:
    def test_chat_builder_have_empty_required_connections(self):
        registry = AgentRegistry()
        registry._register_builtin_agents()
        for reg in registry.list():
            if reg.id in _CONNECTOR_DEMO_AGENTS:
                continue
            assert reg.required_connections == [], (
                f"Built-in agent {reg.id} unexpectedly declares "
                f"required_connections={reg.required_connections}"
            )
            assert reg.namespaced_agent_id == f"builtin:{reg.id}"

    def test_reserved_ids_match_registered_builtins(self):
        registry = AgentRegistry()
        registry._register_builtin_agents()
        registered = {r.id for r in registry.list()}
        # Every reserved id must still belong to a real built-in: either it is
        # registered directly, or it is a legacy "-lite"/gaia-lite alias that
        # resolves to a registered agent (#1162 — lite variants are now a model
        # tier, not a separate card, but stay reserved so a custom agent can't
        # claim the old id and shadow alias resolution).
        for reserved in _RESERVED_BUILTIN_IDS:
            resolved = AgentRegistry._LEGACY_ID_ALIASES.get(reserved, reserved)
            assert resolved in registered, (
                f"Reserved id {reserved!r} resolves to {resolved!r}, which is "
                f"not a registered built-in — drop it from _RESERVED_BUILTIN_IDS "
                f"or restore the agent/alias."
            )


CUSTOM_MCP_CONSUMER_TEMPLATE = textwrap.dedent("""
    from typing import ClassVar
    from gaia.agents.base.agent import Agent


    class DynamicMcpAgent(Agent):
        AGENT_ID = "{agent_id}"
        AGENT_NAME = "Dynamic MCP"
        AGENT_DESCRIPTION = "test fixture"
        CONVERSATION_STARTERS = []
        CONSUMES_MCP_SERVERS: ClassVar[bool] = True

        def __init__(self, **kwargs):
            pass

        def _register_tools(self):
            pass

        def get_system_prompt(self) -> str:
            return "fake"

        def step(self, *a, **k):
            return {{}}
    """)


class TestConsumesMcpServers:
    """``CONSUMES_MCP_SERVERS`` widens the Settings "Active for" panel beyond
    static ``REQUIRED_CONNECTORS`` declarants to agents that load MCP servers
    dynamically (e.g. the chat agent)."""

    def test_base_default_is_false(self):
        assert Agent.CONSUMES_MCP_SERVERS is False
        assert isinstance(Agent.CONSUMES_MCP_SERVERS, bool)

    def test_builtin_chat_consumes_mcp_servers(self):
        # chat ships as the standalone gaia-agent-chat wheel (#1102), discovered
        # via the gaia.agent entry point — use discover(), not the builtin pass.
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        chat = registry.get("chat")
        assert chat is not None
        assert chat.consumes_mcp_servers is True

    def test_builtin_chat_registration_matches_class(self):
        # The lazy factory cannot import the chat module at discovery time, so
        # the registration hardcodes the flag. Guard it against the class-level
        # source of truth drifting apart.
        pytest.importorskip("gaia_agent_chat")
        from gaia_agent_chat.agent import ChatAgent

        registry = AgentRegistry()
        registry.discover()
        chat = registry.get("chat")
        assert ChatAgent.CONSUMES_MCP_SERVERS is True
        assert chat.consumes_mcp_servers == ChatAgent.CONSUMES_MCP_SERVERS

    def test_other_builtins_do_not_consume_mcp_servers(self):
        registry = AgentRegistry()
        registry._register_builtin_agents()
        for reg in registry.list():
            if reg.id == "chat":
                continue
            assert reg.consumes_mcp_servers is False, (
                f"Built-in agent {reg.id} unexpectedly sets "
                "consumes_mcp_servers=True"
            )

    def test_custom_agent_flag_round_trips(self, tmp_path, monkeypatch):
        agent_dir = tmp_path / ".gaia" / "agents" / "dyn-mcp"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text(
            CUSTOM_MCP_CONSUMER_TEMPLATE.format(agent_id="dyn_mcp")
        )
        monkeypatch.setattr("gaia.agents.registry.Path.home", lambda: tmp_path)

        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("dyn_mcp")
        assert reg is not None
        assert reg.consumes_mcp_servers is True

    def test_custom_agent_defaults_false(self, tmp_path, monkeypatch):
        # The REQUIRED_CONNECTORS fixture does not set the flag — it must
        # default to False (least privilege; the panel won't list it for
        # connectors it doesn't declare).
        agent_dir = tmp_path / ".gaia" / "agents" / "inbox-zero"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text(
            CUSTOM_AGENT_TEMPLATE.format(agent_id="inbox_zero")
        )
        monkeypatch.setattr("gaia.agents.registry.Path.home", lambda: tmp_path)

        registry = AgentRegistry()
        registry.discover()
        assert registry.get("inbox_zero").consumes_mcp_servers is False


class TestCustomAgentPath:
    def test_required_connections_round_trip(self, tmp_path, monkeypatch):
        agents_root = tmp_path / ".gaia" / "agents"
        agent_dir = agents_root / "inbox-zero"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text(
            CUSTOM_AGENT_TEMPLATE.format(agent_id="inbox_zero")
        )

        # Point Path.home() at tmp_path so the registry's discovery logic
        # finds our fixture agent.
        monkeypatch.setattr("gaia.agents.registry.Path.home", lambda: tmp_path)

        registry = AgentRegistry()
        registry.discover()

        reg = registry.get("inbox_zero")
        assert reg is not None
        assert reg.source == "custom_python"
        assert reg.namespaced_agent_id.startswith("custom:")
        assert reg.namespaced_agent_id.endswith(":inbox_zero")
        # 16-char sha256 prefix between the literal segments.
        prefix = reg.namespaced_agent_id.split(":")[1]
        assert len(prefix) == 16
        # Connection requirement preserved verbatim.
        assert len(reg.required_connections) == 1
        cr = reg.required_connections[0]
        assert isinstance(cr, ConnectorRequirement)
        assert cr.connector_id == "google"
        assert cr.scopes == ("https://www.googleapis.com/auth/gmail.readonly",)
        assert "Gmail inbox" in cr.reason

    def test_factory_sets_namespaced_id_on_instance(self, tmp_path, monkeypatch):
        agents_root = tmp_path / ".gaia" / "agents"
        agent_dir = agents_root / "inbox-zero"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text(
            CUSTOM_AGENT_TEMPLATE.format(agent_id="inbox_zero")
        )

        monkeypatch.setattr("gaia.agents.registry.Path.home", lambda: tmp_path)

        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("inbox_zero")

        # The factory wrapper attaches the namespaced id to the instance so
        # process_query reads it from there.
        instance = reg.factory()
        assert getattr(instance, "_gaia_namespaced_agent_id") == reg.namespaced_agent_id

    def test_origin_hash_changes_when_agent_py_changes(self, tmp_path, monkeypatch):
        # Different bytes of agent.py → different namespaced id. The user
        # then re-grants explicitly rather than inheriting the prior grant.
        agents_root = tmp_path / ".gaia" / "agents"
        agent_dir = agents_root / "inbox-zero"
        agent_dir.mkdir(parents=True)
        py_file = agent_dir / "agent.py"
        py_file.write_text(CUSTOM_AGENT_TEMPLATE.format(agent_id="inbox_zero"))

        monkeypatch.setattr("gaia.agents.registry.Path.home", lambda: tmp_path)
        r1 = AgentRegistry()
        r1.discover()
        ns1 = r1.get("inbox_zero").namespaced_agent_id

        # Re-write with different content (extra trailing comment).
        py_file.write_text(
            CUSTOM_AGENT_TEMPLATE.format(agent_id="inbox_zero") + "\n# changed\n"
        )

        r2 = AgentRegistry()
        r2.discover()
        ns2 = r2.get("inbox_zero").namespaced_agent_id

        assert ns1 != ns2

    def test_reserved_id_is_blocked(self, tmp_path, monkeypatch, caplog):
        # ``builder`` is the canonical reserved framework built-in (chat/doc/file
        # migrated to the gaia-agent-chat wheel, #1102, and are no longer
        # reserved). A custom agent must not be able to claim a reserved id.
        agents_root = tmp_path / ".gaia" / "agents"
        agent_dir = agents_root / "trojan"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text(
            CUSTOM_AGENT_TEMPLATE.format(agent_id="builder")
        )

        monkeypatch.setattr("gaia.agents.registry.Path.home", lambda: tmp_path)

        registry = AgentRegistry()
        # discover() should log a warning and skip the trojan agent — it must
        # not register under id "builder" and overwrite the built-in.
        registry._register_builtin_agents()  # registers built-in builder
        with caplog.at_level("WARNING"):
            registry.discover()

        builder_reg = registry.get("builder")
        # The built-in builder is the one that survives.
        assert builder_reg.source == "builtin"
        assert builder_reg.namespaced_agent_id == "builtin:builder"


class TestAgentInfoSerialization:
    def test_required_connections_round_trip(self):
        info = AgentInfo(
            id="inbox_zero",
            name="Inbox",
            description="x",
            source="custom_python",
            conversation_starters=[],
            models=[],
            required_connections=[
                {
                    "connector_id": "google",
                    "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
                    "reason": "test",
                }
            ],
            namespaced_agent_id="custom:abc:inbox_zero",
        )
        # Pydantic v2 model_dump_json round-trips cleanly.
        as_json = info.model_dump_json()
        round_tripped = AgentInfo.model_validate_json(as_json)
        assert round_tripped.required_connections == info.required_connections
        assert round_tripped.namespaced_agent_id == "custom:abc:inbox_zero"

    def test_default_required_connections_empty(self):
        info = AgentInfo(
            id="x",
            name="x",
            description="x",
            source="builtin",
        )
        assert info.required_connections == []
        assert info.namespaced_agent_id == ""
        assert info.consumes_mcp_servers is False

    def test_consumes_mcp_servers_round_trips(self):
        info = AgentInfo(
            id="chat",
            name="Chat",
            description="x",
            source="builtin",
            consumes_mcp_servers=True,
        )
        round_tripped = AgentInfo.model_validate_json(info.model_dump_json())
        assert round_tripped.consumes_mcp_servers is True

    def test_invalid_source_rejected(self):
        with pytest.raises(ValidationError):
            AgentInfo(
                id="x",
                name="x",
                description="x",
                source="not_a_source",  # type: ignore[arg-type]
            )
