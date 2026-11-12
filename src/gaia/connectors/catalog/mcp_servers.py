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

import json
from urllib.parse import quote

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
    category="search",
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
    category="data",
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

SLACK_BOT_SCOPES = (
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "users:read",
    "usergroups:read",
)

# Slack opens "Create app" with this manifest filled in, so the user never picks scopes.
_SLACK_APP_MANIFEST = {
    "display_information": {
        "name": "GAIA",
        "description": "Lets your local GAIA assistant read this workspace.",
    },
    "features": {"bot_user": {"display_name": "GAIA", "always_online": False}},
    "oauth_config": {"scopes": {"bot": list(SLACK_BOT_SCOPES)}},
    "settings": {
        "org_deploy_enabled": False,
        "socket_mode_enabled": False,
        "token_rotation_enabled": False,
    },
}

# The server also offers join, leave, mark-read and user-group edits. The bot
# scopes can't use them, so the model only sees the tools that can work.
SLACK_READ_TOOLS = (
    "channels_list",
    "channels_me",
    "conversations_history",
    "conversations_replies",
    "users_search",
    "usergroups_list",
    "usergroups_me",
)

SLACK_CREATE_APP_URL = "https://api.slack.com/apps?new_app=1&manifest_json=" + quote(
    json.dumps(_SLACK_APP_MANIFEST, separators=(",", ":"))
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
    setup_url=SLACK_CREATE_APP_URL,
    mcp_command="npx",
    mcp_args=(
        "-y",
        "slack-mcp-server@1.3.0",
        "--transport",
        "stdio",
        "--enabled-tools",
        ",".join(SLACK_READ_TOOLS),
    ),
    mcp_env_keys=("SLACK_MCP_XOXB_TOKEN",),
    config_schema=(
        ConfigField(
            key="SLACK_MCP_XOXB_TOKEN",
            label="Slack bot token",
            kind="secret",
            placeholder="xoxb-…",
            prefix="xoxb-",
            help_md=(
                "GAIA reads Slack through a small app you add to your workspace. "
                "You need permission to install apps there; guest accounts can't, "
                "and some workspaces need an admin to approve it. "
                "Click **Create Slack app**, pick your workspace, and click **Next**, "
                "then **Create**. If your workspace isn't in the list, sign in to it "
                "at [slack.com](https://slack.com/signin) and click the button again. "
                "If Slack asks, install the app and click **Allow**. If Slack shows "
                "commands to run, skip them and click **Go to App Settings**. "
                "Open **OAuth & Permissions** in Slack's left menu, copy the "
                "**Bot User OAuth Token**, and paste it here. "
                "Then type `/invite @GAIA` in each channel GAIA should read."
            ),
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

_NOTION = ConnectorSpec(
    id="mcp-notion",
    display_name="Notion",
    icon="📝",
    category="productivity",
    tier=2,
    type="mcp_server",
    description="Search, read and edit the Notion pages and databases you share with GAIA.",
    docs_url="https://amd-gaia.ai/docs/connectors/notion",
    mcp_command="npx",
    mcp_args=("-y", "@notionhq/notion-mcp-server@2.5.2"),
    mcp_env_keys=("NOTION_TOKEN",),
    config_schema=(
        ConfigField(
            key="NOTION_TOKEN",
            label="Notion Integration Secret",
            kind="secret",
            placeholder="ntn_…",
            help_md=(
                "Create an internal integration at "
                "[notion.so/profile/integrations](https://www.notion.so/profile/integrations), "
                "then share the pages GAIA may use with it."
            ),
            secret=True,
        ),
    ),
)

_BRAVE_SEARCH = ConnectorSpec(
    id="mcp-brave-search",
    display_name="Brave Search",
    icon="🦁",
    category="search",
    tier=2,
    type="mcp_server",
    description="Web and local search through the Brave Search API.",
    docs_url="https://amd-gaia.ai/docs/connectors/brave-search",
    mcp_command="npx",
    mcp_args=("-y", "@modelcontextprotocol/server-brave-search@0.6.2"),
    mcp_env_keys=("BRAVE_API_KEY",),
    config_schema=(
        ConfigField(
            key="BRAVE_API_KEY",
            label="Brave Search API Key",
            kind="secret",
            placeholder="BSA…",
            help_md="Get a key from the [Brave Search API dashboard](https://api-dashboard.search.brave.com/).",
            secret=True,
        ),
    ),
)

# The server offers ~60 Jira and Confluence tools; GAIA loads none once active
# servers total more than 50, so the model sees a small read-only set.
JIRA_READ_TOOLS = (
    "jira_search",
    "jira_get_issue",
    "jira_get_all_projects",
    "jira_get_project_issues",
    "jira_get_agile_boards",
    "jira_get_board_issues",
    "jira_get_sprints_from_board",
    "jira_get_sprint_issues",
    "jira_get_transitions",
    "jira_get_user_profile",
)

_JIRA = ConnectorSpec(
    id="mcp-jira",
    display_name="Jira",
    icon="🧭",
    category="productivity",
    tier=2,
    type="mcp_server",
    description="Search and read Jira issues, projects, boards and sprints.",
    docs_url="https://amd-gaia.ai/docs/connectors/jira",
    mcp_command="uvx",
    mcp_args=(
        "mcp-atlassian==0.24.1",
        "--read-only",
        "--enabled-tools",
        ",".join(JIRA_READ_TOOLS),
    ),
    mcp_env_keys=(
        "JIRA_URL",
        "JIRA_USERNAME",
        "JIRA_API_TOKEN",
    ),
    config_schema=(
        ConfigField(
            key="JIRA_URL",
            label="Jira site address",
            kind="url",
            placeholder="https://your-team.atlassian.net",
            help_md="The address you open Jira at, for example `https://your-team.atlassian.net`.",
        ),
        ConfigField(
            key="JIRA_USERNAME",
            label="Atlassian account email",
            kind="email",
            placeholder="you@example.com",
            help_md="The email you sign in to Atlassian with.",
        ),
        ConfigField(
            key="JIRA_API_TOKEN",
            label="Atlassian API token",
            kind="secret",
            placeholder="ATATT…",
            help_md="Create one at [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens) and paste it here.",
            secret=True,
        ),
    ),
)

_GITLAB = ConnectorSpec(
    id="mcp-gitlab",
    display_name="GitLab",
    icon="🦊",
    category="dev-tools",
    tier=2,
    type="mcp_server",
    description="Projects, merge requests, issues and files on GitLab.com.",
    docs_url="https://amd-gaia.ai/docs/connectors/gitlab",
    mcp_command="npx",
    mcp_args=("-y", "@zereight/mcp-gitlab@2.3.25"),
    mcp_env_keys=("GITLAB_PERSONAL_ACCESS_TOKEN",),
    config_schema=(
        ConfigField(
            key="GITLAB_PERSONAL_ACCESS_TOKEN",
            label="GitLab Personal Access Token",
            kind="secret",
            placeholder="glpat-…",
            help_md="Create a token under [User settings → Access tokens](https://gitlab.com/-/user_settings/personal_access_tokens) with the `api` scope, or `read_api` to keep GAIA read-only.",
            secret=True,
        ),
    ),
)

_SUPABASE = ConnectorSpec(
    id="mcp-supabase",
    display_name="Supabase",
    icon="⚡",
    category="data",
    tier=2,
    type="mcp_server",
    description="Read your Supabase projects, tables, logs and docs. Read-only.",
    docs_url="https://amd-gaia.ai/docs/connectors/supabase",
    mcp_command="npx",
    mcp_args=("-y", "@supabase/mcp-server-supabase@0.16.1", "--read-only"),
    mcp_env_keys=("SUPABASE_ACCESS_TOKEN",),
    config_schema=(
        ConfigField(
            key="SUPABASE_ACCESS_TOKEN",
            label="Supabase access token",
            kind="secret",
            placeholder="sbp_…",
            help_md="Create a personal access token under [Account → Access Tokens](https://supabase.com/dashboard/account/tokens).",
            secret=True,
        ),
    ),
)

_HUBSPOT = ConnectorSpec(
    id="mcp-hubspot",
    display_name="HubSpot",
    icon="🧲",
    category="business",
    tier=2,
    type="mcp_server",
    description="Look up and update HubSpot contacts, companies, deals and tickets.",
    docs_url="https://amd-gaia.ai/docs/connectors/hubspot",
    mcp_command="npx",
    mcp_args=("-y", "@hubspot/mcp-server@0.4.0"),
    mcp_env_keys=("PRIVATE_APP_ACCESS_TOKEN",),
    config_schema=(
        ConfigField(
            key="PRIVATE_APP_ACCESS_TOKEN",
            label="HubSpot private app token",
            kind="secret",
            placeholder="pat-…",
            help_md="In HubSpot, open **Settings → Integrations → Private Apps**, create an app with the CRM scopes GAIA should have, and copy its access token.",
            secret=True,
        ),
    ),
)

_FIGMA = ConnectorSpec(
    id="mcp-figma",
    display_name="Figma",
    icon="🎨",
    category="design",
    tier=2,
    type="mcp_server",
    description="Read layout, styles and images from the Figma files you link.",
    docs_url="https://amd-gaia.ai/docs/connectors/figma",
    mcp_command="npx",
    mcp_args=("-y", "figma-developer-mcp@0.13.2", "--stdio"),
    mcp_env_keys=("FIGMA_API_KEY",),
    config_schema=(
        ConfigField(
            key="FIGMA_API_KEY",
            label="Figma personal access token",
            kind="secret",
            placeholder="figd_…",
            help_md="In Figma, open **Settings → Security → Personal access tokens** and generate a token with read access to file content.",
            secret=True,
        ),
    ),
)

_AIRTABLE = ConnectorSpec(
    id="mcp-airtable",
    display_name="Airtable",
    icon="🗂️",
    category="data",
    tier=2,
    type="mcp_server",
    description="Read and edit records in the Airtable bases you choose.",
    docs_url="https://amd-gaia.ai/docs/connectors/airtable",
    mcp_command="npx",
    mcp_args=("-y", "airtable-mcp-server@1.14.0"),
    mcp_env_keys=("AIRTABLE_API_KEY",),
    config_schema=(
        ConfigField(
            key="AIRTABLE_API_KEY",
            label="Airtable personal access token",
            kind="secret",
            placeholder="pat…",
            help_md="Create a token at [airtable.com/create/tokens](https://airtable.com/create/tokens) with `schema.bases:read` and `data.records:read` (add `data.records:write` to let GAIA edit), and pick the bases it may use.",
            secret=True,
        ),
    ),
)

_TODOIST = ConnectorSpec(
    id="mcp-todoist",
    display_name="Todoist",
    icon="✅",
    category="productivity",
    tier=2,
    type="mcp_server",
    description="Find, add, complete and reschedule your Todoist tasks.",
    docs_url="https://amd-gaia.ai/docs/connectors/todoist",
    mcp_command="npx",
    mcp_args=("-y", "@doist/todoist-ai@9.0.0"),
    mcp_env_keys=("TODOIST_API_KEY",),
    config_schema=(
        ConfigField(
            key="TODOIST_API_KEY",
            label="Todoist API token",
            kind="secret",
            help_md="In Todoist, open **Settings → Integrations → Developer** and copy your API token.",
            secret=True,
        ),
    ),
)

_FIRECRAWL = ConnectorSpec(
    id="mcp-firecrawl",
    display_name="Firecrawl",
    icon="🔥",
    category="search",
    tier=2,
    type="mcp_server",
    description="Scrape, crawl and search web pages as clean text through Firecrawl.",
    docs_url="https://amd-gaia.ai/docs/connectors/firecrawl",
    mcp_command="npx",
    mcp_args=("-y", "firecrawl-mcp@3.39.0"),
    mcp_env_keys=("FIRECRAWL_API_KEY",),
    config_schema=(
        ConfigField(
            key="FIRECRAWL_API_KEY",
            label="Firecrawl API Key",
            kind="secret",
            placeholder="fc-…",
            help_md="Get a key from your [Firecrawl dashboard](https://www.firecrawl.dev/app/api-keys).",
            secret=True,
        ),
    ),
)

_EXA = ConnectorSpec(
    id="mcp-exa",
    display_name="Exa",
    icon="🔎",
    category="search",
    tier=2,
    type="mcp_server",
    description="Web search built for AI, with page contents, through the Exa API.",
    docs_url="https://amd-gaia.ai/docs/connectors/exa",
    mcp_command="npx",
    mcp_args=("-y", "exa-mcp-server@3.4.3"),
    mcp_env_keys=("EXA_API_KEY",),
    config_schema=(
        ConfigField(
            key="EXA_API_KEY",
            label="Exa API Key",
            kind="secret",
            help_md="Get a key from the [Exa dashboard](https://dashboard.exa.ai/api-keys).",
            secret=True,
        ),
    ),
)

_CONTEXT7 = ConnectorSpec(
    id="mcp-context7",
    display_name="Context7",
    icon="📚",
    category="dev-tools",
    tier=2,
    type="mcp_server",
    description="Up-to-date documentation and code examples for libraries. No API key.",
    docs_url="https://amd-gaia.ai/docs/connectors/context7",
    mcp_command="npx",
    mcp_args=("-y", "@upstash/context7-mcp@4.6.0"),
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
    _NOTION,
    _BRAVE_SEARCH,
    _JIRA,
    _GITLAB,
    _SUPABASE,
    _HUBSPOT,
    _FIGMA,
    _AIRTABLE,
    _TODOIST,
    _FIRECRAWL,
    _EXA,
    _CONTEXT7,
)

for _spec in _ALL_SPECS:
    REGISTRY.register(_spec)
