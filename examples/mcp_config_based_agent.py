"""
Example: MCP Agent with Config-Based Server Loading

Demonstrates the RECOMMENDED workflow for using MCP servers in team projects:
1. Commit mcp_servers.json to git (project config)
2. Agent loads servers automatically from project config
3. No hard-coding needed!

Setup (one-time for the project):
    # Option 1: Create project config manually
    # Create ./mcp_servers.json with your required servers
    # (See example below or in docs)

    # Option 2: Use CLI with --config flag
    gaia mcp add memory "npx -y @modelcontextprotocol/server-memory" --config ./mcp_servers.json
    gaia mcp add thinking "npx -y @modelcontextprotocol/server-sequential-thinking" --config ./mcp_servers.json
    gaia mcp add time "uvx mcp-server-time" --config ./mcp_servers.json

    # Verify project configuration
    gaia mcp list --config ./mcp_servers.json

    # Commit to git so all team members get the same servers!
    git add mcp_servers.json
    git commit -m "Add MCP server configuration"

Run:
    python examples/mcp_config_based_agent.py

Requirements:
- Node.js 18+ (for npx)
- uv (for mcp-server-time)
- No API keys required

Note:
    This example uses a project-level config (./mcp_servers.json).
    You can also use the default user config (~/.gaia/mcp_servers.json)
    by not specifying a config path in the agent initialization.
"""

from pathlib import Path

from gaia.agents.base.agent import Agent
from gaia.agents.base.mcp_client_mixin import MCPClientMixin
from gaia.mcp.client.config import MCPConfig


class ConfigBasedAgent(Agent, MCPClientMixin):
    """Agent that loads MCP servers from configuration.

    By default, uses project-level config (./mcp_servers.json).
    Falls back to user config (~/.gaia/mcp_servers.json) if not found.
    """

    def __init__(self, config_path: str = None, **kwargs):
        # Skip Lemonade initialization to avoid subprocess interference
        kwargs.setdefault('skip_lemonade', True)
        # Allow more steps for complex multi-server workflows
        kwargs.setdefault('max_steps', 20)

        # Initialize Agent
        Agent.__init__(self, **kwargs)
        # Initialize MCPClientMixin
        MCPClientMixin.__init__(self)

        # Determine which config to use
        if config_path is None:
            # Try project-level config first
            project_config = Path("./mcp_servers.json")
            if project_config.exists():
                config_path = str(project_config)
                print(f"📋 Using project config: {config_path}")
            else:
                print("📋 Using user config: ~/.gaia/mcp_servers.json")

        # Set custom config if specified
        if config_path:
            self._mcp_manager.config = MCPConfig(config_path)

        # Load ALL configured MCP servers automatically
        print("Loading configured MCP servers...")
        count = self.load_mcp_servers_from_config()

        # Show what was loaded
        servers = self.list_mcp_servers()
        if servers:
            print(f"✓ Loaded {len(servers)} server(s): {', '.join(servers)}")

            # Show tool count for each server
            for server_name in servers:
                client = self.get_mcp_client(server_name)
                tools = client.list_tools()
                print(f"  {server_name}: {len(tools)} tools")
        else:
            print("⚠️  No MCP servers configured!")
            print("\nConfigure servers with:")
            print('  gaia mcp add memory "npx -y @modelcontextprotocol/server-memory"')
            print('  gaia mcp add thinking "npx -y @modelcontextprotocol/server-sequential-thinking"')
            print('  gaia mcp add time "uvx mcp-server-time"')
            return

        # Rebuild system prompt to include MCP tools
        self._rebuild_system_prompt()

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return """You are a helpful AI assistant with access to MCP servers.

Use the available MCP tools to complete tasks. Each tool is prefixed with 'mcp_<server>_<tool>'.

Always use the appropriate tools when asked to perform tasks related to:
- Memory/knowledge storage
- Sequential/structured thinking
- Time and timezone operations
"""

    def _register_tools(self) -> None:
        """Register agent tools (MCP tools are auto-registered)."""
        # MCP tools are automatically registered by MCPClientMixin
        # when load_from_config() is called
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
    """Run the config-based MCP agent example."""
    print("=" * 60)
    print("Config-Based MCP Agent Example")
    print("=" * 60)
    print()

    # Create agent (loads servers from config)
    agent = ConfigBasedAgent()

    if not agent.list_mcp_servers():
        # No servers configured - exit
        return

    # Example workflow using configured servers
    result = agent.process_query("""
    What MCP servers are currently connected? List each server and
    briefly describe what tools it provides.
    """)

    print("\n" + "=" * 60)
    print("Result:")
    print("=" * 60)
    print(result.get('result', 'No result'))


if __name__ == "__main__":
    main()
