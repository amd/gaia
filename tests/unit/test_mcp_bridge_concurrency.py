#!/usr/bin/env python
#
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A busy or stalled caller must not take the ``gaia mcp`` bridge offline.

The bridge serves one shared Lemonade, so a single tool call can sit for
minutes behind another app's request. ``/health`` and ``/status`` (what
``gaia mcp status`` reads) have to keep answering meanwhile, or the bridge is
reported down while it is only busy.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gaia.mcp import mcp_bridge
from gaia.mcp.mcp_bridge import GAIAMCPBridge, MCPHTTPHandler

pytestmark = pytest.mark.allow_network

REPO_SRC = Path(__file__).resolve().parents[2] / "src"

# Runs the real ``start_server`` with an LLM client whose call takes 30 s, the
# way a request queued behind another app on a busy Lemonade does.
_SLOW_BRIDGE = """
import sys, time
from pathlib import Path
from gaia.mcp import mcp_bridge

port, marker = int(sys.argv[1]), Path(sys.argv[2])

class SlowLLM:
    def generate(self, **_kwargs):
        marker.write_text("started")
        time.sleep(30)
        return "done"

mcp_bridge.create_client = lambda *_a, **_k: SlowLLM()
mcp_bridge.start_server("127.0.0.1", port)
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, json.loads(response.read())


def _post_json(url, payload, timeout):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture(name="slow_bridge")
def _slow_bridge(tmp_path):
    port = _free_port()
    marker = tmp_path / "llm_started"
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(REPO_SRC),
            "GAIA_HOME": str(tmp_path / "gaia_home"),
            # Nothing listens here; the fake client never dials it anyway.
            "LEMONADE_BASE_URL": "http://127.0.0.1:9/api/v1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    env.pop("GAIA_MCP_AUTH_TOKEN", None)
    proc = subprocess.Popen(
        [sys.executable, "-c", _SLOW_BRIDGE, str(port), str(marker)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                _get(f"{base}/health", timeout=2)
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"bridge did not start (exit code {proc.poll()})")
                time.sleep(0.2)
        yield base, marker
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _start_slow_llm_call(base, marker):
    """POST /llm in the background and wait until the bridge is inside it."""

    def _call():
        try:
            _post_json(f"{base}/llm", {"query": "hi"}, 60)
        except OSError:
            pass  # The fixture kills the bridge mid-call at teardown.

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not marker.exists():
        assert time.monotonic() < deadline, "the /llm call never reached the LLM"
        time.sleep(0.05)


def test_health_answers_while_an_llm_call_is_busy(slow_bridge):
    base, marker = slow_bridge
    _start_slow_llm_call(base, marker)

    status, body = _get(f"{base}/health", timeout=2)

    assert status == 200
    assert body["status"] == "healthy"


def test_status_answers_while_an_llm_call_is_busy(slow_bridge):
    # `gaia mcp status` reads /status with a 3 s timeout.
    base, marker = slow_bridge
    _start_slow_llm_call(base, marker)

    status, body = _get(f"{base}/status", timeout=3)

    assert status == 200
    assert body["status"] == "healthy"


def test_health_answers_while_a_client_stalls_mid_body(slow_bridge):
    base, _marker = slow_bridge
    port = int(base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as stalled:
        stalled.sendall(
            b"POST /llm HTTP/1.1\r\nHost: x\r\n"
            b"Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{"
        )
        time.sleep(0.5)

        status, _body = _get(f"{base}/health", timeout=2)

    assert status == 200


# In-process tests against the server the bridge really builds.


@pytest.fixture(name="serve")
def _serve():
    servers = []

    def _start(bridge):
        httpd = mcp_bridge.create_server(bridge, "127.0.0.1", 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append((httpd, thread))
        return f"http://127.0.0.1:{httpd.server_port}"

    yield _start
    for httpd, thread in servers:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_a_stalled_body_is_answered_408_and_released(serve, monkeypatch):
    monkeypatch.setattr(MCPHTTPHandler, "timeout", 0.5)
    base = serve(GAIAMCPBridge(port=0))
    port = int(base.rsplit(":", 1)[1])

    with socket.create_connection(("127.0.0.1", port), timeout=5) as stalled:
        stalled.sendall(
            b"POST /llm HTTP/1.1\r\nHost: x\r\n"
            b"Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{"
        )
        reply = stalled.recv(4096)

    assert reply.startswith(b"HTTP/1.0 408")


def test_a_stalled_request_line_is_dropped_without_a_traceback(
    serve, monkeypatch, capsys
):
    monkeypatch.setattr(MCPHTTPHandler, "timeout", 0.5)
    base = serve(GAIAMCPBridge(port=0))
    port = int(base.rsplit(":", 1)[1])

    with socket.create_connection(("127.0.0.1", port), timeout=5) as stalled:
        stalled.sendall(b"POST /llm HTTP/1.1\r\nHost: x\r\n")
        assert stalled.recv(4096) == b""

    _get(f"{base}/health", timeout=2)  # the handler thread has finished logging
    assert "Traceback" not in capsys.readouterr().err


def test_handler_socket_reads_are_bounded():
    assert MCPHTTPHandler.timeout is not None
    assert 0 < MCPHTTPHandler.timeout <= 120


def test_handler_threads_do_not_block_shutdown():
    httpd = mcp_bridge.create_server(GAIAMCPBridge(port=0), "127.0.0.1", 0)
    try:
        assert httpd.daemon_threads is True
    finally:
        httpd.server_close()


class _CountingChatSDK:
    """AgentSDK stand-in that records whether two sends ever overlap."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.history = []
        self._guard = threading.Lock()

    def send(self, query):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.2)
        self.history.append(query)
        with self._guard:
            self.active -= 1
        return type("Reply", (), {"text": f"re: {query}"})()


