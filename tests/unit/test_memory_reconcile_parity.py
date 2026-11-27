# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Reconcile button gives the same result with or without a chat open.

With a chat open the dashboard runs ``MemoryMixin.reconcile_memory``; without
one, the router's standalone path. Both must change the same memories the same
way for the same verdicts.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.ui.routers.memory as memory_router_mod
from gaia.agents.base.memory import (
    RECONCILIATION_SYSTEM_PROMPT,
    MemoryMixin,
    _embedding_to_blob,
    reconcile_memories,
    reconciliation_max_tokens,
)
from gaia.agents.base.memory_store import MemoryStore
from tests.unit.faiss_support import require_faiss

DIM = 8
MODEL = "Gemma-4-E4B-it-GGUF"
OLD = "2025-01-01T00:00:00"
NEW = "2026-01-01T00:00:00"

# (content, created_at, cluster). Memories in one cluster share a vector, so
# each cluster is one pair; clusters are orthogonal, so they never pair up.
FIXTURE = [
    ("User drinks tea every morning", OLD, 0),
    ("User drinks coffee every morning", NEW, 0),
    ("User runs five miles daily", OLD, 1),
    ("User runs less since the knee injury", NEW, 1),
    ("User lives in Austin", OLD, 2),
    ("User's home city is Austin", NEW, 2),
    ("User owns a red bike", OLD, 3),
    ("User's favourite bike shop closed", NEW, 3),
]

VERDICTS = {
    "drinks": "contradict",
    "runs": "weaken",
    "Austin": "reinforce",  # a duplicate restatement
    "bike": "neutral",  # unrelated
}


def _classify(prompt: str) -> str:
    for keyword, relationship in VERDICTS.items():
        if keyword in prompt:
            return f'{{"relationship": "{relationship}", "action": "noop"}}'
    raise AssertionError(f"unexpected pair: {prompt}")


def _axis(i: int) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float32)
    vec[i] = 1.0
    return vec


def _seed(store: MemoryStore, rows) -> dict:
    ids = {}
    for content, created_at, cluster in rows:
        kid = store.store(category="fact", content=content, context="global")
        store.store_embedding(kid, _embedding_to_blob(_axis(cluster)))
        with store._lock:
            store._conn.execute(
                "UPDATE knowledge SET created_at = ? WHERE id = ?", (created_at, kid)
            )
            store._conn.commit()
        ids[content] = kid
    assert len(set(ids.values())) == len(rows)
    return ids


def _snapshot(store: MemoryStore, ids: dict) -> dict:
    """Each memory's state, with ids swapped for content so stores compare."""
    by_id = {kid: content for content, kid in ids.items()}
    snap = {}
    for content, kid in ids.items():
        item = store.get_item(kid)
        meta = item.get("metadata") or {}
        snap[content] = {
            "confidence": round(item["confidence"], 6),
            "superseded_by": by_id.get(item["superseded_by"]),
            "reconciled_with": sorted(
                by_id[r] for r in meta.get("reconciled_with", [])
            ),
        }
    return snap


class _Host(MemoryMixin):
    pass


def _run_agent(store: MemoryStore, classify=_classify, max_pairs: int = 20):
    """Reconcile through the chat-session path; returns (result, prompts, host)."""
    host = _Host()
    host._memory_store = store
    host._embedding_dim = DIM
    host._faiss_index = MagicMock(ntotal=len(FIXTURE))
    host._faiss_remove = MagicMock()
    prompts = []

    def send_messages(messages, **kwargs):
        prompts.append((messages[0]["content"], kwargs))
        return SimpleNamespace(text=classify(messages[0]["content"]))

    host.chat = MagicMock(effective_model=MODEL)
    host.chat.send_messages.side_effect = send_messages
    return host.reconcile_memory(max_pairs=max_pairs), prompts, host


def _run_router(store: MemoryStore, classify=_classify, max_pairs: int = 20):
    """Reconcile through the no-session dashboard path; returns (result, prompts)."""
    app = FastAPI()
    app.include_router(memory_router_mod.router)
    prompts = []

    def chat(messages, **kwargs):
        user = [m for m in messages if m["role"] == "user"][0]["content"]
        prompts.append((messages, kwargs))
        return classify(user)

    llm = MagicMock()
    llm.chat.side_effect = chat
    memory_router_mod._store = store
    memory_router_mod._reconcile_fn = None
    try:
        with (
            patch("gaia.llm.create_client", return_value=llm),
            patch(
                "gaia.llm.lemonade_client.resolve_local_chat_model",
                return_value=MODEL,
            ),
            TestClient(app, headers={"X-Gaia-UI": "1"}) as client,
        ):
            resp = client.post(f"/api/memory/reconcile?max_pairs={max_pairs}")
    finally:
        memory_router_mod._store = None
    assert resp.status_code == 200, resp.text
    return resp.json(), prompts


@pytest.fixture
def stores(tmp_path):
    require_faiss()
    made = []

    def make(name):
        store = MemoryStore(db_path=tmp_path / f"{name}.db")
        made.append(store)
        return store

    yield make
    for store in made:
        store.close()


