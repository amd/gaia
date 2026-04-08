# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Template data and code generator for scaffolded custom agents."""

# Default instructions for generated agents — a fun, educational starting point.
# Users are expected to replace this with their own system prompt.
TEMPLATE_INSTRUCTIONS = """\
You are a funny and enthusiastic zookeeper who has a deep passion for animals. \
You work at the world's most amazing zoo and every response you give includes \
a fun fact or a playful reference to one of your beloved zoo animals.

When someone greets you, respond with excitement about what the animals are up \
to today. Be creative, lighthearted, and always bring the conversation back to \
the wonderful world of zoo animals!

Feel free to replace this instructions block with your own system prompt. \
This is where you define your agent's personality, knowledge, and behavior.\
"""

# Conversation starters shown as suggestion chips in the GAIA UI.
TEMPLATE_STARTERS = [
    "Hello! What's happening at the zoo today?",
    "Tell me a fun fact about one of your animals.",
    "Which animal is your favourite and why?",
]


def generate_agent_source(
    agent_id: str,
    agent_name: str,
    description: str,
    class_name: str,
    starters: list,
    system_prompt: str,
) -> str:
    """Build a syntactically-safe agent.py source string.

    Uses ``repr()`` for all user-supplied values to eliminate injection and
    escaping bugs.  The output is validated with ``ast.parse()`` by the caller.
    """
    lines = [
        f"# {class_name} -- Custom GAIA Agent",
        f"# Location: ~/.gaia/agents/{agent_id}/agent.py",
        "# Docs: https://amd-gaia.ai/sdk/core/agent-system",
        "",
        "from gaia.agents.base.agent import Agent",
        "from gaia.agents.base.tools import tool  # noqa: F401",
        "",
        "",
        f"class {class_name}(Agent):",
        '    """Custom agent created by the Gaia Builder."""',
        "",
        f"    AGENT_ID = {repr(agent_id)}",
        f"    AGENT_NAME = {repr(agent_name)}",
        f"    AGENT_DESCRIPTION = {repr(description)}",
        f"    CONVERSATION_STARTERS = {repr(starters)}",
        "",
        "    # -- System Prompt -----------------------------------------------",
        "    # This is your agent's personality and instructions.",
        "    # Edit the text below to change how your agent behaves.",
        "",
        "    def _get_system_prompt(self) -> str:",
        f"        return {repr(system_prompt)}",
        "",
        "    # -- Tools -------------------------------------------------------",
        "    # Define custom tools using the @tool decorator.",
        "    # Each tool becomes an action your agent can take.",
        "",
        "    def _register_tools(self):",
        "        # Example -- uncomment and modify:",
        "        #",
        "        # @tool",
        "        # def my_tool(query: str) -> str:",
        '        #     """Describe what this tool does."""',
        '        #     return f"Result for: {query}"',
        "        pass",
        "",
        "    # -- Advanced (optional) -----------------------------------------",
        "    #",
        "    # Change the default model:",
        "    #     def __init__(self, **kwargs):",
        '    #         kwargs.setdefault("model_id", "Qwen3-0.6B-GGUF")',
        "    #         super().__init__(**kwargs)",
        "    #",
        "    # Add MCP server support:",
        "    #",
        "    #   1. Create mcp_servers.json in this agent's directory:",
        f"    #      ~/.gaia/agents/{agent_id}/mcp_servers.json",
        "    #",
        "    #      Example mcp_servers.json:",
        "    #      {",
        '    #          "mcpServers": {',
        '    #              "time": {',
        '    #                  "command": "uvx",',
        '    #                  "args": ["mcp-server-time"]',
        "    #              }",
        "    #          }",
        "    #      }",
        "    #",
        "    #   2. Update this file with FOUR changes (all required):",
        "    #",
        "    #      a) Add imports at the top of this file:",
        "    #         from pathlib import Path",
        "    #         from gaia.mcp.mixin import MCPClientMixin",
        "    #         from gaia.mcp.client.config import MCPConfig",
        "    #         from gaia.mcp.client.mcp_client_manager import MCPClientManager",
        "    #",
        f"    #      b) Change the class declaration (IMPORTANT — MCPClientMixin must be added here):",
        f"    #         class {class_name}(Agent, MCPClientMixin):  # <-- add MCPClientMixin",
        "    #",
        "    #      c) Add __init__ to wire up the config:",
        "    #         def __init__(self, **kwargs):",
        "    #             config_file = str(Path(__file__).parent / 'mcp_servers.json')",
        "    #             self._mcp_manager = MCPClientManager(",
        "    #                 config=MCPConfig(config_file=config_file)",
        "    #             )",
        "    #             super().__init__(**kwargs)",
        "    #",
        "    #      d) Load MCP servers in _register_tools:",
        "    #         def _register_tools(self):",
        "    #             self.load_mcp_servers_from_config()",
        "    #",
        "    #      This overrides the global ~/.gaia/mcp_servers.json config.",
        "    #      Docs: https://amd-gaia.ai/sdk/infrastructure/mcp",
        "",
    ]
    return "\n".join(lines) + "\n"
