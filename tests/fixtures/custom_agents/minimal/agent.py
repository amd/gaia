# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Minimal custom agent fixture for reproducing issue #973.

Before the fix, copying this file to ``~/.gaia/agents/minimal/agent.py`` and
launching the Agent UI surfaced ``Agent error: Agent.__init__() got an
unexpected keyword argument 'rag_documents'`` on the very first message —
because the UI session layer injects ``rag_documents``, ``library_documents``,
``allowed_paths``, and ``ui_session_id`` into every ``create_agent`` call
and the bare ``super().__init__(**kwargs)`` below forwarded them straight
into the base ``Agent.__init__`` which rejects unknown kwargs.

After the fix (``python_factory`` filters kwargs against the target's
``__init__`` chain), the UI-injected session kwargs are silently dropped at
the registry boundary and the agent constructs cleanly. To verify locally:

    mkdir -p ~/.gaia/agents/minimal
    cp tests/fixtures/custom_agents/minimal/agent.py ~/.gaia/agents/minimal/
    gaia chat --ui

In the UI, switch to the ``minimal`` agent and send any prompt. The chat
should respond normally; tail ``~/.gaia/logs/ui.log`` for a debug-level
``registry: python_factory dropped ...`` line to confirm the filter is
active. Remove the file from ``~/.gaia/agents/`` afterwards so it doesn't
pollute the developer's local agent set.
"""

from gaia.agents.base.agent import Agent


class MinimalAgent(Agent):
    AGENT_ID = "minimal"
    AGENT_NAME = "Minimal"
    AGENT_DESCRIPTION = "Repro fixture for issue #973 — bare super().__init__"
    CONVERSATION_STARTERS = ["Say hello"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _get_system_prompt(self):
        return "You are a minimal test agent. Reply briefly to whatever the user asks."

    def _register_tools(self):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        _TOOL_REGISTRY.clear()
