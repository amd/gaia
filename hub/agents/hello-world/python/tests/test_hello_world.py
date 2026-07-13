# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the Hello World reference agent.

These tests mock the LLM and never contact a live Lemonade server, so they run
anywhere ``amd-gaia`` is importable. They show the recommended pattern for
testing a GAIA agent:

- ``build_registration()`` metadata is pure data — assert it directly.
- The agent class is constructed with the LLM client patched out and
  ``skip_lemonade=True`` so no server is required.
"""

from unittest.mock import MagicMock, patch

import gaia_agent_hello_world
from gaia_agent_hello_world.agent import HelloWorldAgent


def test_build_registration_metadata():
    """The entry-point registration advertises the agent to the registry."""
    reg = gaia_agent_hello_world.build_registration()

    assert reg.id == "hello-world"
    assert reg.name == "Hello World"
    assert reg.category == "examples"
    assert reg.tools_count == 0
    assert reg.namespaced_agent_id == "installed:hello-world"
    assert reg.models == ["Gemma-4-E4B-it-GGUF"]
    assert reg.conversation_starters  # non-empty


def test_factory_creates_agent_instance():
    """The registration factory builds a real HelloWorldAgent."""
    reg = gaia_agent_hello_world.build_registration()

    with patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()):
        agent = reg.factory(skip_lemonade=True)

    assert isinstance(agent, HelloWorldAgent)


def test_system_prompt_and_no_tools():
    """A no-tool conversational agent exposes its prompt and registers nothing."""
    # The tool registry is a process-global dict; clear it so this assertion
    # doesn't depend on which other agents were imported first in the session.
    from gaia.agents.base.tools import _TOOL_REGISTRY

    _TOOL_REGISTRY.clear()

    with patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()):
        agent = HelloWorldAgent(skip_lemonade=True)

    assert "Hello World agent" in agent.system_prompt
    assert agent.response_mode == "conversational"
    # No tools registered for this minimal example.
    assert agent._tools_registry == {}


def test_lazy_class_export():
    """The class is importable via the package's lazy ``__getattr__``."""
    assert gaia_agent_hello_world.HelloWorldAgent is HelloWorldAgent
