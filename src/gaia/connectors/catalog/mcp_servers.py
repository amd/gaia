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

# Home Assistant's own MCP server. Pinned to the Assist endpoint, which reaches
# only the entities the user exposed to Assist; /api/mcp and the other API
# endpoints can call any action with an admin token.
_HOME_ASSISTANT = ConnectorSpec(
    id="mcp-home-assistant",
    display_name="Home Assistant",
    icon="🏠",
    category="smart-home",
    tier=2,
    type="mcp_server",
    description="Check and control the devices you've shared with Assist in Home Assistant.",
    docs_url="https://amd-gaia.ai/docs/connectors/home-assistant",
    product_url="https://www.home-assistant.io/integrations/mcp_server/",
    mcp_transport="http",
    mcp_url_field="HA_URL",
    mcp_url_path="/api/mcp/assist",
    mcp_env_keys=("HA_TOKEN",),
    mcp_headers=(("Authorization", "HA_TOKEN", "Bearer {}"),),
    mcp_http_hints=(
        (
            "unauthorized",
            "Home Assistant rejected the access token. Create a new long-lived "
            "access token in your Home Assistant profile (Security tab) and "
            "paste it here.",
        ),
        (
            "not_found",
            "Home Assistant at {url} has no MCP server GAIA can use. Add the "
            "Model Context Protocol Server integration in Home Assistant "
            "(Settings › Devices & services › Add integration), and update Home "
            "Assistant if it is older than 2026.8. Then connect again.",
        ),
        (
            "unreachable",
            "Home Assistant isn't answering at {url}. Check the address, and "
            "that this PC is on the same network.",
        ),
        (
            "protocol",
            "Home Assistant at {url} answered, but its MCP server is too old "
            "for GAIA. Update Home Assistant, then connect again.",
        ),
    ),
    mcp_writes_need_person=True,
    mcp_tool_note=(
        "Read the live context first and use the device's name exactly as it "
        "lists it; set area only to an area the live context names."
    ),
    config_schema=(
        ConfigField(
            key="HA_URL",
            label="Home Assistant address",
            kind="url",
            placeholder="http://homeassistant.local:8123",
            help_md="The address you open Home Assistant at, without a path.",
        ),
        ConfigField(
            key="HA_TOKEN",
            label="Long-lived access token",
            kind="secret",
            placeholder="eyJ…",
            help_md="In Home Assistant open your profile, then **Security › Long-lived access tokens › Create token**. A token from a non-admin user is safest. GAIA can only reach the devices you expose to Assist.",
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
    _HOME_ASSISTANT,
)

for _spec in _ALL_SPECS:
    REGISTRY.register(_spec)
