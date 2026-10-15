# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An MCP tool that ran and failed (``isError: true``) is an error, not data.

The wrapper only looked for a JSON-RPC ``error`` key, so a tool-level failure
came back as ``status: "success"`` with the error text as its "data".
"""

from types import SimpleNamespace

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.mcp.client.mcp_client import MCPTool, mcp_tool_error
from gaia.mcp.external_services import PerplexityService
from gaia.mcp.mixin import MCPClientMixin

FAILED = {"isError": True, "content": [{"type": "text", "text": "repo not found"}]}


@pytest.mark.parametrize(
    "result, expected",
    [
        (FAILED, "repo not found"),
        ({"isError": True, "content": []}, "The MCP tool reported an error."),
        ({"error": "Invalid params"}, "Invalid params"),
        ({"isError": False, "content": [{"type": "text", "text": "ok"}]}, None),
        ({"content": [{"type": "text", "text": "ok"}]}, None),
        ("plain text", None),
    ],
)
def test_mcp_tool_error_reads_both_failure_shapes(result, expected):
    assert mcp_tool_error(result) == expected


@pytest.fixture
def registered():
    saved = dict(_TOOL_REGISTRY)
    outcome = {}
    client = SimpleNamespace(
        name="github",
        prefix="github",
        list_tools=lambda: [
            MCPTool(name="get_repo", description="d", input_schema={"type": "object"})
        ],
        create_tool_wrapper=lambda tool: lambda **kw: outcome["result"],
    )
    host = object.__new__(MCPClientMixin)
    MCPClientMixin._register_mcp_tools(host, client)
    try:
        yield outcome, _TOOL_REGISTRY["mcp_github_get_repo"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_the_wrapper_reports_is_error_as_an_error(registered):
    outcome, tool = registered
    outcome["result"] = FAILED

    result = tool()

    assert result["status"] == "error"
    assert result["error"] == "repo not found"


def test_the_wrapper_keeps_a_good_result_as_data(registered):
    outcome, tool = registered
    outcome["result"] = {"content": [{"type": "text", "text": "ok"}]}

    assert tool()["status"] == "success"


def test_perplexity_does_not_return_a_tool_error_as_the_answer(monkeypatch):
    service = object.__new__(PerplexityService)
    service.env = {"PERPLEXITY_API_KEY": "k"}
    monkeypatch.setattr(service, "call_tool", lambda *a, **k: FAILED, raising=False)

    result = service.search_web("q")

    assert result == {"success": False, "error": "repo not found", "answer": ""}