def test_concurrent_chats_share_one_conversation_in_turn(serve, monkeypatch):
    built = []

    def _make_sdk(*_args, **_kwargs):
        sdk = _CountingChatSDK()
        built.append(sdk)
        return sdk

    import gaia.chat.sdk as chat_sdk_module

    monkeypatch.setattr(chat_sdk_module, "AgentSDK", _make_sdk)
    monkeypatch.setattr(chat_sdk_module, "AgentConfig", lambda: None)
    base = serve(GAIAMCPBridge(port=0))

    results = []

    def _chat(i):
        results.append(_post_json(f"{base}/chat", {"query": f"q{i}"}, 10))

    threads = [threading.Thread(target=_chat, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert [status for status, _ in results] == [200] * 4
    assert len(built) == 1, "each concurrent first chat built its own AgentSDK"
    assert built[0].max_active == 1, "two chats wrote one history at once"
    assert sorted(built[0].history) == ["q0", "q1", "q2", "q3"]


def test_concurrent_queries_build_one_llm_client(serve, monkeypatch):
    built = []
    gate = threading.Barrier(4, timeout=5)

    class _LLM:
        def generate(self, prompt, **_kwargs):
            return f"echo: {prompt}"

    def _make_client(*_args, **_kwargs):
        built.append(True)
        time.sleep(0.2)
        return _LLM()

    monkeypatch.setattr(mcp_bridge, "create_client", _make_client)
    base = serve(GAIAMCPBridge(port=0))
    results = []

    def _query(i):
        gate.wait()
        results.append(_post_json(f"{base}/llm", {"query": f"q{i}"}, 10))

    threads = [threading.Thread(target=_query, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert [status for status, _ in results] == [200] * 4
    assert len(built) == 1


def test_concurrent_queries_take_turns_on_the_shared_client(serve, monkeypatch):
    # The provider keeps per-call state on the instance, so overlapping
    # generate() calls could hand one caller the other's answer.
    state = {"active": 0, "max_active": 0}
    guard = threading.Lock()

    class _LLM:
        def generate(self, prompt, **_kwargs):
            with guard:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.2)
            with guard:
                state["active"] -= 1
            return f"echo: {prompt}"

    monkeypatch.setattr(mcp_bridge, "create_client", lambda *_a, **_k: _LLM())
    base = serve(GAIAMCPBridge(port=0))
    results = []

    def _query(i):
        results.append(_post_json(f"{base}/llm", {"query": f"q{i}"}, 10))

    threads = [threading.Thread(target=_query, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert sorted(body["result"] for _, body in results) == [
        "echo: q0",
        "echo: q1",
        "echo: q2",
    ]
    assert state["max_active"] == 1
