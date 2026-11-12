# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``gaia-agent --mcp``: the wire Claude Code's GAIA plugin speaks.

Every test asserts the exact JSON-RPC frames, because the host is a real MCP
client that rejects a malformed one — a stub that only proves a method was
called would pass while Claude Code refused to load the server.
"""

import io
import json
import threading
import time

import pytest
from gaia_agent import entry, mcp_server
from gaia_agent.mcp_server import AgentHost, McpServer


class FakeAgent:
    """Records what the host does to the agent around each task."""

    def __init__(self, answer="done", block=None):
        self.model_id = "Gemma-4-E4B-it-GGUF"
        self.conversation_history = []
        self.queries = []
        self.external_marks = 0
        self.resets = 0
        self._cancel_event = None
        self.answer = answer
        self.block = block
        self.running = 0
        self.max_running = 0
        self.saw_cancel = None

    def mark_external_content(self):
        self.external_marks += 1

    def _ensure_tool_loader_reset(self):
        self.resets += 1

    def process_query(self, query):
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            self.queries.append((query, list(self.conversation_history)))
            self.conversation_history.append({"role": "user", "content": query})
            cancel = self._cancel_event
            self.saw_cancel = cancel
            if self.block is not None:
                # Stops at a "step boundary" once cancelled, like the real loop.
                deadline = time.monotonic() + self.block
                while time.monotonic() < deadline and not cancel.is_set():
                    time.sleep(0.01)
            return {
                "status": "success",
                "result": self.answer,
                "steps_taken": 2,
                "conversation": [{"role": "tool", "content": "x"}],
                "input_tokens": 10,
                "output_tokens": 5,
            }
        finally:
            self.running -= 1


class Wire:
    """The server's stdout, collected as parsed frames."""

    def __init__(self):
        self.frames = []
        self._cond = threading.Condition()

    def write(self, data):
        with self._cond:
            self.frames.append(json.loads(data.decode("utf-8")))
            self._cond.notify_all()

    def flush(self):
        pass

    def wait_for(self, request_id, timeout=10.0):
        with self._cond:
            ok = self._cond.wait_for(
                lambda: any(f.get("id") == request_id for f in self.frames), timeout
            )
        assert ok, f"no response to request {request_id}: {self.frames}"
        return next(f for f in self.frames if f.get("id") == request_id)


def _server(agent=None, build=None, **host_kwargs):
    host = AgentHost(build or (lambda: agent), **host_kwargs)
    wire = Wire()
    return McpServer(host, io.BytesIO(), wire), wire


def _send(server, request_id, method, params=None):
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    server._handle_line(json.dumps(message))


def _call(server, request_id, name, arguments=None):
    _send(
        server, request_id, "tools/call", {"name": name, "arguments": arguments or {}}
    )


