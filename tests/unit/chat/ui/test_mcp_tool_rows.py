# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An MCP tool's activity row names its connector and shows what it returned.

Both rows of a Git-connector turn used to read "MCP tool", and expanding one
showed only "success: Tool 'git_log' returned data". The tool events now carry
the connector's display name, the server's own tool name, and the tool's text
output — in-process and through the flagship sidecar relay alike.
"""

from __future__ import annotations

import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.connectors import registry as connector_registry
from gaia.connectors.catalog import mcp_servers as mcp_catalog
from gaia.connectors.registry import ConnectorRegistry
from gaia.mcp.client.mcp_client import MCPTool
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE
from gaia.ui.models import AgentStepResponse
from gaia.ui.sse_handler import _MCP_OUTPUT_CHAR_CAP, SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

LOG = "\n".join(
    f"Commit: abc{i}\nAuthor: Dev\nMessage: change {i}\n" for i in range(2)
).strip()


def _wrapped(text: str) -> dict:
    """What ``MCPClientMixin``'s wrapper hands the agent for a text result."""
    return {
        "status": "success",
        "message": "Tool 'git_log' returned data",
        "data": {"content": [{"type": "text", "text": text}], "isError": False},
        "instruction": "Parse this JSON data and provide a human-readable summary.",
    }


def _drain(handler: SSEOutputHandler) -> list:
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


@pytest.fixture
def git_tool(monkeypatch):
    """``git_log`` from the Git connector, registered the way the mixin does it."""
    catalog = ConnectorRegistry()
    catalog.register(mcp_catalog._GIT)
    monkeypatch.setattr(connector_registry, "REGISTRY", catalog)
    entry = MCPTool(
        name="git_log", description="Show commit logs", input_schema={}
    ).to_gaia_format("git", "mcp-git")
    _TOOL_REGISTRY[entry["name"]] = entry
    yield entry["name"]
    _TOOL_REGISTRY.pop(entry["name"], None)


def test_tool_start_names_the_connector_and_its_tool(git_tool):
    handler = SSEOutputHandler()
    handler.print_tool_usage(git_tool)

    (start,) = _drain(handler)
    assert git_tool == "mcp_git_git_log"
    assert start["mcp_server"] == "mcp-git"
    assert start["mcp_server_label"] == "Git"
    assert start["mcp_tool"] == "git_log"


def test_tool_result_carries_the_text_the_tool_returned(git_tool):
    handler = SSEOutputHandler()
    handler.print_tool_usage(git_tool)
    _drain(handler)
    handler.pretty_print_json(_wrapped(LOG), title="Result")

    (result,) = _drain(handler)
    assert result["mcp_output"] == {"text": LOG, "truncated": False}
    assert result["summary"] == "Commit: abc0 (+5 more lines)"
    assert "returned data" not in result["summary"]


def test_a_long_output_is_capped_and_says_so(git_tool):
    handler = SSEOutputHandler()
    handler.print_tool_usage(git_tool)
    handler.pretty_print_json(_wrapped("x" * (_MCP_OUTPUT_CHAR_CAP + 10)), "Result")

    result = _drain(handler)[-1]
    assert len(result["mcp_output"]["text"]) == _MCP_OUTPUT_CHAR_CAP
    assert result["mcp_output"]["truncated"] is True


def test_a_failed_mcp_call_keeps_its_error_and_carries_no_output(git_tool):
    handler = SSEOutputHandler()
    handler.print_tool_usage(git_tool)
    handler.pretty_print_json(
        {"status": "error", "error": "not a git repository", "data": {}}, "Result"
    )

    result = _drain(handler)[-1]
    assert result["success"] is False
    assert "not a git repository" in result["summary"]
    assert "mcp_output" not in result


def test_a_server_outside_the_catalog_is_named_by_its_config_name(monkeypatch):
    monkeypatch.setattr(connector_registry, "REGISTRY", ConnectorRegistry())
    entry = MCPTool(name="lookup", description="", input_schema={}).to_gaia_format(
        "my_server", "my-server"
    )
    _TOOL_REGISTRY[entry["name"]] = entry
    try:
        handler = SSEOutputHandler()
        handler.print_tool_usage(entry["name"])
        (start,) = _drain(handler)
    finally:
        _TOOL_REGISTRY.pop(entry["name"], None)
    assert start["mcp_server_label"] == "my-server"
    assert start["mcp_tool"] == "lookup"


def test_the_flagship_relay_keeps_the_connector_name_and_output(git_tool):
    """Sidecar handler -> canonical wire -> relay -> the UI's own events."""
    sidecar = SSEOutputHandler()
    sidecar.print_tool_usage(git_tool)
    sidecar.pretty_print_json({"repo_path": "C:/repo", "max_count": 2}, "Arguments")
    sidecar.pretty_print_json(_wrapped(LOG), "Result")
    translator = CanonicalTranslator("run-1", agent_id="gaia")
    wire = [out for ev in _drain(sidecar) for out in translator.translate(ev)]

    ui = SSEOutputHandler()
    for event in wire:
        relay._dispatch_one(ui, event, GAIA_PROFILE)
    events = _drain(ui)

    start = next(e for e in events if e["type"] == "tool_start")
    assert start["mcp_server_label"] == "Git"
    assert start["mcp_tool"] == "git_log"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["mcp_output"]["text"] == LOG
    assert result["summary"] == "Commit: abc0 (+5 more lines)"


def test_a_saved_step_keeps_the_connector_name_and_output():
    step = AgentStepResponse(
        id=1,
        type="tool",
        label="Using mcp_git_git_log",
        mcpServerLabel="Git",
        mcpTool="git_log",
        mcpOutput={"text": LOG, "truncated": False},
    )
    saved = step.model_dump()
    assert saved["mcpServerLabel"] == "Git"
    assert saved["mcpOutput"]["text"] == LOG
