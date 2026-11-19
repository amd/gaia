# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``reasoning`` field (contract 2.22) streams the model's reasoning."""

from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _Agent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_streaming_text("<think>Weigh the ")
        self.console.print_streaming_text("forecast.</think>Bring a coat.")
        self.console.print_streaming_text("", end_of_stream=True)
        return {"answer": "Bring a coat."}

    def close(self):
        pass


@pytest.fixture
def client(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(server_mod, "build_query_agent", lambda **kw: _Agent(**kw))
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _Agent(**kw))
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL)
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _events(client, **fields):
    body = {"query": "Do I need a coat?", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    r = client.post("/v1/gaia/query", json=body)
    assert r.status_code == 200, r.text
    return [
        json.loads(line[len("data: ") :])
        for line in r.text.splitlines()
        if line.startswith("data: ")
    ]


def test_the_contract_advertises_reasoning():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 22)
    assert "reasoning" in server_mod.QueryRequest.model_fields


def test_asking_for_reasoning_streams_it_apart_from_the_answer(client):
    events = _events(client, reasoning=True)

    reasoning = "".join(e["delta"] for e in events if e["type"] == "reasoning")
    assert reasoning == "Weigh the forecast."
    assert not any("Weigh" in e.get("message", "") for e in events)
    assert "Weigh" not in "".join(
        e.get("delta", "") for e in events if e["type"] == "token"
    )


def test_without_the_field_reasoning_folds_into_status_as_before(client):
    events = _events(client)

    assert not any(e["type"] == "reasoning" for e in events)
    assert any("Weigh" in e.get("message", "") for e in events if e["type"] == "status")
