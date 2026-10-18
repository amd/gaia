# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Memory must never store the model's own tool-call markup or reasoning (#5300).

When Gemma packs several tool calls into one reply and closes a string wrongly,
the following calls (and its ``<|channel>thought`` reasoning) end up inside the
first call's ``fact`` argument. GAIA stored that verbatim: 42 of 112 entries in
one eval run were corrupted, the swallowed items were never saved on their own,
and ``remember`` still reported success. The store refuses such text, and
``remember`` says exactly what was and was not saved.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import (
    MAX_CONTENT_LENGTH,
    MemoryContentError,
    MemoryStore,
)
from gaia.agents.base.tools import _TOOL_REGISTRY

# Verbatim shape of a corrupted row from the memory_stress_capacity run.
ISSUE_PAYLOAD = (
    'Staging AWS account ID: 987654321098"""}<tool_call|><|tool_call>'
    'call:remember{category:"}<tool_call|><|channel>thought\n'
    "Fact #4 has been processed. Continuing Batch 1 with Fact #5…"
)


def _gemma_call(fact: str) -> str:
    return (
        '<|tool_call>call:remember{category:<|"|>fact<|"|>,'
        f'fact:<|"|>{fact}<|"|>}}<tool_call|>'
    )


def _swallowed(first: str, rest: list[str]) -> str:
    """A first fact whose string swallowed every following remember call."""
    return first + '"""}<tool_call|>' + "".join(_gemma_call(f) for f in rest)


MARKUP_PAYLOADS = {
    "issue_evidence": ISSUE_PAYLOAD,
    "swallowed_calls": _swallowed(
        "Production AWS account ID: 123456789012",
        ["Staging region is us-west-2", "On-call rotation is weekly"],
    ),
    "gemma_reasoning_only": (
        "User prefers aisle seats<|channel>thought\nI have processed the first "
        "five facts. Next I will store the sixth.<channel|>"
    ),
    "harmony_reasoning": (
        "Team standup is at 9:30<|channel|>analysis<|message|>Need to store "
        "the next item.<|end|>"
    ),
    "chatml_turn": "Favorite editor is Vim<|im_end|>\n<|im_start|>assistant",
    "deepseek_tokens": (
        "Build uses Bazel<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>remember"
        '<｜tool▁sep｜>{"fact": "x"}<｜tool▁call▁end｜>'
    ),
    "gemma_string_delimiter_only": 'Laptop is a <|"|>Framework 13<|"|>',
    "unicode_around_markup": "東京オフィスの住所 🏢<|tool_call>call:remember{}",
    "markup_in_unterminated_fence": "Notes:\n```\n<|tool_call>call:remember{}",
}

LEGIT_FACTS = {
    "think_tag_mention": "My local agent wraps its reasoning in <think> tags",
    "think_block_example": "The parser drops <think>...</think> blocks before output",
    "json_in_quotes": (
        'The webhook payload is \'{"tool_calls": [{"name": "remember", '
        '"arguments": {"fact": "x"}}]}\''
    ),
    "plain_xml_tool_call": "Qwen wraps calls in <tool_call></tool_call> XML",
    "sentinel_in_inline_code": "Gemma's call opener token is `<|tool_call>`",
    "sentinel_in_fence": "Template snippet:\n```\n<|channel>thought\n```\nend",
    "pipes_and_angles": "Use a | b > c and x <= y || z in the shell script",
    "markdown_table": "| host | port |\n|------|------|\n| db | 5432 |",
    "unicode": "Café opens at 7 — 東京 branch 🚀 uses UTC+9",
    "call_word": "Remember to call: Linda{at home} after 6pm",
}


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _contents(store: MemoryStore) -> list[str]:
    return [r[0] for r in store._conn.execute("SELECT content FROM knowledge")]


class _Host:
    def __init__(self):
        self._system_prompt_cache = None


class _Agent(MemoryMixin, _Host):
    def __init__(self, db_path):
        super().__init__()
        self._memory_store = MemoryStore(db_path=db_path)
        self._memory_context = "global"
        self._memory_write_failures = {}
        self._embedding_dim = 8
        self._faiss_index = None
        self._faiss_id_map = []

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")


