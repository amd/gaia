# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Template data and code generator for scaffolded custom agents."""

from typing import List, Optional

from gaia.agents.registry import KNOWN_TOOLS

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


def _build_header(class_name: str, agent_id: str, flavor: str) -> List[str]:
    """Top-of-file comments shared by every template."""
    return [
        f"# {class_name} -- Custom GAIA Agent{flavor}",
        f"# Location: ~/.gaia/agents/{agent_id}/agent.py",
        "# Docs: https://amd-gaia.ai/sdk/core/agent-system",
        "#        https://amd-gaia.ai/sdk/patterns",
        "",
    ]


def _build_class_attrs(
    agent_id: str, agent_name: str, description: str, starters: list
) -> List[str]:
    return [
        f"    AGENT_ID = {repr(agent_id)}",
        f"    AGENT_NAME = {repr(agent_name)}",
        f"    AGENT_DESCRIPTION = {repr(description)}",
        f"    CONVERSATION_STARTERS = {repr(starters)}",
    ]


def _render_basic(
    agent_id: str,
    agent_name: str,
    description: str,
    class_name: str,
    starters: list,
    system_prompt: str,
) -> List[str]:
    return [
        *_build_header(class_name, agent_id, ""),
        "from gaia.agents.base.agent import Agent",
        "from gaia.agents.base.tools import _TOOL_REGISTRY, tool  # noqa: F401",
        "",
        "",
        f"class {class_name}(Agent):",
        '    """One-line description of what this agent does.',
        "",
        "    TODO: Replace this docstring — it appears in IDE tooltips,",
        "    `help()` output, and agent-discovery listings.",
        '    """',
        "",
        *_build_class_attrs(agent_id, agent_name, description, starters),
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
        "        _TOOL_REGISTRY.clear()",
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
        "    # MCP: https://amd-gaia.ai/sdk/infrastructure/mcp",
        "",
    ]


def _render_mcp(
    agent_id: str,
    agent_name: str,
    description: str,
    class_name: str,
    starters: list,
    system_prompt: str,
) -> List[str]:
    return [
        *_build_header(class_name, agent_id, " (MCP-enabled)"),
        "from pathlib import Path",
        "",
        "from gaia.agents.base.agent import Agent",
        "from gaia.agents.base.tools import _TOOL_REGISTRY, tool  # noqa: F401",
        "from gaia.mcp.client.config import MCPConfig",
        "from gaia.mcp.client.mcp_client_manager import MCPClientManager",
        "from gaia.mcp.mixin import MCPClientMixin",
        "",
        "",
        f"class {class_name}(Agent, MCPClientMixin):",
        '    """Custom MCP-enabled agent created by the Gaia Builder."""',
        "",
        *_build_class_attrs(agent_id, agent_name, description, starters),
        "",
        "    # -- MCP Setup --------------------------------------------------",
        "    # _mcp_manager must be set BEFORE super().__init__() because",
        "    # Agent.__init__() calls _register_tools(), which loads MCP tools.",
        "",
        "    def __init__(self, **kwargs):",
        "        config_file = str(Path(__file__).parent / 'mcp_servers.json')",
        "        self._mcp_manager = MCPClientManager(",
        "            config=MCPConfig(config_file=config_file)",
        "        )",
        "        super().__init__(**kwargs)",
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
        "    # Add your tools BEFORE the MCP load call.",
        "",
        "    def _register_tools(self):",
        "        _TOOL_REGISTRY.clear()",
        "        # Example -- uncomment and modify:",
        "        #",
        "        # @tool",
        "        # def my_tool(query: str) -> str:",
        '        #     """Describe what this tool does."""',
        '        #     return f"Result for: {query}"',
        "        self.load_mcp_servers_from_config()",
        "",
        "    # -- Advanced (optional) -----------------------------------------",
        "    #",
        "    # Change the default model:",
        "    #     def __init__(self, **kwargs):",
        '    #         kwargs.setdefault("model_id", "Qwen3-0.6B-GGUF")',
        "    #         # Keep the _mcp_manager setup above this line",
        "    #         super().__init__(**kwargs)",
        "",
    ]


