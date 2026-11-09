# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The n8n workflow GAIA ships only calls routes and tools the bridge serves."""

import json
from pathlib import Path
from urllib.parse import urlparse

import gaia.mcp
from gaia.mcp.mcp_bridge import GAIAMCPBridge

WORKFLOW = Path(gaia.mcp.__file__).parent / "n8n.json"

# The routes MCPHTTPHandler.do_GET / do_POST answer.
SERVED = {
    ("GET", "/health"),
    ("GET", "/tools"),
    ("GET", "/v1/tools"),
    ("GET", "/status"),
    ("POST", "/"),
    ("POST", "/v1/messages"),
    ("POST", "/rpc"),
    ("POST", "/chat"),
    ("POST", "/llm"),
}


def _workflow():
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def _http_nodes(workflow):
    return [n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]


def test_every_request_targets_a_served_route():
    for node in _http_nodes(_workflow()):
        params = node["parameters"]
        route = (params["method"], urlparse(params["url"]).path)
        assert route in SERVED, f"{node['name']} calls {route}, which the bridge 404s"


def test_every_json_rpc_tool_is_registered():
    tools = GAIAMCPBridge(port=0).tools
    calls = [
        json.loads(n["parameters"]["jsonBody"])
        for n in _http_nodes(_workflow())
        if "jsonBody" in n["parameters"]
    ]
    assert calls, "the example should show a JSON-RPC tools/call"
    for call in calls:
        assert call["method"] == "tools/call"
        assert call["params"]["name"] in tools


def test_connections_name_real_nodes():
    workflow = _workflow()
    names = {n["name"] for n in workflow["nodes"]}
    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for target in branch:
                assert target["node"] in names
