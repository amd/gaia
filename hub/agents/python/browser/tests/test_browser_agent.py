# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Smoke tests for the standalone gaia-agent-browser package."""


def test_build_registration_shape():
    import gaia_agent_browser as m

    reg = m.build_registration()
    assert reg.id == "web"
    assert reg.namespaced_agent_id == "installed:web"
    assert reg.source == "installed"
    tier_names = [t.name for t in reg.model_tiers]
    assert tier_names == ["full", "lite"]


def test_can_import_agent():
    from gaia_agent_browser.agent import BrowserAgent, BrowserAgentConfig

    assert BrowserAgent is not None
    assert BrowserAgentConfig is not None


def test_discovered_when_installed():
    from gaia.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.discover()
    assert "web" in {a.id for a in reg.list()}
