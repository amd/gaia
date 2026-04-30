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


class TestBuiltinPath:
    def test_chat_builder_have_empty_required_connections(self):
        registry = AgentRegistry()
        registry._register_builtin_agents()
        for reg in registry.list():
            assert reg.required_connections == [], (
                f"Built-in agent {reg.id} unexpectedly declares "
                f"required_connections={reg.required_connections}"
            )
            assert reg.namespaced_agent_id == f"builtin:{reg.id}"

    def test_reserved_ids_match_registered_builtins(self):
        registry = AgentRegistry()
        registry._register_builtin_agents()
        registered = {r.id for r in registry.list()}
        # Every reserved id is actually registered. (If we ever drop one,
        # the reserved set must drop with it — otherwise custom agents are
        # blocked from a name that no longer belongs to anyone.)
        assert _RESERVED_BUILTIN_IDS <= registered


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
        agents_root = tmp_path / ".gaia" / "agents"
        agent_dir = agents_root / "trojan"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text(
            CUSTOM_AGENT_TEMPLATE.format(agent_id="chat")
        )

        monkeypatch.setattr("gaia.agents.registry.Path.home", lambda: tmp_path)

        registry = AgentRegistry()
        # discover() should log a warning and skip the trojan agent — it must
        # not register under id "chat" and overwrite the built-in.
        registry._register_builtin_agents()  # registers built-in chat
        with caplog.at_level("WARNING"):
            registry.discover()

        chat_reg = registry.get("chat")
        # The built-in chat is the one that survives.
        assert chat_reg.source == "builtin"
        assert chat_reg.namespaced_agent_id == "builtin:chat"


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
                    "provider": "google",
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

    def test_invalid_source_rejected(self):
        with pytest.raises(ValidationError):
            AgentInfo(
                id="x",
                name="x",
                description="x",
                source="not_a_source",  # type: ignore[arg-type]
            )
