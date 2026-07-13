# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole


class ExampleAgent(Agent):
    AGENT_ID = "example-agent"
    AGENT_NAME = "Example Agent"
    AGENT_DESCRIPTION = (
        "A bundled example agent that shows how installer-seeded custom agents work"
    )
    CONVERSATION_STARTERS = [
        "What are you an example of?",
        "How do I build my own agent like you?",
    ]

    def _get_system_prompt(self) -> str:
        return (
            "You are GAIA's bundled Example Agent — a working demonstration of a "
            "custom agent seeded by the installer. Explain plainly that you exist "
            "to show how custom agents are packaged and loaded, and point anyone "
            "who wants to build their own to "
            "https://amd-gaia.ai/docs/guides/custom-agent."
        )

    def _create_console(self) -> AgentConsole:
        return AgentConsole()

    def _register_tools(self) -> None:
        pass
