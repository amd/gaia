# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Template data and code generator for scaffolded custom agents."""

from typing import List, Optional

from gaia.agents.registry import KNOWN_TOOLS

# Demo-only example persona — NOT the production default. A self-describing
# example so docs and tutorials can show a complete agent. Generated agents
# derive their persona from the user's described purpose (see
# ``default_system_prompt``); a thematically-wrong placeholder is worse than a
# generic-but-correct one.
TEMPLATE_INSTRUCTIONS = """\
You are a freshly scaffolded GAIA example agent — a working starting point, \
not a finished product. Introduce yourself as exactly that: a template the \
developer is about to shape into something of their own.

When someone talks to you, answer helpfully from general knowledge and remind \
them you can be given a real personality, knowledge, and tools.

Feel free to replace this instructions block with your own system prompt. \
This is where you define your agent's personality, knowledge, and behavior.\
"""

# Demo-only example conversation starters (see TEMPLATE_INSTRUCTIONS).
TEMPLATE_STARTERS = [
    "What are you an example of?",
    "How do I customize you?",
    "What could I turn you into?",
]

# Appended to every scaffolded agent's authored system prompt so the agent
# itself sets honest expectations: it's a starter template, not a finished agent
# with real tools. The Builder is an alpha feature.
STARTER_CAVEAT = (
    "\n\n---\n"
    "You are a starter template scaffolded by the GAIA Agent Builder (an alpha "
    "feature). You can talk about your topic from what you already know, but you do "
    "not yet have tools to take real actions — you cannot actually fetch, browse, or "
    "parse live data. In your first reply, briefly tell the user you're a starting "
    "point they can extend with tools or MCP servers "
    "(https://amd-gaia.ai/docs/guides/custom-agent), then help as best you can."
)


def default_system_prompt(agent_name: str, description: str) -> str:
    """Minimal, purpose-derived system prompt used when none is supplied.

    A generic-but-correct prompt beats a thematically-wrong placeholder
    (CLAUDE.md: no silent fallbacks).
    """
    desc = description.strip()
    body = f"You are {agent_name}, a helpful AI assistant."
    if desc:
        body += f" {desc}"
    return (
        f"{body}\n\nAnswer clearly and accurately. If you are unsure, say so "
        "rather than guessing, and keep your responses focused on the user's "
        "request."
    )


def default_conversation_starters(agent_name: str) -> List[str]:
    """Generic, on-topic starter chips used when none are supplied.

    Returns three to honor the "2-3 starters" contract in the create_agent tool
    docstring and the Builder system prompt.
    """
    return [
        f"What can {agent_name} help me with?",
        "Show me an example of what you can do.",
        "How should I get started?",
    ]


def _class_docstring(description: str) -> str:
    """Return a safe one-line docstring literal built from the description.

    Uses ``repr()`` so any quotes/backslashes in the description can't break the
    generated source — consistent with the rest of this module.
    """
    return repr(description.strip() or "Custom GAIA agent.")


def _build_header(class_name: str, agent_id: str, flavor: str) -> List[str]:
    """Top-of-file comments shared by every template."""
    return [
        f"# {class_name} -- Custom GAIA Agent{flavor}",
        "# Alpha: scaffolded by the Gaia Builder — a starter template you extend yourself.",
        f"# Location: ~/.gaia/agents/{agent_id}/agent.py",
        "# Docs: https://amd-gaia.ai/docs/sdk/core/agent-system",
        "#        https://amd-gaia.ai/docs/sdk/patterns",
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
    card_description: str,
) -> List[str]:
    return [
        *_build_header(class_name, agent_id, ""),
        "from gaia.agents.base.agent import Agent",
        "from gaia.agents.base.tools import _TOOL_REGISTRY, tool  # noqa: F401",
        "",
        "",
        f"class {class_name}(Agent):",
        f"    {_class_docstring(description)}",
        "",
        *_build_class_attrs(agent_id, agent_name, card_description, starters),
        "",
        "    # -- System Prompt -----------------------------------------------",
        "    # This is your agent's personality and instructions.",
        "    # Edit the text below to change how your agent behaves.",
        "",
        "    # A starter-template caveat is appended to the end of the returned prompt",
        '    # (after the "---" marker). Delete that trailing text once your agent has real',
        "    # tools/MCP, so it stops disclaiming abilities it now has.",
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
        "    # MCP: https://amd-gaia.ai/docs/sdk/infrastructure/mcp",
        "",
    ]


def _render_mcp(
    agent_id: str,
    agent_name: str,
    description: str,
    class_name: str,
    starters: list,
    system_prompt: str,
    card_description: str,
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
        f"    {_class_docstring(description)}",
        "",
        *_build_class_attrs(agent_id, agent_name, card_description, starters),
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
        "    # A starter-template caveat is appended to the end of the returned prompt",
        '    # (after the "---" marker). Delete that trailing text once your agent has real',
        "    # tools/MCP, so it stops disclaiming abilities it now has.",
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
    card_description: str,
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
        f"    {_class_docstring(description)}",
        "",
        *_build_class_attrs(agent_id, agent_name, card_description, starters),
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
            "    # A starter-template caveat is appended to the end of the returned prompt",
            '    # (after the "---" marker). Delete that trailing text once your agent has real',
            "    # tools/MCP, so it stops disclaiming abilities it now has.",
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
            "    # See https://amd-gaia.ai/docs/sdk/patterns for more composition examples.",
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
    card_description: Optional[str] = None,
) -> str:
    """Build a syntactically-safe agent.py source string.

    Uses ``repr()`` for all user-supplied values to eliminate injection and
    escaping bugs.  The output is validated with ``ast.parse()`` by the caller.

    Args:
        agent_id: Short slug used as the directory name and AGENT_ID.
        agent_name: Human-readable display name (e.g. "Alpha Agent").
        description: One-sentence description of the agent. Used for the class
            docstring (so IDE tooltips / ``help()`` stay clean).
        class_name: Python class name (e.g. "AlphaAgent").
        starters: Conversation starter strings for the UI.
        system_prompt: The agent's system prompt text.
        enable_mcp: When True, scaffold MCP support with MCPClientMixin wiring.
        tools: Optional list of KNOWN_TOOLS names (e.g. ["rag", "file_search"]).
            When provided, adds the corresponding mixin imports, base classes,
            and ``self.register_<tool>_tools()`` calls.  Invalid names raise
            ``ValueError``.
        card_description: Optional text for ``AGENT_DESCRIPTION`` (the Hub-card
            label). Defaults to ``description`` when not supplied. The Builder
            passes a tagged variant here so the "(alpha template)" marker shows
            on the card without polluting the class docstring.

    Raises:
        ValueError: If ``tools`` contains an entry not in ``KNOWN_TOOLS``.
    """
    card_description = card_description if card_description is not None else description
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
            card_description=card_description,
        )
    elif enable_mcp:
        lines = _render_mcp(
            agent_id,
            agent_name,
            description,
            class_name,
            starters,
            system_prompt,
            card_description,
        )
    else:
        lines = _render_basic(
            agent_id,
            agent_name,
            description,
            class_name,
            starters,
            system_prompt,
            card_description,
        )
    return "\n".join(lines) + "\n"
