# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Streaming ``/v1/chat/completions`` on client disconnect and agent failure (#4210).

The requests go through the raw ASGI app, so the disconnect arrives the way
uvicorn delivers it: an ``http.disconnect`` under ASGI spec 2.3, which makes
Starlette cancel the streaming response.
"""

import asyncio
import json
import threading

import pytest

from gaia.agents.base.agent import Agent
from gaia.api import openai_server
from gaia.api.openai_server import app
from gaia.api.sse_handler import SSEOutputHandler


class _ProbeAgent(Agent):
    def _register_tools(self):
        pass


def _probe_agent():
    return _ProbeAgent(
        skip_lemonade=True, silent_mode=True, output_handler=SSEOutputHandler()
    )


_PAYLOAD = {
    "model": "gaia",
    "stream": True,
    "messages": [{"role": "user", "content": "take your time"}],
}


async def _run_asgi(disconnect_after_first_chunk):
    """POST ``_PAYLOAD`` and return the response body as text."""
    body = json.dumps(_PAYLOAD).encode()
    pending = [{"type": "http.request", "body": body, "more_body": False}]
    first_chunk = asyncio.Event()
    chunks = []

    async def receive():
        if pending:
            return pending.pop(0)
        if disconnect_after_first_chunk:
            await first_chunk.wait()
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"].decode())
            first_chunk.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8080),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return "".join(chunks)


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")


def test_client_disconnect_cancels_the_agent(mocker):
    agent = _probe_agent()
    llm_started = threading.Event()
    released_by_cancel = threading.Event()

    def slow_chat(messages, model=None, stream=False, **kwargs):
        llm_started.set()
        cancel = agent._cancel_event
        if cancel is not None and cancel.wait(timeout=5):
            released_by_cancel.set()
        return "late answer"

    agent.chat.llm_client.chat = slow_chat
    mocker.patch.object(openai_server.registry, "get_agent", return_value=agent)

    asyncio.run(_run_asgi(disconnect_after_first_chunk=True))

    assert llm_started.wait(timeout=5)
    assert released_by_cancel.wait(timeout=5), "agent was never told to stop"
    assert agent.console.cancelled.is_set()


def test_agent_failure_ends_the_stream_with_an_error_and_done(mocker):
    agent = _probe_agent()
    mocker.patch.object(agent, "process_query", side_effect=RuntimeError("boom"))
    mocker.patch.object(openai_server.registry, "get_agent", return_value=agent)

    body = asyncio.run(_run_asgi(disconnect_after_first_chunk=False))

    events = [line[len("data: ") :] for line in body.split("\n") if line]
    assert events[-1] == "[DONE]", body
    error = json.loads(events[-2])
    assert error["error"]["type"] == "server_error"
    assert "boom" in error["error"]["message"]
    # A failed stream must not also claim it finished normally.
    assert '"finish_reason": "stop"' not in body


def test_successful_stream_is_unchanged(mocker):
    agent = _probe_agent()
    agent.chat.llm_client.chat = lambda *a, **k: "fine answer"
    mocker.patch.object(openai_server.registry, "get_agent", return_value=agent)

    body = asyncio.run(_run_asgi(disconnect_after_first_chunk=False))

    assert body.rstrip().endswith("data: [DONE]")
    assert '"finish_reason": "stop"' in body
    assert '"error"' not in body
    assert not agent.console.cancelled.is_set()
