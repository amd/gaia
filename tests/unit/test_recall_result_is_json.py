# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A memory search hands the model text, never the rows' embedding bytes.

In v0.26.0-rc18 ``recall(category="fact", limit=100, query=...)`` over 323
memories returned each vector hit with its stored embedding BLOB. The result
was large enough to condense, and the condenser's size check ended the turn
with a bare "Object of type bytes is not JSON serializable".
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from tests.unit.agents.test_large_tool_result_truncation import make_agent

_DIM = 8


class _Host(MemoryMixin):
    def __init__(self, store: MemoryStore, hits):
        self._memory_store = store
        self._memory_context = "global"
        self._hits = hits

    def _finish_deferred_memory_init(self):
        pass

    def _embed_text(self, text):
        return np.ones(_DIM, dtype=np.float32)

    def _faiss_search(self, query_vec, top_k):
        return [(kid, 0.9) for kid in self._hits[:top_k]]


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _seed(store: MemoryStore, count: int):
    ids = []
    for i in range(count):
        kid = store.store(
            category="fact",
            content=f"User's cousin number {i} is nicknamed quartz-{i:04d}",
            context="global",
        )
        store.store_embedding(kid, np.full(_DIM, i, dtype=np.float32).tobytes())
        ids.append(kid)
    return ids


def test_hybrid_search_returns_no_embedding_bytes(store):
    host = _Host(store, _seed(store, 30))

    results = host._hybrid_search("cousin number 7", category="fact", top_k=100)

    assert results, "the vector hits should come back"
    assert all("embedding" not in item for item in results)
    json.dumps(results)  # the model and the condenser both encode it


def test_a_large_recall_result_condenses_without_bytes(store):
    host = _Host(store, _seed(store, 100))
    rows = host._hybrid_search("cousin number 7", category="fact", top_k=100)
    agent = make_agent(device=None, model_id="fireworks.kimi-k2p7-code")
    agent._truncation_budget = lambda: (2000, 1500)
    result = {"status": "found", "count": len(rows), "results": rows}

    shown = agent._handle_large_tool_result("recall", result, [])

    json.dumps(shown)


def test_a_tool_result_carrying_bytes_names_the_tool_and_field():
    agent = make_agent(device=None, model_id="fireworks.kimi-k2p7-code")
    agent._truncation_budget = lambda: (2000, 1500)
    rows = [
        {"id": str(i), "content": "x" * 80, "embedding": b"\x00" * 3072}
        for i in range(100)
    ]

    with pytest.raises(
        TypeError, match=r"recall returned bytes at results\[0\]\.embedding"
    ):
        agent._handle_large_tool_result("recall", {"results": rows}, [])


def test_bytes_nested_deep_in_a_text_result_are_named_too():
    agent = make_agent(device=None, model_id="fireworks.kimi-k2p7-code")
    agent._truncation_budget = lambda: (2000, 1500)
    result = {"content": "line\n" * 2000, "meta": {"raw": [1, {"blob": b"\x01"}]}}

    with pytest.raises(TypeError, match=r"meta\.raw\[1\]\.blob"):
        agent._handle_large_tool_result("read_file", result, [])
