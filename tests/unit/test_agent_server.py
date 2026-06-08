# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the framework generic server (``gaia.agents.base.server``).

Covers the 5-interface standard (#1101) without a live Lemonade server:
the REST API serves a mock agent, MCP stdio answers tools/list & tools/call,
pipe mode reads stdin → stdout, and a manifest-disabled interface fails loudly.
"""

import io
import json

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.server import (
    AgentServer,
    InterfaceNotSupportedError,
    run_agent_cli,
)
from gaia.hub.manifest import AgentManifest


class FakeAgent(Agent):
    """Minimal Agent that skips the heavy LLM/Lemonade ``__init__``.

    Bypassing ``Agent.__init__`` keeps the test hermetic — no Lemonade server,
    no model load — while still passing ``isinstance(agent, Agent)``.
    """

    def __init__(self):  # noqa: D401 - deliberately skip super().__init__
        self.calls = []

    def _register_tools(self):  # abstract on Agent; no tools needed here
        pass

    def process_query(self, user_input, **kwargs):
        self.calls.append(user_input)
        return {"status": "success", "result": f"echo: {user_input}"}

    def get_tools_info(self):
        return {
            "greet": {
                "name": "greet",
                "description": "Greet someone by name.\nSecond line ignored.",
                "parameters": {"name": {"type": "string", "required": True}},
            }
        }

    def _execute_tool(self, tool_name, tool_args):
        if tool_name == "greet":
            return {"status": "success", "result": f"Hello {tool_args.get('name')}"}
        return {"status": "error", "error": f"unknown tool {tool_name}"}


def _manifest(**interface_flags) -> AgentManifest:
    """Build a valid manifest with the given ``interfaces`` flags."""
    return AgentManifest.from_dict(
        {
            "id": "fake",
            "name": "Fake",
            "version": "0.1.0",
            "description": "Test agent",
            "author": "AMD",
            "license": "MIT",
            "language": "python",
            "interfaces": interface_flags,
        }
    )


@pytest.fixture
def server():
    return AgentServer(FakeAgent(), name="Fake")


# ---------------------------------------------------------------------------
# Construction / interface gating
# ---------------------------------------------------------------------------


def test_rejects_non_agent():
    with pytest.raises(TypeError, match="requires a gaia Agent"):
        AgentServer(object())


def test_default_model_id_strips_agent_suffix():
    assert AgentServer(FakeAgent()).model_id == "gaia-fake"


def test_no_manifest_allows_all_interfaces(server):
    # No manifest → every interface permitted; should not raise.
    server._ensure_interface("api_server")
    server._ensure_interface("mcp_server")


def test_unsupported_interface_raises_actionable_error():
    server = AgentServer(FakeAgent(), manifest=_manifest(cli=True, api_server=False))
    with pytest.raises(InterfaceNotSupportedError) as exc:
        server.build_api_app()
    msg = str(exc.value)
    assert "api_server" in msg
    assert "interfaces.api_server: true" in msg  # tells the author what to fix


def test_supported_interface_passes():
    server = AgentServer(FakeAgent(), manifest=_manifest(api_server=True))
    app = server.build_api_app()
    assert app is not None


# ---------------------------------------------------------------------------
# REST API mode
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(server):
    from fastapi.testclient import TestClient

    return TestClient(server.build_api_app())


def test_api_health(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_api_lists_single_model(api_client):
    resp = api_client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [m["id"] for m in data] == ["gaia-fake"]


def test_api_lists_tools(api_client):
    resp = api_client.get("/v1/tools")
    assert resp.status_code == 200
    tools = resp.json()["tools"]
    assert tools[0]["name"] == "greet"
    assert tools[0]["inputSchema"]["required"] == ["name"]


def test_api_chat_completion(api_client):
    resp = api_client.post(
        "/v1/chat/completions",
        json={
            "model": "gaia-fake",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "gaia-fake"
    assert body["choices"][0]["message"]["content"] == "echo: hello"
    assert body["usage"]["total_tokens"] > 0


def test_api_chat_completion_unknown_model_404(api_client):
    resp = api_client.post(
        "/v1/chat/completions",
        json={"model": "gaia-other", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404


def test_api_chat_completion_no_user_message_400(api_client):
    resp = api_client.post(
        "/v1/chat/completions",
        json={"model": "gaia-fake", "messages": [{"role": "system", "content": "x"}]},
    )
    assert resp.status_code == 400


def test_api_streaming(api_client):
    resp = api_client.post(
        "/v1/chat/completions",
        json={
            "model": "gaia-fake",
            "messages": [{"role": "user", "content": "stream me"}],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert "echo: stream me" in resp.text
    assert "data: [DONE]" in resp.text


# ---------------------------------------------------------------------------
# MCP stdio mode
# ---------------------------------------------------------------------------


def test_mcp_initialize(server):
    resp = server.handle_mcp_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "Fake"
    assert "tools" in resp["result"]["capabilities"]


def test_mcp_tools_list(server):
    resp = server.handle_mcp_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    )
    tools = resp["result"]["tools"]
    assert tools[0]["name"] == "greet"
    assert tools[0]["description"].startswith("Greet someone by name.")


def test_mcp_tools_call(server):
    resp = server.handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "greet", "arguments": {"name": "Ada"}},
        }
    )
    result = resp["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["result"] == "Hello Ada"


def test_mcp_tools_call_error_flags_iserror(server):
    resp = server.handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        }
    )
    assert resp["result"]["isError"] is True


def test_mcp_unknown_method_returns_error(server):
    resp = server.handle_mcp_request(
        {"jsonrpc": "2.0", "id": 5, "method": "bogus/method"}
    )
    assert resp["error"]["code"] == -32601


def test_mcp_notification_returns_none(server):
    # A request without an id is a notification — no response.
    assert (
        server.handle_mcp_request({"jsonrpc": "2.0", "method": "initialized"}) is None
    )


def test_mcp_run_over_stdio(server):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "greet", "arguments": {"name": "Grace"}},
        },
    ]
    stdin = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
    stdout = io.StringIO()
    server.run_mcp(stdin=stdin, stdout=stdout)

    lines = [
        json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()
    ]
    assert lines[0]["result"]["tools"][0]["name"] == "greet"
    assert "Grace" in lines[1]["result"]["content"][0]["text"]


def test_mcp_run_handles_malformed_json(server):
    stdin = io.StringIO("not json\n")
    stdout = io.StringIO()
    server.run_mcp(stdin=stdin, stdout=stdout)
    resp = json.loads(stdout.getvalue())
    assert resp["error"]["code"] == -32700


def test_mcp_prompts_list_from_manifest():
    manifest = AgentManifest.from_dict(
        {
            "id": "fake",
            "name": "Fake",
            "version": "0.1.0",
            "description": "Test agent",
            "author": "AMD",
            "license": "MIT",
            "language": "python",
            "conversation_starters": ["Summarize a file", "Answer a question"],
            "interfaces": {"mcp_server": True},
        }
    )
    server = AgentServer(FakeAgent(), manifest=manifest)
    resp = server.handle_mcp_request(
        {"jsonrpc": "2.0", "id": 9, "method": "prompts/list"}
    )
    prompts = resp["result"]["prompts"]
    assert [p["description"] for p in prompts] == [
        "Summarize a file",
        "Answer a question",
    ]


# ---------------------------------------------------------------------------
# Pipe mode
# ---------------------------------------------------------------------------


def test_pipe_reads_stdin_writes_stdout(server):
    stdin = io.StringIO("what is two plus two\n")
    stdout = io.StringIO()
    server.run_pipe(stdin=stdin, stdout=stdout)
    assert stdout.getvalue().strip() == "echo: what is two plus two"


def test_pipe_empty_stdin_fails_loudly(server):
    with pytest.raises(ValueError, match="empty stdin"):
        server.run_pipe(stdin=io.StringIO("   \n"), stdout=io.StringIO())


# ---------------------------------------------------------------------------
# CLI mode + run_agent_cli dispatch
# ---------------------------------------------------------------------------


def test_cli_mode_returns_content(server):
    assert server.run_cli("ping") == "echo: ping"


def test_run_agent_cli_prompt(capsys):
    agent = FakeAgent()
    code = run_agent_cli(agent, ["--prompt", "hello there"])
    assert code == 0
    assert agent.calls == ["hello there"]
    assert "echo: hello there" in capsys.readouterr().out


def test_run_agent_cli_pipe(monkeypatch):
    agent = FakeAgent()
    monkeypatch.setattr("sys.stdin", io.StringIO("piped input\n"))
    code = run_agent_cli(agent, ["--pipe"])
    assert code == 0
    assert agent.calls == ["piped input"]


def test_run_agent_cli_unsupported_interface_returns_1(capsys):
    agent = FakeAgent()
    manifest = _manifest(tui=True, api_server=False)
    code = run_agent_cli(agent, ["--api"], manifest=manifest)
    assert code == 1
    assert "api_server" in capsys.readouterr().err
