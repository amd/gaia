# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``final`` carries what fills the model's context (contract 2.21)."""

from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.ui import turn_insights  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _Agent:
    def __init__(self, result=None, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.model_id = "Qwen3.6-35B-A3B-GGUF"
        self.result = result if result is not None else {"answer": "Paris."}

    def process_query(self, query, max_steps=None):
        return self.result

    def close(self):
        pass


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    state = {"result": None}

    def build(**kw):
        return _Agent(result=state["result"], **kw)

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL), state
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


@pytest.fixture
def measured(monkeypatch):
    """Record what the panel measurement was asked to count."""
    calls = []

    def fake(agent, turns, model_id):
        calls.append({"turns": turns, "model_id": model_id})
        return {"window": 65536, "used": 1234, "model": model_id}

    monkeypatch.setattr(turn_insights, "panel_context", fake)
    return calls


def _final(client, **fields):
    body = {"query": "Capital of France?", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    r = client.post("/v1/gaia/query", json=body)
    assert r.status_code == 200, r.text
    events = [
        json.loads(line[len("data: ") :])
        for line in r.text.splitlines()
        if line.startswith("data: ")
    ]
    finals = [e for e in events if e.get("type") in ("final", "error")]
    assert len(finals) == 1, events
    return finals[0]


def test_the_contract_advertises_final_context():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 21)


def test_final_carries_the_next_calls_context(built, measured):
    client, _ = built
    context = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]

    final = _final(client, context=context)

    assert final["type"] == "final"
    assert final["context"] == {
        "window": 65536,
        "used": 1234,
        "model": "Qwen3.6-35B-A3B-GGUF",
    }
    [call] = measured
    # The pushed turn, then this turn with its answer: what the next push carries.
    assert call["turns"] == [
        [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}],
        [
            {"role": "user", "content": "Capital of France?"},
            {"role": "assistant", "content": "Paris."},
        ],
    ]


def test_a_stopped_turn_sends_no_measurement(built, measured):
    client, state = built
    state["result"] = {"status": "cancelled"}

    final = _final(client)

    assert final["type"] == "final"
    assert "context" not in final and "context_error" not in final
    assert measured == []


def test_a_failed_measurement_says_why_and_keeps_the_answer(built, monkeypatch):
    client, _ = built

    def broken(agent, turns, model_id):
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(turn_insights, "panel_context", broken)

    final = _final(client)

    assert final["answer"] == "Paris."
    assert "context" not in final
    assert "tokenizer exploded" in final["context_error"]


def test_a_slow_tokenizer_never_holds_up_the_answer(built, monkeypatch):
    """A busy model server: the estimate stands in, labelled, within the budget."""
    import threading
    import time

    client, _ = built
    release = threading.Event()

    def slow_exact(agent, turns, model_id, estimate_reason=None):
        if estimate_reason is None:
            release.wait(10)  # the tokenizer is stuck behind another generation
            return {"used": 1, "tokenizer": {"exact": True}}
        return {"used": 2, "tokenizer": {"exact": False, "reason": estimate_reason}}

    monkeypatch.setattr(turn_insights, "panel_context", slow_exact)
    monkeypatch.setattr(server_mod, "_EXACT_COUNT_SECONDS", 0.2)
    started = time.monotonic()
    try:
        final = _final(client)
    finally:
        release.set()

    assert time.monotonic() - started < 3
    assert final["answer"] == "Paris."
    assert final["context"]["used"] == 2
    assert final["context"]["tokenizer"]["exact"] is False
    assert "estimates" in final["context"]["tokenizer"]["reason"]