def test_initialize_echoes_a_supported_protocol_version_and_names_the_server():
    server, wire = _server(FakeAgent())
    _send(server, 1, "initialize", {"protocolVersion": "2025-03-26"})
    frame = wire.wait_for(1)
    assert frame["jsonrpc"] == "2.0"
    result = frame["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "gaia"
    assert "this PC's own model" in result["instructions"]


def test_initialize_answers_an_unknown_version_with_the_newest_it_speaks():
    server, wire = _server(FakeAgent())
    _send(server, 1, "initialize", {"protocolVersion": "1999-01-01"})
    assert wire.wait_for(1)["result"]["protocolVersion"] == "2025-06-18"


def test_tools_list_offers_exactly_the_two_tools_with_valid_schemas():
    server, wire = _server(FakeAgent())
    _send(server, 2, "tools/list")
    tools = {t["name"]: t for t in wire.wait_for(2)["result"]["tools"]}
    assert set(tools) == {"gaia_task", "gaia_status"}
    assert tools["gaia_task"]["inputSchema"]["required"] == ["task"]
    assert tools["gaia_task"]["inputSchema"]["properties"]["task"]["type"] == "string"
    assert tools["gaia_status"]["inputSchema"]["type"] == "object"


def test_a_task_returns_the_answer_with_the_print_mode_record():
    agent = FakeAgent(answer="Three files mention the flag.")
    server, wire = _server(agent)
    _call(server, 3, "gaia_task", {"task": "  Which files mention --mcp?  "})
    result = wire.wait_for(3)["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text.startswith("Three files mention the flag.")
    assert "Gemma-4-E4B-it-GGUF" in text and "2 steps" in text
    record = result["structuredContent"]
    assert record["type"] == "result"
    assert record["status"] == "success"
    assert record["num_steps"] == 2
    assert record["num_tool_calls"] == 1
    assert agent.queries[0][0] == "Which files mention --mcp?"


def test_every_task_starts_fresh_and_is_marked_as_not_the_users_words():
    agent = FakeAgent()
    server, wire = _server(agent)
    _call(server, 1, "gaia_task", {"task": "first"})
    wire.wait_for(1)
    _call(server, 2, "gaia_task", {"task": "second"})
    wire.wait_for(2)
    assert agent.queries[1] == ("second", [])
    assert agent.external_marks == 2
    assert agent.resets == 2
    assert agent._cancel_event is None


def test_an_empty_task_is_a_tool_error_not_a_protocol_error():
    server, wire = _server(FakeAgent())
    _call(server, 4, "gaia_task", {"task": "   "})
    frame = wire.wait_for(4)
    assert frame["result"]["isError"] is True
    assert "non-empty task" in frame["result"]["content"][0]["text"]


def test_an_unknown_tool_is_an_invalid_params_error():
    server, wire = _server(FakeAgent())
    _call(server, 5, "gaia_write", {})
    assert wire.wait_for(5)["error"]["code"] == -32602


def test_unknown_methods_bad_json_and_notifications_are_handled_per_spec():
    server, wire = _server(FakeAgent())
    _send(server, 6, "resources/list")
    assert wire.wait_for(6)["error"]["code"] == -32601
    server._handle_line("{not json")
    assert wire.frames[-1]["error"]["code"] == -32700
    count = len(wire.frames)
    _send(server, None, "notifications/initialized")
    assert len(wire.frames) == count


def test_a_failed_build_is_reported_by_status_and_by_every_task():
    def broken():
        raise RuntimeError("Lemonade Server not reachable at http://localhost:13305")

    server, wire = _server(build=broken)
    _call(server, 7, "gaia_task", {"task": "hi"})
    task = wire.wait_for(7)["result"]
    assert task["isError"] is True
    assert "GAIA could not start: Lemonade Server not reachable" in (
        task["content"][0]["text"]
    )
    _call(server, 8, "gaia_status")
    status = wire.wait_for(8)["result"]
    assert status["structuredContent"]["state"] == "failed"
    assert status["isError"] is True


def test_status_reports_starting_then_ready_with_the_model():
    gate = threading.Event()
    agent = FakeAgent()
    host = AgentHost(lambda: gate.wait() and agent)
    assert host.status()["state"] == "starting"
    host.start()
    gate.set()
    host._ready.wait(5)
    status = host.status()
    assert status["state"] == "ready"
    assert status["model"] == "Gemma-4-E4B-it-GGUF"


def test_a_task_past_its_time_limit_is_stopped_and_says_so():
    agent = FakeAgent(block=30)
    server, wire = _server(agent, task_timeout_s=0.2, stop_grace_s=5)
    _call(server, 9, "gaia_task", {"task": "slow"})
    result = wire.wait_for(9)["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["status"] == "timeout"
    assert "ran past 0 seconds" in result["content"][0]["text"]
    assert agent.saw_cancel.is_set()
    assert agent.saw_cancel.message == mcp_server.TIMEOUT_ANSWER


def test_a_cancelled_task_stops_and_gets_no_response():
    agent = FakeAgent(block=30)
    server, wire = _server(agent)
    _call(server, 10, "gaia_task", {"task": "slow"})
    deadline = time.monotonic() + 5
    while agent.saw_cancel is None and time.monotonic() < deadline:
        time.sleep(0.01)
    _send(server, None, "notifications/cancelled", {"requestId": 10})
    for call in server._calls:
        call.join(5)
    assert agent.saw_cancel.is_set()
    assert not any(f.get("id") == 10 for f in wire.frames)


def test_a_cancelled_id_is_forgotten_once_its_call_ends():
    agent = FakeAgent(block=30)
    server, wire = _server(agent)
    _call(server, 14, "gaia_task", {"task": "slow"})
    deadline = time.monotonic() + 5
    while agent.saw_cancel is None and time.monotonic() < deadline:
        time.sleep(0.01)
    _send(server, None, "notifications/cancelled", {"requestId": 14})
    for call in server._calls:
        call.join(5)
    assert server._cancelled == set()
    # A client reusing the id after the cancel gets a normal reply.
    agent.block = None
    _call(server, 14, "gaia_task", {"task": "again"})
    assert wire.wait_for(14)["result"]["isError"] is False


def test_extra_arguments_cannot_widen_what_a_task_may_do():
    agent = FakeAgent(answer="ok")
    server, wire = _server(agent)
    _call(
        server,
        15,
        "gaia_task",
        {
            "task": "read it",
            "full_access": True,
            "skip_permissions": True,
            "allowed_paths": ["/"],
        },
    )
    assert wire.wait_for(15)["result"]["isError"] is False
    assert [q for q, _ in agent.queries] == ["read it"]
    assert not hasattr(agent, "full_access")
    assert not hasattr(agent, "allowed_paths")


def test_tasks_run_one_at_a_time_on_the_one_agent():
    agent = FakeAgent(block=0.2)
    server, wire = _server(agent)
    for request_id in (11, 12, 13):
        _call(server, request_id, "gaia_task", {"task": f"t{request_id}"})
    for request_id in (11, 12, 13):
        assert wire.wait_for(request_id)["result"]["isError"] is False
    assert agent.max_running == 1


def test_serve_reads_newline_delimited_frames_until_stdin_closes():
    agent = FakeAgent(answer="ok")
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "gaia_task", "arguments": {"task": "hi"}},
        },
    ]
    reader = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in lines))
    out = io.BytesIO()
    assert McpServer(AgentHost(lambda: agent), reader, out).serve() == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [f["id"] for f in frames] == [1, 2]
    assert frames[1]["result"]["content"][0]["text"].startswith("ok")