def test_both_paths_apply_the_same_verdicts(stores):
    agent_store, router_store = stores("agent"), stores("router")
    agent_ids = _seed(agent_store, FIXTURE)
    router_ids = _seed(router_store, FIXTURE)

    agent_result, agent_prompts, host = _run_agent(agent_store)
    router_result, router_prompts = _run_router(router_store)

    expected = {
        "pairs_checked": 4,
        "reinforced": 1,
        "contradicted": 1,
        "weakened": 1,
        "neutral": 1,
    }
    assert agent_result == router_result == expected
    after = _snapshot(agent_store, agent_ids)
    assert after == _snapshot(router_store, router_ids)

    # The canonical effects: the older contradicted memory is superseded by the
    # newer, which gains 0.1; weaken costs only the older one 0.1.
    assert after["User drinks tea every morning"]["superseded_by"] == (
        "User drinks coffee every morning"
    )
    assert after["User drinks coffee every morning"]["confidence"] == 0.6
    assert after["User runs five miles daily"]["confidence"] == 0.4
    assert after["User runs less since the knee injury"]["confidence"] == 0.5
    assert after["User lives in Austin"]["confidence"] == 0.55
    assert after["User's home city is Austin"]["confidence"] == 0.55
    assert after["User owns a red bike"]["confidence"] == 0.5
    assert after["User's favourite bike shop closed"]["confidence"] == 0.5
    for state in after.values():
        assert len(state["reconciled_with"]) == 1
    host._faiss_remove.assert_called_once_with(
        agent_ids["User drinks tea every morning"]
    )

    # The same request reaches the model on both paths.
    assert sorted(p for p, _ in agent_prompts) == sorted(
        m[-1]["content"] for m, _ in router_prompts
    )
    for _prompt, kwargs in agent_prompts:
        assert kwargs["system_prompt"] == RECONCILIATION_SYSTEM_PROMPT
        assert kwargs["temperature"] == 0.1
        assert kwargs["max_tokens"] == reconciliation_max_tokens(MODEL)
    for messages, kwargs in router_prompts:
        assert messages[0] == {
            "role": "system",
            "content": RECONCILIATION_SYSTEM_PROMPT,
        }
        assert kwargs["temperature"] == 0.1
        assert kwargs["max_tokens"] == reconciliation_max_tokens(MODEL)
        assert kwargs["model"] == MODEL


@pytest.mark.parametrize("path", ["agent", "router"])
def test_a_memory_in_two_pairs_keeps_both_markers(stores, path):
    """A memory weakened by two others is weakened once per pair, ever."""
    store = stores(path)
    rows = [
        ("Anchor: user works from the office", "2024-01-01T00:00:00", 0),
        ("User works from home on Fridays", OLD, 0),
        ("User works remotely in winter", NEW, 0),
    ]
    ids = _seed(store, rows)
    anchor = "Anchor: user works from the office"

    def classify(prompt):
        relationship = "weaken" if "Anchor" in prompt else "neutral"
        return f'{{"relationship": "{relationship}"}}'

    run = _run_agent if path == "agent" else _run_router
    first = run(store, classify)[0]
    after_first = _snapshot(store, ids)
    second = run(store, classify)[0]

    assert first["weakened"] == 2
    assert first["neutral"] == 1
    assert after_first[anchor]["reconciled_with"] == sorted([rows[1][0], rows[2][0]])
    assert after_first[anchor]["confidence"] == 0.3
    assert second["pairs_checked"] == 0
    assert _snapshot(store, ids) == after_first


def test_a_memory_superseded_mid_run_is_not_classified_again(stores):
    store = stores("supersede")
    rows = [
        ("User drinks tea", OLD, 0),
        ("User drinks coffee", NEW, 0),
        ("User drinks only water", NEW, 0),
    ]
    ids = _seed(store, rows)
    seen = []

    def classify(prompt):
        seen.append(prompt)
        if "tea" in prompt:
            return '{"relationship": "contradict"}'
        return '{"relationship": "neutral"}'

    result = reconcile_memories(store, classify, embedding_dim=DIM)

    # tea is retired by whichever newer memory it meets first; its other pair
    # is dropped rather than retiring it a second time.
    assert result["contradicted"] == 1
    assert sum("tea" in p for p in seen) == 1
    assert store.get_item(ids["User drinks tea"])["superseded_by"] in (
        ids["User drinks coffee"],
        ids["User drinks only water"],
    )


def test_settled_pairs_do_not_use_up_the_cap(stores):
    """With max_pairs=1, each run classifies a pair no earlier run did."""
    store = stores("cap")
    _seed(
        store,
        [
            ("User buys jazz records", OLD, 0),
            ("User plays jazz piano at night", NEW, 0),
            ("User listens to blues", OLD, 1),
            ("User collects old guitars", NEW, 1),
        ],
    )
    neutral = lambda _prompt: '{"relationship": "neutral"}'  # noqa: E731

    runs = [
        reconcile_memories(store, neutral, max_pairs=1, embedding_dim=DIM)
        for _ in range(3)
    ]

    assert [r["pairs_checked"] for r in runs] == [1, 1, 0]


def test_a_malformed_marker_fails_the_run(stores):
    """A marker that is not a list is not reset: that would re-penalise pairs."""
    store = stores("meta")
    ids = _seed(
        store,
        [
            ("User buys jazz records", OLD, 0),
            ("User plays jazz piano at night", NEW, 0),
        ],
    )
    store.update(
        ids["User buys jazz records"], metadata={"reconciled_with": "not-a-list"}
    )
    classify = MagicMock()

    with pytest.raises(ValueError, match="not a list"):
        reconcile_memories(store, classify, embedding_dim=DIM)
    classify.assert_not_called()
