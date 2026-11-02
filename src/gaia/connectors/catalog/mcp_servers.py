# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MCP server catalog entries.

Curated, tested MCP servers exposed as ``ConnectorSpec`` objects in the global
``REGISTRY``. Importing this module also imports ``gaia.connectors.mcp_server``
so the handler is registered before any dispatch call.

Scope: the catalog lists only MCP servers that are consumed by the built-in
agents through the connectors framework (i.e. at least one built-in agent
declares ``REQUIRED_CONNECTORS`` pointing at the entry). Servers with no
built-in consumers were removed: ``mcp-filesystem`` and ``mcp-fetch`` are
gone because the File Agent and Web Agent use Python-native tool mixins rather
than the MCP protocol. Custom agents that want file/web MCP access supply
their own ``mcp_servers.json`` — discoverable via the Settings "Custom agent
servers" section (#1020, #1021).
"""

import gaia.connectors.mcp_server  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConfigField, ConnectorSpec

# ---------------------------------------------------------------------------
# Curated catalog
# ---------------------------------------------------------------------------
# Package versions are pinned; refresh them with the steps in
# docs/reference/dependency-management.mdx ("MCP connector catalog pins").

_GITHUB = ConnectorSpec(
    id="mcp-github",
    display_name="GitHub",
    icon="🐙",
    category="dev-tools",
    tier=1,
    type="mcp_server",
    description="Repos, PRs, issues, workflows — full GitHub access.",
    docs_url="https://amd-gaia.ai/docs/connectors/github",
    mcp_command="npx",
    mcp_args=("-y", "@modelcontextprotocol/server-github@2025.4.8"),
    mcp_env_keys=("GITHUB_PERSONAL_ACCESS_TOKEN",),
    config_schema=(
        ConfigField(
            key="GITHUB_PERSONAL_ACCESS_TOKEN",
            label="GitHub Personal Access Token",
            kind="secret",
            placeholder="ghp_…",
            help_md="Create a [classic token](https://github.com/settings/tokens) with `repo` and `workflow` scopes.",
            secret=True,
        ),
    ),
)

_TAVILY = ConnectorSpec(
    id="mcp-tavily",
    display_name="Tavily",
    icon="🛜",
    category="dev-tools",
    tier=1,
    type="mcp_server",
    description="Web search and content extraction for agents through the Tavily API.",
    docs_url="https://amd-gaia.ai/docs/connectors/tavily",
    mcp_command="npx",
    mcp_args=("-y", "tavily-mcp@0.2.22"),
    mcp_env_keys=("TAVILY_API_KEY",),
    config_schema=(
        ConfigField(
            key="TAVILY_API_KEY",
            label="Tavily API Key",
            kind="secret",
            placeholder="tvly-…",
            help_md="Get an API key from your [Tavily dashboard](https://app.tavily.com/).",
            secret=True,
        ),
    ),
)

_MEMORY = ConnectorSpec(
    id="mcp-memory",
    display_name="Memory",
    icon="🧠",
    category="context",
    tier=1,
    type="mcp_server",
    description="Knowledge graph-based persistent memory for agents.",
    docs_url="https://amd-gaia.ai/docs/connectors/memory",
    mcp_command="npx",
    mcp_args=("-y", "@modelcontextprotocol/server-memory@2026.8.31"),
)

_GIT = ConnectorSpec(
    id="mcp-git",
    display_name="Git",
    icon="🔀",
    category="dev-tools",
    tier=1,
    type="mcp_server",
    description="Git repository tools: status, diff, log, show, branch, and commit.",
    docs_url="https://amd-gaia.ai/docs/connectors/git",
    # The reference Git server ships on PyPI only; the npm name never existed.
    mcp_command="uvx",
    mcp_args=("mcp-server-git==2026.8.18",),
)

# @modelcontextprotocol/server-slack is deprecated on npm; this is the maintained successor.
_SLACK = ConnectorSpec(
    id="mcp-slack",
    display_name="Slack",
    icon="💬",
    category="communication",
    tier=2,
    type="mcp_server",
    description="Read channels, threads, and DMs in your Slack workspace.",
    docs_url="https://amd-gaia.ai/docs/connectors/slack",
    mcp_command="npx",
    mcp_args=("-y", "slack-mcp-server@1.3.0", "--transport", "stdio"),
    mcp_env_keys=("SLACK_MCP_XOXB_TOKEN",),
    config_schema=(
        ConfigField(
            key="SLACK_MCP_XOXB_TOKEN",
            label="Slack Bot User OAuth Token",
            kind="secret",
            placeholder="xoxb-…",
            help_md="Create a [Slack app](https://api.slack.com/apps), add the bot scopes listed in the [setup guide](https://amd-gaia.ai/docs/connectors/slack), install it to your workspace, and copy the **Bot User OAuth Token**.",
            secret=True,
        ),
    ),
)

_DISCORD = ConnectorSpec(
    id="mcp-discord",
    display_name="Discord",
    icon="🎮",
    category="communication",
    tier=2,
    type="mcp_server",
    description="Read and send messages in the Discord servers your bot has joined.",
    docs_url="https://amd-gaia.ai/docs/connectors/discord",
    mcp_command="npx",
    mcp_args=("-y", "mcp-discord@1.3.4"),
    mcp_env_keys=("DISCORD_TOKEN",),
    config_schema=(
        ConfigField(
            key="DISCORD_TOKEN",
            label="Discord Bot Token",
            kind="secret",
            placeholder="MTA…",
            help_md="Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, turn on **Message Content Intent**, and copy its token. See the [setup guide](https://amd-gaia.ai/docs/connectors/discord).",
            secret=True,
        ),
    ),
)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_ALL_SPECS = (
    _GITHUB,
    _TAVILY,
    _MEMORY,
    _GIT,
    _SLACK,
    _DISCORD,
)

for _spec in _ALL_SPECS:
    REGISTRY.register(_spec)
