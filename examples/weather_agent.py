#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Weather Agent Example

A simple agent that provides real-time weather information using an MCP
weather server. Demonstrates how to compose ``Agent`` with
``MCPClientMixin`` and connect to a stdio-based MCP server at runtime.

Requirements:
- Python 3.12+
- uv (which provides `uvx` for launching the MCP server on demand)
- Lemonade server running for LLM reasoning

Run:
    uv run examples/weather_agent.py

Examples:
    You: What's the weather in Austin, Texas?
    You: Will it rain in Seattle tomorrow?
    You: What's the temperature in Tokyo?
"""

from gaia import Agent
from gaia.mcp import MCPClientMixin
from gaia.mcp.client.config import MCPConfig
from gaia.mcp.client.mcp_client_manager import MCPClientManager


class WeatherAgent(Agent, MCPClientMixin):
    """Agent that provides weather information via an MCP weather server."""

    # Connection spec for the open-meteo-mcp server — a free, no-API-key
    # weather server that wraps the Open-Meteo API.  ``uvx`` downloads and
    # runs it on demand, so no manual install step is required.
    WEATHER_SERVER = {
        "name": "weather",
        "config": {
            "command": "uvx",
            "args": ["--from", "open-meteo-mcp", "open_meteo_mcp"],
        },
    }

    def __init__(self, **kwargs):
        """Initialize the Weather Agent.

        The MCP client manager has to be wired up BEFORE ``super().__init__()``
        because :py:meth:`Agent.__init__` calls ``_register_tools``, which in
        turn triggers the MCP connection.
        """
        # Create an MCP client manager without loading any shared config so the
        # agent only sees the MCP server we attach below.
        self._mcp_manager = MCPClientManager(config=MCPConfig(config_file=None))

        # Use the compact 4B model for faster local inference. Extra kwargs are
        # forwarded to Agent (e.g. ``max_steps``, ``debug``).
        kwargs.setdefault("model_id", "Qwen3-4B-Instruct-2507-GGUF")
        kwargs.setdefault("max_steps", 10)
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return """You are a helpful weather assistant.

Use the available MCP weather tools to provide accurate, real-time weather information.
When users ask about weather, use the tools to get current conditions and forecasts.

Be conversational and helpful. Include relevant details like temperature, conditions,
and any weather alerts if available."""

    def _register_tools(self) -> None:
        """Connect to the weather MCP server and register its tools.

        ``Agent.__init__`` calls this after the client manager is wired up, so
        the MCP tools are available for the first user query.
        """
        print("Connecting to MCP weather server...")
        success = self.connect_mcp_server(
            self.WEATHER_SERVER["name"], self.WEATHER_SERVER["config"]
        )
        if success:
            print("  ✅ Connected to weather MCP server")
        else:
            print("  ❌ Failed to connect to weather MCP server")
            print("  Make sure `uv` is installed so `uvx` can fetch open-meteo-mcp.")


def main():
    """Run the Weather Agent interactively."""
    print("=" * 60)
    print("Weather Agent - Real-time Weather via MCP")
    print("=" * 60)
    print("\nExamples:")
    print("  - 'What's the weather in Austin, Texas?'")
    print("  - 'Will it rain in Seattle tomorrow?'")
    print("  - 'What's the temperature in Tokyo?'")
    print("\nType 'quit' or 'exit' to stop.\n")

    # Create agent (uses local Lemonade server by default)
    try:
        agent = WeatherAgent()
        print("Weather Agent ready!\n")
    except Exception as e:
        print(f"Error initializing agent: {e}")
        print("\nMake sure:")
        print("  1. Lemonade server is running: lemonade-server serve")
        print("  2. `uv` is installed so `uvx` can fetch the weather MCP server")
        return

    # Interactive loop
    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            # Process the query
            result = agent.process_query(user_input)
            if result.get("result"):
                print(f"\nAgent: {result['result']}\n")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
