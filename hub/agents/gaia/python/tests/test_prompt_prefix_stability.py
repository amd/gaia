# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every request the flagship sends must extend the one before it.

llama.cpp reuses its cache only for the prefix two requests share. Gemma 4's
sliding-window attention and Qwen3.6's hybrid layers can resume only from a
checkpoint near the end of an earlier prompt, so a byte that changes anywhere
in the system prompt or tool list makes the server re-read the whole
conversation: 34K tokens and 155 s for one step of a v0.25 RC session.

This drives the real flagship through the stdio transport's history path with
the model call captured, over the things that used to rewrite the prompt
mid-session: indexing a document, a learned procedure being recalled, and
background memory extraction storing a fact between turns.
"""

from __future__ import annotations

import contextlib
import hashlib
import json

import numpy as np
import pytest
from gaia_agent import stdio
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
from gaia_agent_chat.agent import ChatAgent

from gaia.agents.base.history import SessionHistory
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.skill_synthesis import DistilledProcedure, load_synthesis_config
from gaia.agents.base.tools import _TOOL_REGISTRY

_DIM = 768


def _fake_embedding(text: str) -> np.ndarray:
    """Deterministic bag-of-words vector, so selection never needs a server."""
    vec = np.zeros(_DIM, dtype=np.float32)
    for word in str(text).lower().split():
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % _DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


@contextlib.contextmanager
def _isolated_registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _render(request) -> str:
    """The request as the chat template lays it out: system, tools, then turns."""
    messages, tools = request
    dump = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False)
    return dump(messages[:1]) + dump(tools) + "".join(dump(m) for m in messages[1:])


def _first_difference(before: str, after: str) -> int:
    for index, (a, b) in enumerate(zip(before, after)):
        if a != b:
            return index
    return min(len(before), len(after))


class _ScriptedModel:
    """Stands in for the provider's ``chat``: records requests, replays a script.

    Only requests that carry tools are the agent's own turn steps; memory
    extraction runs on a side thread without tools and gets an empty reply.
    """

    def __init__(self):
        self.requests = []
        self.script = []

    def chat(self, messages, model=None, stream=False, **kwargs):
        tools = kwargs.get("tools")
        if not tools:
            reply = "[]"
        else:
            self.requests.append(
                (json.loads(json.dumps(messages)), json.loads(json.dumps(tools)))
            )
            step = self.script.pop(0)
            if isinstance(step, dict):
                call = {
                    "id": step["id"],
                    "type": "function",
                    "function": {
                        "name": step["name"],
                        "arguments": json.dumps(step["args"]),
                    },
                }
                reply = json.dumps(
                    {
                        "__tool_calls__": [call],
                        "finish_reason": "tool_calls",
                        "content": None,
                    }
                )
            else:
                reply = step
        return iter([reply]) if stream else reply


@pytest.fixture
def flagship(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    # Unroutable: nothing here may reach a developer's running server.
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    monkeypatch.setattr(MemoryMixin, "_embed_text", lambda self, t: _fake_embedding(t))
    monkeypatch.setattr(
        ChatAgent,
        "_embed_texts_batch",
        lambda self, texts: np.stack([_fake_embedding(t) for t in texts]),
    )
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    doc = work / "handbook.md"
    doc.write_text("# Safety handbook\n\nWear goggles in the lab.\n", encoding="utf-8")

    with _isolated_registry():
        # Per-turn tool selection changes the tool list by design, so it is
        # off here; this pins everything else in the request.
        agent = GaiaAgent(
            config=GaiaAgentConfig(
                silent_mode=True, streaming=True, dynamic_tools=False
            )
        )
        assert agent._memory_store is not None, "memory must be on for this test"
        agent.conversation_history = SessionHistory(str(tmp_path / "history.sqlite3"))

        # A stand-in RAG — this test pins request-prefix stability, not real
        # indexing, and the CI lane that runs it installs no RAG extras.
        class _FakeRag:
            def __init__(self):
                self.indexed_files = set()

            def index_document(self, path):
                self.indexed_files.add(path)
                return {"success": True, "file_name": "handbook.md", "num_chunks": 1}

        agent.rag = _FakeRag()

        procedure = DistilledProcedure(
            name="read-the-handbook",
            when_to_use="questions about the safety handbook",
            body="1. index_document the handbook. 2. query_specific_file it.",
            tools_required=["index_document"],
        )
        monkeypatch.setattr(
            agent,
            "_recall_skills_for_turn",
            lambda goal: (
                [procedure] if "handbook" in goal.lower() else [],
                load_synthesis_config({}),
            ),
        )

        model = _ScriptedModel()
        monkeypatch.setattr(agent.chat.llm_client, "chat", model.chat)
        for name, value in (
            ("get_last_usage", None),
            ("get_last_ttft_seconds", None),
            ("get_last_finish_reason", "stop"),
            ("get_last_reasoning", None),
        ):
            monkeypatch.setattr(
                agent.chat.llm_client, name, lambda *a, _v=value, **k: _v
            )
        monkeypatch.setattr(agent.chat, "get_stats", lambda: {})
        try:
            yield agent, model, doc
        finally:
            agent.close()


def _turn(agent, model, query, script):
    model.script[:] = list(script)
    agent.conversation_history.prepare(agent, query)
    result = agent.process_query(query)
    stdio._record_turn(agent, query, result["result"], result)
    assert not model.script, f"turn ended before using its script: {model.script}"


def test_each_request_extends_the_previous_one(flagship):
    agent, model, doc = flagship

    _turn(agent, model, "Hi, I'm Sam and I work on the safety team.", ["Hi Sam."])
    # What background extraction does after a turn.
    agent._memory_store.store(
        category="fact", content="Sam works on the safety team", source="extraction"
    )
    _turn(
        agent,
        model,
        f"Index {doc} and tell me what the handbook says.",
        [
            {"id": "c1", "name": "index_document", "args": {"file_path": str(doc)}},
            "Wear goggles in the lab.",
            # A document answer that skipped retrieval is re-asked with the
            # framework's own lookup appended as a new message.
            "Wear goggles in the lab.",
        ],
    )
    _turn(
        agent,
        model,
        "Read handbook.md again.",
        [
            {"id": "c2", "name": "read_file", "args": {"file_path": str(doc)}},
            "It says to wear goggles.",
        ],
    )
    _turn(agent, model, "Thanks!", ["You're welcome."])

    rendered = [_render(request) for request in model.requests]
    assert len(rendered) == 7
    for step, (before, after) in enumerate(zip(rendered, rendered[1:]), start=1):
        at = _first_difference(before, after)
        assert after.startswith(before), (
            f"request {step + 1} stops extending request {step} at char {at} "
            f"of {len(before)}:\n  was: {before[max(0, at - 60): at + 120]!r}\n"
            f"  now: {after[max(0, at - 60): at + 120]!r}"
        )

    # Nothing the prompt used to carry was lost: it rides in the turn instead.
    turn_two = model.requests[1][0][-1]["content"]
    assert "RECALLED PROCEDURES" in turn_two and "read-the-handbook" in turn_two
    turn_three = [m for m in model.requests[4][0] if m["role"] == "user"][-1]
    assert "[Indexed documents: handbook.md]" in turn_three["content"]
    assert "Sam works on the safety team" not in model.requests[-1][0][0]["content"]
