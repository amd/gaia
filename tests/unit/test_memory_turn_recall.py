# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Stored memories relevant to the current request must reach the model.

The stable memory prompt is frozen at session start and picks facts by
confidence alone, with no regard to what is being asked. Everything else was
reachable only through an explicit ``recall`` call, which models rarely make: 4
calls in a 2,121-task benchmark sweep.

Each turn now surfaces the few memories most relevant to the request in the
per-turn dynamic context, which is prepended to the user message so the cached
system prompt is unchanged. Every surfaced memory, there and in the stable
prompt, carries the absolute dates it was learned and last confirmed.
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pytest

from gaia.agents.base.memory import MemoryMixin, _embedding_to_blob
from gaia.agents.base.memory_store import MemoryStore

DIM = 8


def _axis(i: int, lean: float = 0.0, towards: int = 7) -> np.ndarray:
    """Unit vector on axis *i*, optionally leaning towards another axis."""
    vec = np.zeros(DIM, dtype=np.float32)
    vec[i] = 1.0
    vec[towards] += lean
    return vec / np.linalg.norm(vec)


class _Host(MemoryMixin):
    def __init__(self, store: MemoryStore, vectors: Dict[str, np.ndarray]):
        self._memory_store = store
        self._memory_context = "work"
        self._embedding_dim = DIM
        self._vectors = vectors
        self._faiss_index = None
        self._faiss_id_map = []

    def _embed_text(self, text):
        return self._vectors[text]


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _remember(store, vectors, content, vec, **kwargs):
    kwargs.setdefault("context", "work")
    kid = store.store(content=content, **kwargs)
    store.store_embedding(kid, _embedding_to_blob(vec))
    vectors[content] = vec
    return kid


def _backdate(store, kid, created, updated):
    with store._lock:
        store._conn.execute(
            "UPDATE knowledge SET created_at = ?, updated_at = ? WHERE id = ?",
            (created, updated, kid),
        )
        store._conn.commit()


QUERY = "why does the toybox test suite fail on CI"


@pytest.fixture
def host(store):
    vectors: Dict[str, np.ndarray] = {QUERY: _axis(0)}
    h = _Host(store, vectors)
    h.vectors = vectors
    return h


class TestRelevantMemoriesAreSurfaced:
    def test_the_relevant_note_reaches_the_turn_with_its_age(self, host, store):
        kid = _remember(
            store,
            host.vectors,
            "toybox tests need TOYBOX_CLOCK=frozen or they fail",
            _axis(0, lean=0.3),
            category="note",
        )
        _backdate(store, kid, "2026-06-03T10:00:00+00:00", "2026-09-20T08:00:00+00:00")
        _remember(
            store,
            host.vectors,
            "User's cat is called Miso",
            _axis(3),
            category="note",
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        ctx = host.get_memory_dynamic_context()

        assert "Stored memories that may bear on this message:" in ctx
        assert (
            "  - [note] toybox tests need TOYBOX_CLOCK=frozen or they fail "
            "(learned 2026-06-03, last confirmed 2026-09-20)"
        ) in ctx
        assert "Miso" not in ctx

    def test_only_the_top_few_are_surfaced(self, host, store):
        topics = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        for i, topic in enumerate(topics):
            _remember(
                store,
                host.vectors,
                f"{topic} fact",
                _axis(0, lean=0.1 * (i + 1)),
                category="fact",
            )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        ctx = host.get_memory_dynamic_context()

        assert ctx.count(" fact (learned ") == 3
        assert "alpha fact" in ctx and "foxtrot fact" not in ctx

    def test_what_the_stable_prompt_shows_is_not_repeated(self, host, store):
        _remember(
            store,
            host.vectors,
            "toybox CI runs on Python 3.12",
            _axis(0, lean=0.2),
            category="fact",
            confidence=0.9,
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        stable = host.get_memory_system_prompt()
        ctx = host.get_memory_dynamic_context()

        assert "toybox CI runs on Python 3.12" in stable
        assert "toybox CI runs on Python 3.12" not in ctx

    def test_surfacing_does_not_count_as_a_use(self, host, store):
        kid = _remember(
            store,
            host.vectors,
            "toybox CI needs a frozen clock",
            _axis(0),
            category="note",
            confidence=0.4,
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        host.get_memory_dynamic_context()

        item = store.get_item(kid)
        assert item["confidence"] == pytest.approx(0.4)
        assert item["use_count"] == 0


class TestWhatIsNeverSurfaced:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"category": "note", "context": "personal"},
            {"category": "note", "sensitive": True},
            {"category": "reminder", "due_at": "2030-01-01T00:00:00+00:00"},
        ],
        ids=["other-context", "sensitive", "reminder"],
    )
    def test_excluded_rows(self, host, store, kwargs):
        _remember(store, host.vectors, "toybox CI secret-ish row", _axis(0), **kwargs)
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        assert "toybox CI secret-ish row" not in host.get_memory_dynamic_context()

    def test_an_unrelated_nearest_neighbour_is_below_the_floor(self, host, store):
        _remember(
            store,
            host.vectors,
            "User prefers tabs over spaces",
            _axis(0, lean=2.0, towards=5),
            category="note",
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        assert "tabs over spaces" not in host.get_memory_dynamic_context()

    def test_a_credential_is_masked(self, host, store):
        _remember(
            store,
            host.vectors,
            "toybox CI deploy password is hunter2hunter2",
            _axis(0),
            category="note",
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        ctx = host.get_memory_dynamic_context()

        assert "hunter2" not in ctx
        assert "[redacted: this memory looks like a credential." in ctx

    def test_an_embedding_failure_still_returns_the_time(self, host, store):
        _remember(store, host.vectors, "toybox CI note", _axis(0), category="note")
        host._rebuild_faiss_index()
        host._memory_turn_query = "a query the embedder cannot handle"

        ctx = host.get_memory_dynamic_context()

        assert ctx.startswith("[GAIA Memory Context]\nCurrent time: ")
        assert "Stored memories" not in ctx


class TestStablePromptAges:
    def test_each_personal_item_carries_its_dates(self, host, store):
        fact = store.store(
            category="fact", content="Team ships on Fridays", confidence=0.8
        )
        pref = store.store(category="preference", content="Prefers short answers")
        _backdate(store, fact, "2026-06-03T10:00:00+00:00", "2026-09-20T08:00:00+00:00")
        _backdate(store, pref, "2026-07-01T10:00:00+00:00", "2026-07-01T10:00:00+00:00")

        prompt = host.get_memory_system_prompt()

        assert (
            "  - Team ships on Fridays (confidence: 0.80, learned 2026-06-03, "
            "last confirmed 2026-09-20)"
        ) in prompt
        assert "  - Prefers short answers (learned 2026-07-01)" in prompt


class _AgentBase:
    def process_query(self, user_input, **kwargs):
        self.sent = user_input
        return {"result": ""}


class _TurnHost(_Host, _AgentBase):
    pass


def test_the_memory_rides_on_the_user_message_not_the_system_prompt(store):
    vectors: Dict[str, np.ndarray] = {QUERY: _axis(0)}
    host = _TurnHost(store, vectors)
    _remember(
        store, vectors, "toybox CI needs a frozen clock", _axis(0), category="note"
    )
    host._rebuild_faiss_index()
    system_before = host.get_memory_system_prompt()

    host.process_query(QUERY)

    before_question, _, after = host.sent.partition(QUERY)
    assert "  - [note] toybox CI needs a frozen clock" in before_question
    assert after == ""
    assert host.get_memory_system_prompt() == system_before
    assert "frozen clock" not in system_before
