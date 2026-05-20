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
    _GITHUB,
    _MEMORY,
    _GIT,
)

for _spec in _ALL_SPECS:
    REGISTRY.register(_spec)
