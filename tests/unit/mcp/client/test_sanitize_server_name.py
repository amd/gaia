# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``sanitise_server_name`` and ``MCPClient.prefix``."""

import logging

import pytest

from gaia.mcp.client import mcp_client
from gaia.mcp.client.mcp_client import (
    MCPClient,
    sanitise_server_name,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("tool", "tool"),
        ("tool_mcp", "tool"),
        ("mcp_tool", "tool"),
        ("Foo-MCP", "foo"),
        ("foo_mcp_bar", "foo_bar"),
        ("foo_mcp_bar_mcp", "foo_bar"),
        ("mcp", "server"),
        ("MCP", "server"),
        ("", "server"),
        ("  tool_mcp  ", "tool"),
        ("clean_name", "clean_name"),
        ("gaia-bridge", "gaia_bridge"),
    ],
)
def test_sanitise_server_name(raw, expected):
    assert sanitise_server_name(raw) == expected


def test_sanitise_idempotent():
    """Applying twice must produce the same result."""
    assert sanitise_server_name(sanitise_server_name("tool_mcp")) == "tool"
    assert sanitise_server_name(sanitise_server_name("Foo-MCP")) == "foo"


def test_sanitise_warns_once_per_distinct_raw(caplog):
    """The warning must fire exactly once per distinct raw input."""
    mcp_client._logged_sanitisations.clear()
    with caplog.at_level(logging.WARNING, logger="gaia.mcp.client.mcp_client"):
        sanitise_server_name("tool_mcp")
        sanitise_server_name("tool_mcp")
        sanitise_server_name("tool_mcp")
    matches = [r for r in caplog.records if "normalised" in r.getMessage()]
    assert len(matches) == 1


def test_sanitise_does_not_warn_on_clean_input(caplog):
    """Clean inputs (no change) must not emit a warning."""
    mcp_client._logged_sanitisations.clear()
    with caplog.at_level(logging.WARNING, logger="gaia.mcp.client.mcp_client"):
        sanitise_server_name("tool")
        sanitise_server_name("clean_name")
    assert not any("normalised" in r.getMessage() for r in caplog.records)


def test_mcp_client_init_computes_prefix():
    """``MCPClient.__init__`` must compute and store ``prefix``."""
    client = MCPClient.from_command(name="tool_mcp", command="echo")
    assert client.name == "tool_mcp"
    assert client.prefix == "tool"


def test_mcp_client_init_clean_name_prefix_equals_name():
    """Clean names produce ``prefix == name`` — no surprise rewrites."""
    client = MCPClient.from_command(name="filesystem", command="echo")
    assert client.name == "filesystem"
    assert client.prefix == "filesystem"
