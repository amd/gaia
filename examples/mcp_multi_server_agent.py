"""
Example: Multi-Server MCP Agent

Demonstrates an agent connecting to three MCP servers and using them
together in a single cohesive workflow.

Servers:
- Memory: Knowledge graph storage
- Sequential Thinking: Structured reasoning
- Time: Timezone utilities

Workflow:
1. Sequential thinking reasons through the task
2. Time server converts UTC to US Pacific Time
3. Memory stores the result

Requirements:
- Node.js 18+ (for npx)
- uv (for mcp-server-time)
- No API keys required

Run:
    python examples/mcp_multi_server_agent.py

Note:
    This example demonstrates the multi-server MCP pattern.
    The integration tests (tests/mcp/test_mcp_sdk_integration.py) verify
    that all three servers work correctly with these commands.

Troubleshooting:
    If MCP servers fail to connect:
    - Verify Node.js 18+ is installed: node --version
    - Verify npx is available: npx --version
    - Verify uv is installed: uv --version
    - Run integration tests to verify: pytest tests/mcp/test_mcp_sdk_integration.py -m integration
"""

from gaia.agents.base.agent import Agent
from gaia.agents.base.mcp_client_mixin import MCPClientMixin

MCP_SERVERS = {
    "memory": "npx -y @modelcontextprotocol/server-memory",
    "thinking": "npx -y @modelcontextprotocol/server-sequential-thinking",
    "time": "uvx mcp-server-time",
}


class MultiServerAgent(Agent, MCPClientMixin):
    """Agent with memory, reasoning, and time capabilities."""

    def __init__(self, **kwargs):
        # Skip Lemonade initialization to avoid subprocess interference
        kwargs.setdefault('skip_lemonade', True)
        # Allow more steps for complex multi-server workflows
        kwargs.setdefault('max_steps', 20)
        # Initialize Agent
        Agent.__init__(self, **kwargs)
        # Initialize MCPClientMixin
        MCPClientMixin.__init__(self)

        print("Connecting to MCP servers...")
        for name, command in MCP_SERVERS.items():
            success = self.connect_mcp_server(name, command)
            print(f"  {name}: {'✓' if success else '✗'}")

        # Rebuild system prompt to include MCP tools
        self._rebuild_system_prompt()

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return """You are a helpful AI assistant with access to three MCP servers:

1. **Memory Server** - Store and retrieve information using a knowledge graph
   - Use create_entities to store data
   - Use search_nodes to find information
   - Use read_graph to view the entire knowledge graph

2. **Sequential Thinking Server** - Break down complex problems with structured reasoning
   - Use sequentialthinking to reason through tasks step-by-step
   - Each thought builds on previous ones

3. **Time Server** - Get current time and convert between timezones
   - Use get_current_time to get time in any timezone
   - Use convert_time to convert between timezones

Always use the appropriate MCP tools when asked to perform tasks related to time,
reasoning, or storing/retrieving information."""

    def _register_tools(self) -> None:
        """Register agent tools (MCP tools are auto-registered)."""
        # MCP tools are automatically registered by MCPClientMixin
        # when connect_mcp_server() is called
        pass

    def _rebuild_system_prompt(self) -> None:
        """Rebuild system prompt after MCP tools are registered."""
        # Get base prompt
        self.system_prompt = self._get_system_prompt()

        # Add tools section
        tools_description = self._format_tools_for_prompt()
        self.system_prompt += f"\n\n==== AVAILABLE TOOLS ====\n{tools_description}\n"

        # Add response format
        self.system_prompt += """
==== RESPONSE FORMAT ====
You must respond ONLY in valid JSON. No text before { or after }.

**To call a tool:**
{"thought": "reasoning", "goal": "objective", "tool": "tool_name", "tool_args": {"arg1": "value1"}}

**To create a multi-step plan:**
{
  "thought": "reasoning",
  "goal": "objective",
  "plan": [
    {"tool": "tool1", "tool_args": {"arg": "val"}},
    {"tool": "tool2", "tool_args": {"arg": "val"}}
  ],
  "tool": "tool1",
  "tool_args": {"arg": "val"}
}

**To provide a final answer:**
{"thought": "reasoning", "goal": "achieved", "answer": "response to user"}

**RULES:**
1. ALWAYS use tools for real data - NEVER hallucinate
2. Plan steps MUST be objects like {"tool": "x", "tool_args": {}}, NOT strings
3. After tool results, provide an "answer" summarizing them
"""


def main():
    """Run the multi-server workflow example."""
    print("=" * 60)
    print("Multi-Server MCP Agent Example")
    print("=" * 60)
    print("\nThis example demonstrates three MCP servers working together:")
    print("  1. Sequential Thinking - Reason through the task")
    print("  2. Time - Convert server time to US Pacific")
    print("  3. Memory - Store the result")
    print()

    agent = MultiServerAgent()

    # The cohesive workflow
    result = agent.process_query("""
    Complete this workflow using all three MCP servers:

    1. FIRST, use sequential thinking to reason through this task:
       - We need to get the current server time
       - The server is likely running in UTC
       - We want to convert it to US Pacific Time (America/Los_Angeles)
       - We should store the result for future reference

    2. THEN, use the time server to:
       - Get the current time in UTC (the server's timezone)
       - Convert it to US Pacific Time (America/Los_Angeles)

    3. FINALLY, use the memory server to:
       - Create an entity named "ServerTimeCheck"
       - Entity type: "timestamp"
       - Store observations about:
         - The UTC time retrieved
         - The converted Pacific Time
         - When this check was performed
    """)

    print("\n" + "=" * 60)
    print("Result:")
    print("=" * 60)
    print(result)


if __name__ == "__main__":
    main()
