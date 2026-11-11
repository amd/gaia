# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/v1/gaia/query`` fits pushed history into the agent's own budget.

The Agent UI sizes the earlier turns it pushes without seeing this agent's
system prompt or tools. On a small window (32K: NPU models, a short-memory
GPU) that history plus the prompt can overflow, so the sidecar re-fits it.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.agents.base import history  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"
_MODEL = "Qwen3.6-35B-A3B-GGUF"


class _PromptHeavyAgent:
    """Carries a large prompt and records the history it is asked with."""

    def __init__(self, **_kwargs):
        self.model_id = _MODEL
        self.system_prompt = "word " * 10_000
        self._openai_tools = []
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.streaming = False
        self.seen_history = None

    def process_query(self, query, max_steps=None):
        self.seen_history = list(self.conversation_history)
        return {"answer": "ok"}

    def close(self):
        pass


@pytest.fixture
def client(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.resolve_ctx_size", lambda **_kw: 32768
    )
    agents: list = []

    def build(**kw):
        agent = _PromptHeavyAgent(**kw)
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL), agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _context(turns: int, words: int) -> list:
    messages = []
    for n in range(turns):
        messages.append({"role": "user", "content": f"q{n} " + "data " * words})
        messages.append({"role": "assistant", "content": f"a{n}"})
    return messages


def _warnings(response) -> list:
    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return [json.dumps(e) for e in events if "out of the model's view" in str(e)]


def test_history_too_big_beside_the_prompt_is_refit_and_announced(client):
    http, agents = client
    query = "next?"
    own = history.history_budget(_PromptHeavyAgent(), query)
    # Sized from the budget: the token estimator differs between machines.
    words = 1_000 * own // (4 * history.turn_cost(_context(1, 1_000)))
    context = _context(own // history.turn_cost(_context(1, words)) + 1, words)

    response = http.post(
        "/v1/gaia/query",
        json={"query": query, "run_id": str(uuid.uuid4()), "context": context},
    )

    assert response.status_code == 200
    seen = agents[-1].seen_history
    assert 0 < len(seen) < len(context)
    assert seen == context[-len(seen) :]
    assert seen[0]["role"] == "user"
    assert len(_warnings(response)) == 1


def test_history_that_fits_reaches_the_agent_unchanged(client):
    http, agents = client
    context = _context(4, 20)

    response = http.post(
        "/v1/gaia/query",
        json={"query": "q", "run_id": str(uuid.uuid4()), "context": context},
    )

    assert response.status_code == 200
    assert agents[-1].seen_history == context
    assert _warnings(response) == []


def test_a_pushed_turn_bigger_than_the_budget_keeps_its_answer(client):
    http, agents = client
    own = history.history_budget(_PromptHeavyAgent(), "shorten that")
    context = _context(2, 5)
    context.append({"role": "user", "content": "summarize: " + "log " * (own * 2)})
    context.append({"role": "assistant", "content": "revenue grew 12%"})

    response = http.post(
        "/v1/gaia/query",
        json={"query": "shorten that", "run_id": str(uuid.uuid4()), "context": context},
    )

    assert response.status_code == 200
    seen = agents[-1].seen_history
    assert seen[:4] == context[:4]
    assert seen[4]["content"].startswith("summarize: ")
    assert "to fit the model's context window" in seen[4]["content"]
    assert seen[5] == {"role": "assistant", "content": "revenue grew 12%"}
