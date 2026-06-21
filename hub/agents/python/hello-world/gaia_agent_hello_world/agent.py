# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""HelloWorldAgent — the smallest possible GAIA agent.

This is a *reference example*: a conversational agent with a system prompt and
no tools. It exists to show the minimum needed to build a GAIA agent that
plugs into the registry, the CLI, the Agent UI, and the generic REST/MCP
server.

Anatomy of a GAIA agent
-----------------------
1. Subclass :class:`gaia.agents.base.Agent`.
2. Set ``response_mode = "conversational"`` *before* ``super().__init__()`` so
   the agent replies in plain text instead of the planning JSON envelope.
3. Forward constructor kwargs to ``super().__init__()`` — the registry/UI host
   injects things like ``model_id`` and ``base_url`` here.
4. Implement :meth:`_get_system_prompt` to give the agent its personality.
5. Implement :meth:`_register_tools` — empty for a no-tool agent.

To make your own conversational agent, copy this file, rename the class, and
edit ``_SYSTEM_PROMPT``.
"""

from typing import Optional

from gaia.agents.base import Agent

# The system prompt is the agent's entire behavior here. Keep it focused — a
# good prompt describes the role, the tone, and any hard rules.
_SYSTEM_PROMPT = """\
You are GAIA's Hello World agent — a friendly greeter that demonstrates the
smallest possible GAIA agent.

Behavior:
- Greet the user warmly and answer their question in 1-3 short sentences.
- If asked what you are, explain that you are a minimal reference agent with
  no tools, meant as a starting point for building new GAIA agents.
- Keep replies concise and plain-spoken. Never invent capabilities you don't
  have (you have no tools).
"""


class HelloWorldAgent(Agent):
    """Minimal conversational agent with a system prompt and no tools."""

    # Hub-display metadata (read by the registry when this class is the
    # entry point target). The package's ``build_registration`` mirrors these.
    AGENT_ID = "hello-world"
    AGENT_NAME = "Hello World"
    AGENT_DESCRIPTION = (
        "Minimal conversational reference agent — the smallest possible " "GAIA agent"
    )
    CONVERSATION_STARTERS = [
        "Say hello",
        "What can a GAIA agent do?",
    ]

    # A small, fast default keeps the example responsive on modest hardware.
    DEFAULT_MODEL = "Gemma-4-E4B-it-GGUF"

    def __init__(self, model_id: Optional[str] = None, **kwargs):
        # "conversational" must be set before super().__init__() so the base
        # class composes a plain-text response format instead of the planning
        # JSON envelope.
        self.response_mode = "conversational"
        super().__init__(model_id=model_id or self.DEFAULT_MODEL, **kwargs)

    def _get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def _register_tools(self) -> None:
        """No tools. A conversational agent can be useful with a prompt alone."""
