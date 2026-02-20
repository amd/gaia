#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Weather Agent Example

A simple agent that provides real-time weather information using MCP weather server.
This demonstrates GAIA's MCP client integration with external APIs.

Requirements:
- Python 3.12+
- MCP weather server: uvx mcp-server-weather
- Lemonade server running for LLM reasoning

Run:
    uv run examples/weather_agent.py

Examples:
    You: What's the weather in Austin, Texas?
    You: Will it rain in Seattle tomorrow?
    You: What's the temperature in Tokyo?
"""

from gaia import Agent, MCPClientMixin


class WeatherAgent(Agent, MCPClientMixin):
    """Agent that provides weather information via MCP weather server."""

    def __init__(self, **kwargs):
        """Initialize the Weather Agent.

        Args:
            **kwargs: Additional arguments passed to Agent
        """
        # Initialize Agent with lightweight model for faster inference
        Agent.__init__(self, max_steps=10, model_id="Qwen3-4B-GGUF", **kwargs)

        # Initialize MCPClientMixin
        MCPClientMixin.__init__(self, auto_load_config=False)

        # Connect to Open-Meteo weather MCP server (free, no API key needed!)
        print("Connecting to MCP weather server...")
        success = self.connect_mcp_server(
            "weather", {"command": "uvx", "args": ["mcp-server-weather"]}
        )
        if success:
            print("  ✅ Connected to weather MCP server")
        else:
            print("  ❌ Failed to connect to weather MCP server")
            print("  Make sure to install: uvx mcp-server-weather")

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return """You are a helpful weather assistant.

Use the available MCP weather tools to provide accurate, real-time weather information.
When users ask about weather, use the tools to get current conditions and forecasts.

Be conversational and helpful. Include relevant details like temperature, conditions,
and any weather alerts if available."""

    def _register_tools(self) -> None:
        """Register agent tools.

        MCP tools are automatically registered by MCPClientMixin
        when connect_mcp_server() is called.
        """
        pass


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
        print("  2. Weather MCP server is installed: uvx mcp-server-weather")
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