@pytest.fixture
def remember(tmp_path):
    agent = _Agent(tmp_path / "memory.db")
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        agent.register_memory_tools()
        yield agent, _TOOL_REGISTRY["remember"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
        agent._memory_store.close()


class TestStoreBoundary:
    @pytest.mark.parametrize("payload", MARKUP_PAYLOADS.values(), ids=MARKUP_PAYLOADS)
    def test_markup_is_refused_and_nothing_is_stored(self, store, payload):
        with pytest.raises(MemoryContentError):
            store.store(category="fact", content=payload)
        assert _contents(store) == []

    @pytest.mark.parametrize("fact", LEGIT_FACTS.values(), ids=LEGIT_FACTS)
    def test_legit_text_is_stored_verbatim(self, store, fact):
        kid = store.store(category="fact", content=fact)
        assert store.get_item(kid)["content"] == fact

    def test_markup_past_the_length_cap_is_still_refused(self, store):
        payload = "A" * (MAX_CONTENT_LENGTH + 500) + "<|tool_call>call:remember{}"
        with pytest.raises(MemoryContentError):
            store.store(category="fact", content=payload)
        assert _contents(store) == []

    def test_update_refuses_markup_and_keeps_the_original(self, store):
        kid = store.store(category="fact", content="Office is on floor 3")
        with pytest.raises(MemoryContentError):
            store.update(kid, content=ISSUE_PAYLOAD)
        assert store.get_item(kid)["content"] == "Office is on floor 3"

    def test_error_names_the_unrun_calls(self, store):
        payload = _swallowed("first", [f"item {i}" for i in range(24)])
        with pytest.raises(MemoryContentError) as exc:
            store.store(category="fact", content=payload)
        assert exc.value.unrun_tool_calls == 24
        assert "24" in str(exc.value)

    def test_error_is_a_value_error(self):
        assert issubclass(MemoryContentError, ValueError)


class TestRememberReportsWhatWasStored:
    def test_issue_payload_is_rejected_with_nothing_stored(self, remember):
        agent, fn = remember
        result = fn(fact=ISSUE_PAYLOAD, category="fact")
        assert result["status"] == "error"
        assert result["stored"] == 0
        assert "separate" in result["message"]
        assert _contents(agent._memory_store) == []

    def test_swallowed_calls_are_counted_so_the_model_can_reissue(self, remember):
        agent, fn = remember
        rest = [f"Item {i}: value-{i}" for i in range(2, 26)]
        result = fn(fact=_swallowed("Item 1: value-1", rest), category="fact")
        assert result["status"] == "error"
        assert result["stored"] == 0
        assert result["unrun_tool_calls"] == 24
        assert "24" in result["message"]
        assert _contents(agent._memory_store) == []

    def test_a_clean_fact_reports_one_stored(self, remember):
        agent, fn = remember
        fact = LEGIT_FACTS["think_tag_mention"]
        result = fn(fact=fact, category="fact")
        assert result["status"] == "stored"
        assert result["stored"] == 1
        assert agent._memory_store.get_item(result["knowledge_id"])["content"] == fact

    def test_markup_beyond_the_length_cap_is_rejected(self, remember):
        agent, fn = remember
        fact = "B" * (MAX_CONTENT_LENGTH + 1000) + "<|channel>thought\nnext"
        result = fn(fact=fact, category="fact")
        assert result["status"] == "error"
        assert result["stored"] == 0
        assert _contents(agent._memory_store) == []

    def test_partial_failures_store_only_clean_items(self, remember):
        agent, fn = remember
        facts = [
            "Production AWS account ID: 123456789012",
            ISSUE_PAYLOAD,
            "Preferred airline is Alaska",
            MARKUP_PAYLOADS["harmony_reasoning"],
            "東京 office opens at 9",
        ]
        results = [fn(fact=f, category="fact") for f in facts]

        assert [r["status"] for r in results] == [
            "stored",
            "error",
            "stored",
            "error",
            "stored",
        ]
        assert sum(r["stored"] for r in results) == 3
        stored = _contents(agent._memory_store)
        assert sorted(stored) == sorted([facts[0], facts[2], facts[4]])
        for content in stored:
            assert "<|" not in content and "|>" not in content


class TestExtractionPath:
    def test_extracted_markup_is_not_applied(self, tmp_path):
        agent = _Agent(tmp_path / "memory.db")
        try:
            applied = agent._execute_extraction_operations(
                [
                    {"op": "add", "category": "fact", "content": ISSUE_PAYLOAD},
                    {"op": "add", "category": "fact", "content": "Uses Postgres 16"},
                ],
                existing_items=[],
            )
            assert applied == 1
            assert _contents(agent._memory_store) == ["Uses Postgres 16"]
        finally:
            agent._memory_store.close()


class TestUpdateMemoryTool:
    def test_markup_edit_is_refused_and_the_row_is_unchanged(self, remember):
        agent, _ = remember
        update_memory = _TOOL_REGISTRY["update_memory"]["function"]
        kid = agent._memory_store.store(category="fact", content="Desk is 4B")
        result = update_memory(knowledge_id=kid, content=ISSUE_PAYLOAD)
        assert result["status"] == "error"
        assert "Nothing was stored" in result["message"]
        assert agent._memory_store.get_item(kid)["content"] == "Desk is 4B"

    def test_markup_past_the_cap_in_an_edit_is_refused(self, remember):
        agent, _ = remember
        update_memory = _TOOL_REGISTRY["update_memory"]["function"]
        kid = agent._memory_store.store(category="fact", content="Desk is 4B")
        content = "C" * (MAX_CONTENT_LENGTH + 10) + "<tool_call|>"
        result = update_memory(knowledge_id=kid, content=content)
        assert result["status"] == "error"
        assert agent._memory_store.get_item(kid)["content"] == "Desk is 4B"


@pytest.fixture
def dashboard(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import gaia.ui.routers.memory as memory_router

    app = FastAPI()
    app.include_router(memory_router.router)
    previous = memory_router._store
    memory_router._store = store
    try:
        with patch.object(memory_router, "_embed_row", return_value={}):
            with TestClient(app, headers={"X-Gaia-UI": "1"}) as client:
                yield client
    finally:
        memory_router._store = previous


class TestDashboard:
    def test_create_with_markup_is_a_422_not_a_500(self, dashboard, store):
        resp = dashboard.post("/api/memory/knowledge", json={"content": ISSUE_PAYLOAD})
        assert resp.status_code == 422
        assert "markup" in resp.json()["detail"]
        assert _contents(store) == []

    def test_edit_with_markup_is_a_422_and_keeps_the_row(self, dashboard, store):
        kid = store.store(category="fact", content="Desk is 4B")
        resp = dashboard.put(
            f"/api/memory/knowledge/{kid}", json={"content": ISSUE_PAYLOAD}
        )
        assert resp.status_code == 422
        assert store.get_item(kid)["content"] == "Desk is 4B"