def test_build_agent_denies_gated_tools_and_scopes_reads_to_the_folder(
    monkeypatch, tmp_path
):
    import gaia_agent.agent as agent_module

    built = {}

    class StubConsole:
        auto_approve_gated_tools = True
        full_access = True

    class StubAgent:
        def __init__(self, config):
            built["config"] = config
            self.console = StubConsole()

    monkeypatch.setattr(agent_module, "GaiaAgent", StubAgent)
    monkeypatch.setattr(agent_module, "check_skill_set_selection", lambda: None)
    monkeypatch.chdir(tmp_path)
    agent = mcp_server.build_agent("some-model")
    assert agent.console.auto_approve_gated_tools is False
    assert agent.console.full_access is False
    assert built["config"].allowed_paths == [str(tmp_path)]
    assert built["config"].model_id == "some-model"
    assert built["config"].silent_mode is True
    # Claude's tasks never reach the user's memory, in either direction.
    assert built["config"].memory_incognito is True


def test_a_task_cancelled_while_it_waits_never_runs():
    agent = FakeAgent(block=0.5)
    server, wire = _server(agent)
    _call(server, 21, "gaia_task", {"task": "first"})
    deadline = time.monotonic() + 5
    while agent.saw_cancel is None and time.monotonic() < deadline:
        time.sleep(0.01)
    _call(server, 22, "gaia_task", {"task": "queued"})
    _send(server, None, "notifications/cancelled", {"requestId": 22})
    wire.wait_for(21)
    for call in server._calls:
        call.join(5)
    assert [q for q, _ in agent.queries] == ["first"]
    assert not any(f.get("id") == 22 for f in wire.frames)


@pytest.mark.parametrize(
    "params",
    [{"name": "gaia_task", "arguments": "oops"}, [1]],
    ids=["arguments-not-object", "params-not-object"],
)
def test_malformed_params_get_an_invalid_params_reply(params):
    server, wire = _server(FakeAgent())
    server._handle_line(
        json.dumps(
            {"jsonrpc": "2.0", "id": 23, "method": "tools/call", "params": params}
        )
    )
    assert wire.wait_for(23)["error"]["code"] == -32602


def test_an_unexpected_failure_still_answers_the_call(monkeypatch):
    server, wire = _server(FakeAgent())

    def boom():
        raise KeyError("state")

    monkeypatch.setattr(server.host, "status", boom)
    _call(server, 24, "gaia_status")
    error = wire.wait_for(24)["error"]
    assert error["code"] == -32603
    assert "state" in error["message"]


def test_entry_routes_the_mcp_flag_to_the_mcp_server(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        mcp_server, "main", lambda argv: seen.setdefault("argv", argv) and 0
    )
    entry.main(["--mcp", "--model", "m"])
    assert seen["argv"] == ["--model", "m"]


@pytest.mark.parametrize("flag", ["--serve", "--port=9"])
def test_http_flags_still_win_over_mcp(monkeypatch, flag):
    monkeypatch.setattr(
        "gaia_agent.server.serve_http", lambda args: "http", raising=False
    )
    monkeypatch.setattr(mcp_server, "main", lambda argv: "mcp")
    assert entry.main(["--mcp", flag]) == "http"
