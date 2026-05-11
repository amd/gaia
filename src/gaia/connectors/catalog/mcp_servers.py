# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MCP server catalog entries.

Curated, tested MCP servers exposed as ``ConnectorSpec`` objects in the global
``REGISTRY``. Importing this module also imports ``gaia.connectors.mcp_server``
so the handler is registered before any dispatch call.

Scope: this catalog ships only entries that have explicit test coverage or are
trivial enough (no API key required) to verify on the fly. Untested entries
that previously shipped have been removed in favor of the user-supplied custom
MCP path (see ``gaia connectors mcp add``).
"""

import gaia.connectors.mcp_server  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConfigField, ConnectorSpec

# ---------------------------------------------------------------------------
# Curated catalog
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

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_ALL_SPECS = (
    _FILESYSTEM,
    _GITHUB,
    _FETCH,
    _MEMORY,
    _GIT,
)

for _spec in _ALL_SPECS:
    REGISTRY.register(_spec)
