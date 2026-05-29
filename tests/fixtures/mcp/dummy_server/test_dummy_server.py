# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Sanity tests for the dummy MCP server fixture."""

from __future__ import annotations

import json
import sys

from gaia.mcp.client.mcp_client import MCPClient


def _dummy_server_config(log_path):
    return {
        "command": sys.executable,
        "args": ["tests/fixtures/mcp/dummy_server/server.py"],
        "env": {"GAIA_DUMMY_MCP_LOG": str(log_path)},
    }


def test_dummy_mcp_server_cli_invocation(tmp_path):
    log_path = tmp_path / "dummy-mcp.jsonl"
    client = MCPClient.from_config("dummy", _dummy_server_config(log_path))

    try:
        assert client.connect()
        tool_names = {tool.name for tool in client.list_tools()}
        assert {"echo", "add_two_numbers", "mock_search"} <= tool_names

        result = client.call_tool("add_two_numbers", {"a": 20, "b": 22})
        assert json.loads(result["content"][0]["text"]) == {"sum": 42}

        records = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        assert records == [
            {
                "tool": "add_two_numbers",
                "arguments": {"a": 20, "b": 22},
                "result": {"sum": 42},
            }
        ]
    finally:
        client.disconnect()
