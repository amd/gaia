# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Dummy MCP server fixture.

The server speaks stdio MCP via FastMCP and records each tool call to a JSONL
file when ``GAIA_DUMMY_MCP_LOG`` is set. It is intentionally dependency-light
and deterministic so installer tests do not need npx, network, or credentials.

Usage:
    python tests/fixtures/mcp/dummy_server/server.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP


def _record_tool_call(tool: str, arguments: Dict[str, Any], result: Any) -> None:
    log_path = os.environ.get("GAIA_DUMMY_MCP_LOG")
    if not log_path:
        return

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"tool": tool, "arguments": arguments, "result": result},
                sort_keys=True,
            )
            + "\n"
        )


def create_dummy_mcp_server() -> FastMCP:
    """Create the deterministic dummy MCP server."""
    mcp = FastMCP(name="GAIA Dummy MCP")

    @mcp.tool()
    def echo(message: str) -> str:
        """Return the provided message unchanged."""
        _record_tool_call("echo", {"message": message}, message)
        return message

    @mcp.tool()
    def add_two_numbers(a: int, b: int) -> Dict[str, int]:
        """Add two integers and return the sum."""
        result = {"sum": a + b}
        _record_tool_call("add_two_numbers", {"a": a, "b": b}, result)
        return result

    @mcp.tool()
    def mock_search(query: str, limit: int = 3) -> Dict[str, Any]:
        """Return deterministic mock search results for a query."""
        result = {
            "query": query,
            "results": [
                {"title": f"{query} result {idx}", "rank": idx}
                for idx in range(1, limit + 1)
            ],
        }
        _record_tool_call("mock_search", {"query": query, "limit": limit}, result)
        return result

    return mcp


def main() -> None:
    """Run the dummy MCP server over stdio."""
    create_dummy_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
