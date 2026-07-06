# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Smoke tests for the standalone gaia-agent-chat package.

ChatAgent ships under three prompt-profile ids — ``chat``/``doc``/``file`` —
each registered via its own ``gaia.agent`` entry point (#1102). These tests
assert the registration shape, lazy re-exports, and registry discovery without
constructing a full agent (which would need a live RAG/LLM backend).
"""


def test_build_chat_registration_shape():
    import gaia_agent_chat as m

    reg = m.build_chat()
    assert reg.id == "chat"
    assert reg.source == "installed"
    # The chat profile loads MCP servers dynamically, so the registration must
    # advertise that to the connectors activation panel (#1005).
    assert reg.consumes_mcp_servers is True
    tier_names = [t.name for t in reg.model_tiers]
    assert tier_names == ["full", "lite"]


def test_build_doc_registration_shape():
    import gaia_agent_chat as m

    reg = m.build_doc()
    assert reg.id == "doc"
    assert reg.source == "installed"
    tier_names = [t.name for t in reg.model_tiers]
    assert tier_names == ["full", "lite"]


def test_build_file_registration_shape():
    import gaia_agent_chat as m

    reg = m.build_file()
    assert reg.id == "file"
    assert reg.source == "installed"
    tier_names = [t.name for t in reg.model_tiers]
    assert tier_names == ["full", "lite"]


def test_lazy_reexports():
    from gaia_agent_chat import ChatAgent, ChatAgentConfig, ChatAgentLite

    assert ChatAgent is not None
    assert ChatAgentConfig is not None
    assert ChatAgentLite is not None


def test_config_defaults():
    from gaia_agent_chat import ChatAgentConfig

    cfg = ChatAgentConfig()
    assert cfg.use_claude is False
    assert cfg.model_id is None
    assert cfg.prompt_profile == "full"


def test_discovered_when_installed():
    from gaia.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.discover()
    ids = {a.id for a in reg.list()}
    assert {"chat", "doc", "file"} <= ids


def test_discovery_stamps_installed_namespace():
    from gaia.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.discover()
    chat = reg.get("chat")
    assert chat is not None
    assert chat.source == "installed"
    assert chat.namespaced_agent_id == "installed:chat"
