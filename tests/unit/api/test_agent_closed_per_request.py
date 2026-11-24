# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Each request builds its own agent, so each request must close it."""

import types

import pytest
from fastapi.testclient import TestClient

from gaia.api import openai_server
from gaia.api.openai_server import app
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
