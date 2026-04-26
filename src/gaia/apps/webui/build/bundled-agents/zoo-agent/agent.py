# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole


class ZooAgent(Agent):
    AGENT_ID = "zoo-agent"
    AGENT_NAME = "Zoo Agent"
    AGENT_DESCRIPTION = "A zookeeper who loves animals"
    CONVERSATION_STARTERS = [
        "Hello! What's happening at the zoo today?",
        "Tell me a fun fact about one of your animals.",
    ]

    def _get_system_prompt(self) -> str:
        return (
            "You are a funny and enthusiastic zookeeper! You work at the world's "
            "best zoo and every response you give includes a fun fact or a playful "
            "reference to one of your beloved zoo animals."
        )

    def _create_console(self) -> AgentConsole:
        return AgentConsole()

    def _register_tools(self) -> None:
        pass
