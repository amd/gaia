# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every embedded memory must be findable by meaning, not just the first 100.

The vector index was rebuilt from a query capped at 100 rows ordered by
confidence, and search then resolved vector hits against a second pool capped
at 200. A memory outside either cap was reachable only by keyword.
"""

from __future__ import annotations

import hashlib
from typing import Dict
from unittest.mock import patch

import numpy as np
import pytest

from gaia.agents.base.memory import EMBEDDING_DIM, MemoryMixin, _embedding_to_blob
from gaia.agents.base.memory_store import MemoryStore

DIM = 512


def _axis(i: int) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float32)
    vec[i] = 1.0
    return vec


def _content(i: int) -> str:
    token = hashlib.sha1(str(i).encode()).hexdigest()[:12]
    return f"fact{i} {token} {token[::-1]}"


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


def _fill(store: MemoryStore, count: int) -> tuple[Dict[str, np.ndarray], str]:
    """Store *count* embedded memories; the first one ranks last by confidence."""
    vectors: Dict[str, np.ndarray] = {}
    first_id = ""
    for i in range(count):
        content = _content(i)
        kid = store.store(
            category="fact",
            content=content,
            context="work",
            confidence=0.2 if i == 0 else 0.6,
        )
        store.store_embedding(kid, _embedding_to_blob(_axis(i)))
        vectors[content] = _axis(i)
        if i == 0:
            first_id = kid
    return vectors, first_id


@pytest.mark.parametrize("count", [150, 250])
def test_rebuild_indexes_every_embedded_memory(store, count):
    vectors, first_id = _fill(store, count)
    host = _Host(store, vectors)

    host._rebuild_faiss_index()

    assert host._faiss_index.ntotal == count
    assert first_id in host._faiss_id_map


@pytest.mark.parametrize("count", [150, 250])
def test_semantic_recall_reaches_a_memory_past_the_first_hundred(store, count):
    vectors, first_id = _fill(store, count)
    query = "which reading was taken on the oldest day"
    vectors[query] = _axis(0)
    host = _Host(store, vectors)
    host._rebuild_faiss_index()

    with patch("gaia.agents.base.memory._get_cross_encoder", return_value=None):
        results = host._hybrid_search(query, top_k=3)

    assert results and results[0]["id"] == first_id


def test_index_stream_honours_filters_and_skips_superseded(store):
    vectors, first_id = _fill(store, 5)
    secret = store.store(
        category="fact", content="vault code orchid", context="work", sensitive=True
    )
    store.store_embedding(secret, _embedding_to_blob(_axis(10)))
    store.update(first_id, superseded_by=secret)

    all_ids = [
        item["id"]
        for item in store.iter_items_with_embeddings(
            include_sensitive=True, batch_size=2
        )
    ]
    public = [
        item["id"]
        for item in store.iter_items_with_embeddings(
            include_sensitive=False, batch_size=2
        )
    ]

    assert len(all_ids) == len(set(all_ids)) == 5
    assert first_id not in all_ids
    assert secret in all_ids
    assert secret not in public
    assert len(public) == len(all_ids) - 1


def test_iter_rejects_a_non_positive_batch(store):
    with pytest.raises(ValueError, match="batch_size"):
        list(store.iter_items_with_embeddings(include_sensitive=True, batch_size=0))


def test_procedure_rebuild_indexes_every_procedure(store):
    for i in range(150):
        vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        vec[i] = 1.0
        store.put_skill(
            name=f"procedure-{i}",
            when_to_use=f"when case {i} comes up",
            markdown_body=f"step for case {i}",
            embedding=_embedding_to_blob(vec),
        )
    host = _Host(store, {})

    host._rebuild_proc_faiss_index()

    assert host._proc_faiss_index.ntotal == 150


def test_resolving_hits_by_id_applies_filters(store):
    vectors, first_id = _fill(store, 3)
    other = store.store(category="note", content="kettle descaled", context="home")
    store.store_embedding(other, _embedding_to_blob(_axis(20)))

    items = store.get_items_with_embeddings(
        context="work", include_sensitive=True, ids=[first_id, other]
    )

    assert [item["id"] for item in items] == [first_id]
