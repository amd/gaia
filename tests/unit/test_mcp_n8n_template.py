# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The shipped n8n workflow must only call what the MCP bridge serves (#3308).

Each HTTP node in ``src/gaia/mcp/n8n.json`` is replayed against the real bridge
server, so a node aimed at a removed route or tool fails here instead of in a
user's imported workflow.
"""

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pytest

import gaia.mcp
from gaia.mcp.mcp_bridge import GAIAMCPBridge, create_server

pytestmark = pytest.mark.allow_network

TEMPLATE = Path(gaia.mcp.__file__).parent / "n8n.json"
_EXPRESSION = re.compile(r"\{\{.*?\}\}")


def _http_nodes():
    workflow = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    nodes = [n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]
    assert nodes, f"no HTTP request nodes in {TEMPLATE}"
    return nodes


def _request_body(params):
    """The JSON body n8n would send, with ``{{ }}`` expressions filled in."""
    if not params.get("sendBody"):
        return None
    if params.get("specifyBody") == "json":
        return json.loads(_EXPRESSION.sub("expr", params["jsonBody"]))
    return {
        p["name"]: _EXPRESSION.sub("expr", p["value"])
        for p in params["bodyParameters"]["parameters"]
    }


@pytest.fixture(name="bridge_url")
def _bridge_url(monkeypatch):
    bridge = GAIAMCPBridge(port=0)
    calls = []

    def fake_execute(tool_name, arguments, client_gone=None):
        calls.append((tool_name, arguments))
        return {"success": True, "result": "ok"}

    monkeypatch.setattr(bridge, "execute_tool", fake_execute)
    httpd = create_server(bridge, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", bridge, calls
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_template_targets_the_default_bridge_port():
    for node in _http_nodes():
        url = urlparse(node["parameters"]["url"])
        assert (url.hostname, url.port) == ("localhost", 8765), node["name"]


@pytest.mark.parametrize("node", _http_nodes(), ids=lambda n: n["name"])
def test_every_node_hits_a_route_and_tool_the_bridge_serves(node, bridge_url):
    base, bridge, calls = bridge_url
    params = node["parameters"]
    body = _request_body(params)
    request = urllib.request.Request(
        base + urlparse(params["url"]).path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=params["method"],
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status, reply = response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        status, reply = e.code, json.loads(e.read())

    assert status == 200, f"{node['name']}: {params['url']} -> {status} {reply}"
    assert "error" not in reply, f"{node['name']}: {reply}"

    for tool_name, arguments in calls:
        schema = bridge.tools[tool_name]["inputSchema"]
        assert set(schema.get("required", [])) <= set(arguments), node["name"]
        assert set(arguments) <= set(schema["properties"]), node["name"]


def test_connections_link_nodes_by_name():
    """n8n links nodes by name; an id here imports as an unconnected node."""
    workflow = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    names = {node["name"] for node in workflow["nodes"]}
    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for target in branch:
                assert target["node"] in names
