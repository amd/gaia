# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Each request builds its own agent, so each request must close it."""

import asyncio
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from gaia.api import openai_server
from gaia.api.openai_server import app
from gaia.api.schemas import ChatCompletionRequest
from gaia.api.sse_handler import SSEOutputHandler


class _FakeAgent:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = 0
        self.output_handler = SSEOutputHandler()
        self.console = self.output_handler
        self.chat = types.SimpleNamespace(config=types.SimpleNamespace())

    def set_caller_system_prompt(self, _text):
        pass

    def mark_external_content(self):
        pass

    def process_query(self, _query):
        if self.fail:
            raise RuntimeError("model unreachable")
        return {"result": "hi", "status": "success"}

    def close(self):
        self.closed += 1


def _body(stream):
    return {
        "model": "gaia",
        "stream": stream,
        "messages": [{"role": "user", "content": "hello"}],
    }


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_agent_is_closed_after_the_request(client, mocker, stream, fail):
    agent = _FakeAgent(fail=fail)
    mocker.patch.object(openai_server.registry, "get_agent", return_value=agent)

    client.post("/v1/chat/completions", json=_body(stream))

    assert agent.closed == 1


class _BlockingAgent(_FakeAgent):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def process_query(self, _query):
        self.started.set()
        assert self.release.wait(10), "test never released the turn"
        return {"result": "hi", "status": "success"}


async def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


def test_stream_hang_up_closes_only_after_the_turn_ends():
    async def scenario():
        agent = _BlockingAgent()
        stream = openai_server.create_sse_stream(agent, "hello", "gaia")
        await stream.__anext__()
        pending = asyncio.ensure_future(stream.__anext__())
        await _until(agent.started.is_set)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()
        await asyncio.sleep(0.2)
        assert agent.closed == 0
        agent.release.set()
        await _until(lambda: agent.closed == 1)

    asyncio.run(scenario())


def test_cancelled_request_closes_only_after_the_turn_ends(mocker):
    async def scenario():
        agent = _BlockingAgent()
        mocker.patch.object(openai_server.registry, "get_agent", return_value=agent)
        request = ChatCompletionRequest(**_body(False))
        call = asyncio.ensure_future(openai_server.create_chat_completion(request))
        await _until(agent.started.is_set)
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        await asyncio.sleep(0.2)
        assert agent.closed == 0
        agent.release.set()
        await _until(lambda: agent.closed == 1)

    asyncio.run(scenario())


def test_agent_is_closed_when_preparing_it_fails(client, mocker):
    agent = _FakeAgent()
    mocker.patch.object(openai_server.registry, "get_agent", return_value=agent)
    mocker.patch.object(
        openai_server, "_prepare_agent", side_effect=RuntimeError("bad request")
    )

    client.post("/v1/chat/completions", json=_body(False))

    assert agent.closed == 1
