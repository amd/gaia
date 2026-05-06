# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MCP server catalog entries.

Translates the curated server list from ``src/gaia/ui/routers/mcp.py`` into
``ConnectorSpec`` objects registered in the global ``REGISTRY``.  Importing
this module also imports ``gaia.connectors.mcp_server`` so the handler is
registered before any dispatch call.
"""

import gaia.connectors.mcp_server  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConfigField, ConnectorSpec

# ---------------------------------------------------------------------------
# Tier 1 — Essential
# ---------------------------------------------------------------------------

_FILESYSTEM = ConnectorSpec(
    id="mcp-filesystem",
    display_name="File System",
    icon="📁",
    category="system",
    tier=1,
    type="mcp_server",
    description="Secure file read/write/search with configurable access controls.",
    mcp_command="npx",
    mcp_args=("-y", "@modelcontextprotocol/server-filesystem", "~"),
    config_schema=(
        ConfigField(
            key="allowed_directories",
            label="Allowed directories",
            kind="text",
            placeholder="~/Documents,~/Downloads",
            help_md="Comma-separated list of paths the server may access.",
        ),
    ),
)

_PLAYWRIGHT = ConnectorSpec(
    id="mcp-playwright",
    display_name="Browser (Playwright)",
    icon="🎭",
    category="browser",
    tier=1,
    type="mcp_server",
    description="Web browsing and interaction via accessibility snapshots.",
    mcp_command="npx",
    mcp_args=("-y", "@anthropic/mcp-playwright"),
)

_GITHUB = ConnectorSpec(
    id="mcp-github",
    display_name="GitHub",
    icon="🐙",
    category="dev-tools",
    tier=1,
    type="mcp_server",
    description="Repos, PRs, issues, workflows — full GitHub access.",
    docs_url="https://amd-gaia.ai/connectors/github",
    mcp_command="npx",
    mcp_args=("-y", "@modelcontextprotocol/server-github"),
    mcp_env_keys=("GITHUB_TOKEN",),
    config_schema=(
        ConfigField(
            key="GITHUB_TOKEN",
            label="GitHub Personal Access Token",
            kind="secret",
            placeholder="ghp_…",
            help_md="Create a [classic token](https://github.com/settings/tokens) with `repo` and `workflow` scopes.",
            secret=True,
        ),
    ),
)

_FETCH = ConnectorSpec(
    id="mcp-fetch",
    display_name="Web Fetch",
    icon="🌐",
    category="web",
    tier=1,
    type="mcp_server",
    description="Fetch web content and convert it to Markdown.",
    mcp_command="npx",
    mcp_args=("-y", "@modelcontextprotocol/server-fetch"),
)

_MEMORY = ConnectorSpec(
    id="mcp-memory",
    display_name="Memory",
    icon="🧠",
    category="context",
    tier=1,
    type="mcp_server",
    description="Knowledge graph-based persistent memory for agents.",
    mcp_command="npx",
    mcp_args=("-y", "@modelcontextprotocol/server-memory"),
)

_GIT = ConnectorSpec(
    id="mcp-git",
    display_name="Git",
    icon="🔀",
    category="dev-tools",
    tier=1,
    type="mcp_server",
    description="Git repository tools: log, diff, status, blame.",
    mcp_command="npx",
    mcp_args=("-y", "@modelcontextprotocol/server-git"),
)

_DESKTOP_COMMANDER = ConnectorSpec(
    id="mcp-desktop-commander",
    display_name="Desktop Commander",
    icon="🖥️",
    category="system",
    tier=1,
    type="mcp_server",
    description="Terminal command execution + file operations with user control.",
    mcp_command="npx",
    mcp_args=("-y", "desktop-commander"),
)

# ---------------------------------------------------------------------------
# Tier 2 — High Value
# ---------------------------------------------------------------------------

_BRAVE_SEARCH = ConnectorSpec(
    id="mcp-brave-search",
    display_name="Brave Search",
    icon="🦁",
    category="web-search",
    tier=2,
    type="mcp_server",
    description="Web search via Brave Search API.",
    mcp_command="npx",
    mcp_args=("-y", "@anthropic/mcp-brave-search"),
    mcp_env_keys=("BRAVE_API_KEY",),
    config_schema=(
        ConfigField(
            key="BRAVE_API_KEY",
            label="Brave API Key",
            kind="secret",
            placeholder="BSA…",
            help_md="Get a key at [brave.com/search/api](https://brave.com/search/api/).",
            secret=True,
        ),
    ),
)

_POSTGRES = ConnectorSpec(
    id="mcp-postgres",
    display_name="PostgreSQL",
    icon="🐘",
    category="database",
    tier=2,
    type="mcp_server",
    description="Read-only database queries against a PostgreSQL database.",
    mcp_command="npx",
    mcp_args=(
        "-y",
        "@modelcontextprotocol/server-postgres",
        "postgresql://localhost/mydb",
    ),
    config_schema=(
        ConfigField(
            key="connection_string",
            label="Connection string",
            kind="text",
            placeholder="postgresql://user:pass@host/db",
        ),
    ),
)

_CONTEXT7 = ConnectorSpec(
    id="mcp-context7",
    display_name="Context7 Docs",
    icon="📖",
    category="documentation",
    tier=2,
    type="mcp_server",
    description="Inject fresh, version-specific library docs into agent context.",
    mcp_command="npx",
    mcp_args=("-y", "context7-mcp"),
)

_GMAIL = ConnectorSpec(
    id="mcp-gmail",
    display_name="Gmail",
    icon="✉️",
    category="email",
    tier=2,
    type="mcp_server",
    description="Read, search, send, label, and archive Gmail messages.",
    mcp_command="npx",
    mcp_args=("-y", "gmail-mcp-server"),
    mcp_env_keys=("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET"),
    config_schema=(
        ConfigField(
            key="GMAIL_CLIENT_ID", label="Gmail Client ID", kind="text", secret=False
        ),
        ConfigField(
            key="GMAIL_CLIENT_SECRET",
            label="Gmail Client Secret",
            kind="secret",
            secret=True,
        ),
    ),
)

_GOOGLE_CALENDAR = ConnectorSpec(
    id="mcp-google-calendar",
    display_name="Google Calendar",
    icon="📅",
    category="calendar",
    tier=2,
    type="mcp_server",
    description="Events, scheduling, availability, and RSVP management.",
    mcp_command="npx",
    mcp_args=("-y", "google-calendar-mcp"),
    mcp_env_keys=("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    config_schema=(
        ConfigField(
            key="GOOGLE_CLIENT_ID", label="Google Client ID", kind="text", secret=False
        ),
        ConfigField(
            key="GOOGLE_CLIENT_SECRET",
            label="Google Client Secret",
            kind="secret",
            secret=True,
        ),
    ),
)

_OUTLOOK = ConnectorSpec(
    id="mcp-outlook",
    display_name="Outlook / Microsoft 365",
    icon="📧",
    category="email",
    tier=2,
    type="mcp_server",
    description="Outlook email and calendar via Microsoft Graph API.",
    mcp_command="npx",
    mcp_args=("-y", "outlook-mcp-server"),
    mcp_env_keys=("MS_CLIENT_ID", "MS_CLIENT_SECRET"),
    config_schema=(
        ConfigField(
            key="MS_CLIENT_ID", label="Azure App Client ID", kind="text", secret=False
        ),
        ConfigField(
            key="MS_CLIENT_SECRET",
            label="Azure App Client Secret",
            kind="secret",
            secret=True,
        ),
    ),
)

_SPOTIFY = ConnectorSpec(
    id="mcp-spotify",
    display_name="Spotify",
    icon="🎵",
    category="media",
    tier=2,
    type="mcp_server",
    description="Play, pause, skip, search tracks, and manage playlists.",
    mcp_command="npx",
    mcp_args=("-y", "spotify-mcp-server"),
    mcp_env_keys=("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"),
    config_schema=(
        ConfigField(
            key="SPOTIFY_CLIENT_ID",
            label="Spotify Client ID",
            kind="text",
            secret=False,
        ),
        ConfigField(
            key="SPOTIFY_CLIENT_SECRET",
            label="Spotify Client Secret",
            kind="secret",
            secret=True,
        ),
    ),
)

_SLACK = ConnectorSpec(
    id="mcp-slack",
    display_name="Slack",
    icon="💬",
    category="communication",
    tier=2,
    type="mcp_server",
    description="Channel management, messaging, and conversation history.",
    mcp_command="npx",
    mcp_args=("-y", "slack-mcp-server"),
    mcp_env_keys=("SLACK_BOT_TOKEN",),
    config_schema=(
        ConfigField(
            key="SLACK_BOT_TOKEN",
            label="Slack Bot Token",
            kind="secret",
            placeholder="xoxb-…",
            help_md="Create a bot at [api.slack.com/apps](https://api.slack.com/apps).",
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
    description="Workspace pages, databases, and task management.",
    mcp_command="npx",
    mcp_args=("-y", "notion-mcp"),
    mcp_env_keys=("NOTION_API_KEY",),
    config_schema=(
        ConfigField(
            key="NOTION_API_KEY",
            label="Notion Integration Token",
            kind="secret",
            placeholder="secret_…",
            help_md="Create an integration at [notion.so/my-integrations](https://www.notion.so/my-integrations).",
            secret=True,
        ),
    ),
)

_LINEAR = ConnectorSpec(
    id="mcp-linear",
    display_name="Linear",
    icon="📋",
    category="dev-tools",
    tier=2,
    type="mcp_server",
    description="Issues, projects, and cycles — full Linear workspace access.",
    mcp_command="npx",
    mcp_args=("-y", "linear-mcp-server"),
    mcp_env_keys=("LINEAR_API_KEY",),
    config_schema=(
        ConfigField(
            key="LINEAR_API_KEY",
            label="Linear API Key",
            kind="secret",
            placeholder="lin_api_…",
            help_md="Generate a personal API key at [linear.app/settings/api](https://linear.app/settings/api).",
            secret=True,
        ),
    ),
)

_JIRA = ConnectorSpec(
    id="mcp-jira",
    display_name="Jira",
    icon="🟦",
    category="dev-tools",
    tier=2,
    type="mcp_server",
    description="Issues, sprints, and boards — full Jira project management.",
    mcp_command="npx",
    mcp_args=("-y", "jira-mcp-server"),
    mcp_env_keys=("JIRA_API_TOKEN", "JIRA_BASE_URL", "JIRA_USER_EMAIL"),
    config_schema=(
        ConfigField(
            key="JIRA_BASE_URL",
            label="Jira Base URL",
            kind="url",
            placeholder="https://yourorg.atlassian.net",
        ),
        ConfigField(key="JIRA_USER_EMAIL", label="Jira User Email", kind="email"),
        ConfigField(
            key="JIRA_API_TOKEN", label="Jira API Token", kind="secret", secret=True
        ),
    ),
)

_STRIPE = ConnectorSpec(
    id="mcp-stripe",
    display_name="Stripe",
    icon="💳",
    category="payments",
    tier=2,
    type="mcp_server",
    description="Payments, subscriptions, and customer management via Stripe API.",
    mcp_command="npx",
    mcp_args=("-y", "stripe-mcp-server"),
    mcp_env_keys=("STRIPE_SECRET_KEY",),
    config_schema=(
        ConfigField(
            key="STRIPE_SECRET_KEY",
            label="Stripe Secret Key",
            kind="secret",
            placeholder="sk_live_…",
            help_md="Find your key in the [Stripe Dashboard](https://dashboard.stripe.com/apikeys).",
            secret=True,
        ),
    ),
)

_SENDGRID = ConnectorSpec(
    id="mcp-sendgrid",
    display_name="SendGrid",
    icon="📨",
    category="email",
    tier=3,
    type="mcp_server",
    description="Transactional email sending and template management via SendGrid.",
    mcp_command="npx",
    mcp_args=("-y", "sendgrid-mcp-server"),
    mcp_env_keys=("SENDGRID_API_KEY",),
    config_schema=(
        ConfigField(
            key="SENDGRID_API_KEY",
            label="SendGrid API Key",
            kind="secret",
            placeholder="SG.…",
            secret=True,
        ),
    ),
)

# ---------------------------------------------------------------------------
# Tier 3 — Desktop / Windows
# ---------------------------------------------------------------------------

_WINDOWS_AUTOMATION = ConnectorSpec(
    id="mcp-windows-automation",
    display_name="Windows Automation",
    icon="🪟",
    category="computer-use",
    tier=3,
    type="mcp_server",
    description="Native Windows UI automation: open apps, control windows, simulate input.",
    mcp_command="npx",
    mcp_args=("-y", "mcp-windows-automation"),
)

# ---------------------------------------------------------------------------
# Tier 4 — Microsoft Ecosystem
# ---------------------------------------------------------------------------

_MICROSOFT_LEARN = ConnectorSpec(
    id="mcp-microsoft-learn",
    display_name="Microsoft Learn",
    icon="📘",
    category="documentation",
    tier=4,
    type="mcp_server",
    description="Real-time access to Microsoft documentation.",
    mcp_command="npx",
    mcp_args=("-y", "@microsoft/mcp-docs"),
)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_ALL_SPECS = (
    _FILESYSTEM,
    _PLAYWRIGHT,
    _GITHUB,
    _FETCH,
    _MEMORY,
    _GIT,
    _DESKTOP_COMMANDER,
    _BRAVE_SEARCH,
    _POSTGRES,
    _CONTEXT7,
    _GMAIL,
    _GOOGLE_CALENDAR,
    _OUTLOOK,
    _SPOTIFY,
    _SLACK,
    _NOTION,
    _LINEAR,
    _JIRA,
    _STRIPE,
    _SENDGRID,
    _WINDOWS_AUTOMATION,
    _MICROSOFT_LEARN,
)

for _spec in _ALL_SPECS:
    REGISTRY.register(_spec)
