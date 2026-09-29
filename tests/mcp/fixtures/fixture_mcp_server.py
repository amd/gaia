# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Local stdio MCP server for the MCP client tests.

Built on the official MCP Python SDK so the client is exercised against a real,
independently implemented server without fetching anything at test time.

Run: python tests/mcp/fixtures/fixture_mcp_server.py
"""

import os

from mcp.server import MCPServer

mcp = MCPServer(name="GAIA test fixture")


@mcp.tool()
def echo(text: str) -> str:
    """Return the given text unchanged."""
    return text


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def read_env(name: str) -> str:
    """Return the value of an environment variable in the server process."""
    if name not in os.environ:
        raise ValueError(f"environment variable {name!r} is not set")
    return os.environ[name]


if __name__ == "__main__":
    mcp.run(transport="stdio")
