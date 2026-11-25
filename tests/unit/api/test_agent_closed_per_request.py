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


def test_agent_is_closed_when_setup_fails(client, mocker):
    agent = _FakeAgent()
    mocker.patch.object(openai_server.registry, "get_agent", return_value=agent)
    mocker.patch.object(
        openai_server, "_prepare_agent", side_effect=RuntimeError("bad request")
    )

    client.post("/v1/chat/completions", json=_body(False))

    assert agent.closed == 1


def test_agent_is_not_closed_while_a_hung_up_stream_is_still_running():
    release = threading.Event()
    started = threading.Event()
    agent = _FakeAgent()

    def blocking_query(_query):
        started.set()
        release.wait(5)
        return {"result": "hi", "status": "success"}

    agent.process_query = blocking_query

    async def hang_up_after_first_chunk():
        stream = openai_server.create_sse_stream(agent, "hello", "gaia")
        await stream.__anext__()
        pending = asyncio.create_task(stream.__anext__())
        assert await asyncio.to_thread(started.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    # asyncio.run would wait for the busy worker thread on exit, so drive the
    # loop by hand and keep it alive for the worker's done-callback.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(hang_up_after_first_chunk())
        assert agent.closed == 0

        release.set()
        deadline = time.time() + 5
        while agent.closed == 0 and time.time() < deadline:
            loop.run_until_complete(asyncio.sleep(0.05))
        assert agent.closed == 1
    finally:
        loop.close()


def test_cancelled_request_is_not_closed_until_its_turn_ends(mocker):
    release = threading.Event()
    started = threading.Event()
    agent = _FakeAgent()

    def blocking_query(_query):
        started.set()
        release.wait(5)
        return {"result": "hi", "status": "success"}

    agent.process_query = blocking_query
    mocker.patch.object(openai_server.registry, "get_agent", return_value=agent)

    async def cancel_mid_turn():
        request = ChatCompletionRequest(**_body(False))
        call = asyncio.create_task(openai_server.create_chat_completion(request))
        assert await asyncio.to_thread(started.wait, 5)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(cancel_mid_turn())
        assert agent.closed == 0

        release.set()
        deadline = time.time() + 5
        while agent.closed == 0 and time.time() < deadline:
            loop.run_until_complete(asyncio.sleep(0.05))
        assert agent.closed == 1
    finally:
        loop.close()
