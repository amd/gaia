# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Smoke tests for the standalone gaia-agent-email package."""


def test_build_registration_shape():
    import gaia_agent_email as m

    reg = m.build_registration()
    assert reg.id == "email"
    assert reg.name == "Email Triage"
    assert reg.namespaced_agent_id == "installed:email"
    assert reg.source == "installed"
    assert reg.tags == ["email", "gmail", "calendar", "triage"]
    assert reg.icon == "mail"
    # Provider-superset connector list — Google + Microsoft (#962, #1275).
    connector_ids = {c.connector_id for c in reg.required_connections}
    assert connector_ids == {"google", "microsoft"}


def test_conversation_starters_match_agent():
    import gaia_agent_email as m
    from gaia_agent_email.agent import EmailTriageAgent

    reg = m.build_registration()
    assert reg.conversation_starters == list(EmailTriageAgent.CONVERSATION_STARTERS)
    assert reg.name == EmailTriageAgent.AGENT_NAME
    assert reg.description == EmailTriageAgent.AGENT_DESCRIPTION


def test_required_connectors_match_agent():
    import gaia_agent_email as m
    from gaia_agent_email.agent import EmailTriageAgent

    reg = m.build_registration()
    built = [(c.connector_id, tuple(c.scopes), c.reason) for c in reg.required_connections]
    expected = [
        (c.connector_id, tuple(c.scopes), c.reason)
        for c in EmailTriageAgent.REQUIRED_CONNECTORS
    ]
    assert built == expected


def test_can_import_agent():
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    assert EmailTriageAgent is not None
    assert EmailAgentConfig is not None


def test_contract_import_is_light():
    # The contract module must import without dragging in the agent/backends —
    # the REST + MCP surfaces depend on this (#1229, #1104).
    from gaia_agent_email.contract import EmailTriageRequest, EmailTriageResponse

    assert EmailTriageRequest is not None
    assert EmailTriageResponse is not None


def test_discovered_when_installed():
    from gaia.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.discover()
    assert "email" in {a.id for a in reg.list()}