def _render_with_tools(
    agent_id: str,
    agent_name: str,
    description: str,
    class_name: str,
    starters: list,
    system_prompt: str,
    tools: List[str],
    enable_mcp: bool,
) -> List[str]:
    """Compose a template with tool mixins (and optional MCP)."""
    # Build imports
    imports = [
        "from gaia.agents.base.agent import Agent",
        "from gaia.agents.base.tools import _TOOL_REGISTRY, tool  # noqa: F401",
    ]
    for t in tools:
        module_path, mixin_cls = KNOWN_TOOLS[t]
        imports.append(f"from {module_path} import {mixin_cls}")
    if enable_mcp:
        imports.extend(
            [
                "from pathlib import Path",
                "",
                "from gaia.mcp.client.config import MCPConfig",
                "from gaia.mcp.client.mcp_client_manager import MCPClientManager",
                "from gaia.mcp.mixin import MCPClientMixin",
            ]
        )

    # Build class signature: Agent first, then mixins, then MCPClientMixin.
    bases = ["Agent"] + [KNOWN_TOOLS[t][1] for t in tools]
    if enable_mcp:
        bases.append("MCPClientMixin")
    class_sig = f"class {class_name}({', '.join(bases)}):"

    flavor = " (with " + ", ".join(tools) + (", MCP" if enable_mcp else "") + ")"

    lines = [
        *_build_header(class_name, agent_id, flavor),
        *imports,
        "",
        "",
        class_sig,
        '    """One-line description of what this agent does.',
        "",
        "    TODO: Replace this docstring — it appears in IDE tooltips,",
        "    `help()` output, and agent-discovery listings.",
        '    """',
        "",
        *_build_class_attrs(agent_id, agent_name, description, starters),
        "",
    ]

    if enable_mcp:
        lines.extend(
            [
                "    # -- MCP Setup --------------------------------------------------",
                "    # _mcp_manager must be set BEFORE super().__init__() because",
                "    # Agent.__init__() calls _register_tools(), which loads MCP tools.",
                "",
                "    def __init__(self, **kwargs):",
                "        config_file = str(Path(__file__).parent / 'mcp_servers.json')",
                "        self._mcp_manager = MCPClientManager(",
                "            config=MCPConfig(config_file=config_file)",
                "        )",
                "        super().__init__(**kwargs)",
                "",
            ]
        )

    lines.extend(
        [
            "    # -- System Prompt -----------------------------------------------",
            "",
            "    def _get_system_prompt(self) -> str:",
            f"        return {repr(system_prompt)}",
            "",
            "    # -- Tools -------------------------------------------------------",
            "    # Mixins below contribute tools via register_*_tools().",
            "    # Add your own @tool functions alongside them.",
            "",
            "    def _register_tools(self):",
            "        _TOOL_REGISTRY.clear()",
        ]
    )
    for t in tools:
        lines.append(f"        self.register_{t}_tools()")
    lines.extend(
        [
            "        # Example custom tool -- uncomment and modify:",
            "        #",
            "        # @tool",
            "        # def my_tool(query: str) -> str:",
            '        #     """Describe what this tool does."""',
            '        #     return f"Result for: {query}"',
        ]
    )
    if enable_mcp:
        lines.append("        self.load_mcp_servers_from_config()")
    else:
        lines.append("        pass" if not tools else "")  # syntactic placeholder

    lines.extend(
        [
            "",
            "    # -- Advanced (optional) -----------------------------------------",
            "    # See https://amd-gaia.ai/sdk/patterns for more composition examples.",
            "",
        ]
    )
    # Drop empty trailing strings consecutive
    return lines


def generate_agent_source(
    agent_id: str,
    agent_name: str,
    description: str,
    class_name: str,
    starters: list,
    system_prompt: str,
    enable_mcp: bool = False,
    tools: Optional[List[str]] = None,
) -> str:
    """Build a syntactically-safe agent.py source string.

    Uses ``repr()`` for all user-supplied values to eliminate injection and
    escaping bugs.  The output is validated with ``ast.parse()`` by the caller.

    Args:
        agent_id: Short slug used as the directory name and AGENT_ID.
        agent_name: Human-readable display name (e.g. "Alpha Agent").
        description: One-sentence description of the agent.
        class_name: Python class name (e.g. "AlphaAgent").
        starters: Conversation starter strings for the UI.
        system_prompt: The agent's system prompt text.
        enable_mcp: When True, scaffold MCP support with MCPClientMixin wiring.
        tools: Optional list of KNOWN_TOOLS names (e.g. ["rag", "file_search"]).
            When provided, adds the corresponding mixin imports, base classes,
            and ``self.register_<tool>_tools()`` calls.  Invalid names raise
            ``ValueError``.

    Raises:
        ValueError: If ``tools`` contains an entry not in ``KNOWN_TOOLS``.
    """
    tools = list(tools or [])
    if tools:
        unknown = [t for t in tools if t not in KNOWN_TOOLS]
        if unknown:
            raise ValueError(
                f"Unknown tool(s): {unknown}. "
                f"Valid options: {sorted(KNOWN_TOOLS.keys())}"
            )
        lines = _render_with_tools(
            agent_id=agent_id,
            agent_name=agent_name,
            description=description,
            class_name=class_name,
            starters=starters,
            system_prompt=system_prompt,
            tools=tools,
            enable_mcp=enable_mcp,
        )
    elif enable_mcp:
        lines = _render_mcp(
            agent_id, agent_name, description, class_name, starters, system_prompt
        )
    else:
        lines = _render_basic(
            agent_id, agent_name, description, class_name, starters, system_prompt
        )
    return "\n".join(lines) + "\n"
