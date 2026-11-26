# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA SDK: the GAIA agent loop for your own program.

The loop runs in the native ``gaiacpp`` engine, started as a child process.
Your tools, system prompt and conversation stay in your process; this package
is the thin layer between the two.

    from gaia_sdk import Agent, Budget, ToolRefusal, tool

    @tool
    def get_order(order_number: str) -> dict:
        \"\"\"Look up an order by its number.\"\"\"
        return {"status": "shipped"}

    with Agent(model="my-model", base_url="https://api.example.com/v1",
               api_key_env="PROVIDER_API_KEY", system_prompt="You answer questions.",
               tools=[get_order]) as agent:
        turn = agent.run("Where is order 1001?", budget=Budget(model_calls=8))
        if turn.ok:
            print(turn.text)
"""

from gaia_sdk.agent import UNRESOLVED_CHECKS, Agent, Budget, Prices, Turn
from gaia_sdk.engine import PROTOCOL, AgentBusy, EngineError, EngineNotFound
from gaia_sdk.tools import Tool, ToolRefusal, tool

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "AgentBusy",
    "Budget",
    "EngineError",
    "EngineNotFound",
    "PROTOCOL",
    "Prices",
    "Tool",
    "ToolRefusal",
    "Turn",
    "UNRESOLVED_CHECKS",
    "tool",
]
