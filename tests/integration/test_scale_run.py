# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA at the size a real user reaches, with a scripted model.

Every unit test indexes three files and stores five memories. rc18 fell over
at 300 files and 300 memories: a folder index reloaded the embedder for every
file (15 minutes for 300 small ones), and a memory search over a few hundred
rows ended the turn on a JSON error.

Nothing here loads a model: the installer journeys' stand-in Lemonade answers
the embedding and model calls and writes down each one, so the assertions are
counts of what GAIA asked for, plus generous time bounds. Run on release tags
by .github/workflows/test_scale.yml.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.ui.server import create_app
from tests.fixtures.fake_lemonade import FakeLemonade

EMBEDDER = "user.embeddinggemma-300m-GGUF"
#: 300 is the size rc18 failed at. GAIA_SCALE_FILES changes it for a local run.
FILES = int(os.environ.get("GAIA_SCALE_FILES") or 300)
MEMORIES = 500
#: Seconds the index may take against a server that answers at once.
INDEX_BUDGET_S = 0.8 * FILES


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    """One large folder index, in its own home, against a recording server."""
    root = tmp_path_factory.mktemp("scale")
    home = root / "home"
    home.mkdir()
    folder = home / "stations"
    folder.mkdir()
    for i in range(FILES):
        (folder / f"station_{i:03d}.txt").write_text(
            f"Station {i} logged {i * 7} litres on day {i % 28 + 1}.\n",
            encoding="utf-8",
        )
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(home))
        patch.setenv("USERPROFILE", str(home))
        patch.setattr(Path, "home", staticmethod(lambda: home))
        patch.setenv("GAIA_HOME", str(root / "gaia-home"))
        patch.delenv("GAIA_DOCUMENT_ROOTS", raising=False)
        with FakeLemonade(downloaded=(EMBEDDER, "Gemma-4-E4B-it-GGUF")) as server:
            patch.setenv("LEMONADE_BASE_URL", server.base_url)
            client = TestClient(create_app(db_path=":memory:"))
            started = time.monotonic()
            response = client.post(
                "/api/documents/index-folder", json={"folder_path": str(folder)}
            )
            elapsed = time.monotonic() - started
            assert response.status_code == 200, response.text
            yield {
                "result": response.json(),
                "rows": client.get("/api/documents").json()["documents"],
                "elapsed": elapsed,
                "loads": [m for m in server.loads if "embed" in m.lower()],
                "unloads": [r for r in server.requests if r.path.endswith("/unload")],
            }


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:"))


def test_a_large_folder_is_indexed_whole_with_one_row_each(indexed):
    result = indexed["result"]

    assert result["failed"] == 0, result["errors"][:3]
    assert result["indexed"] == FILES
    paths = Counter(row["filepath"] for row in indexed["rows"])
    assert len(paths) == FILES
    assert not [path for path, count in paths.items() if count > 1]


def test_a_large_folder_loads_the_embedder_once_and_is_quick(indexed):
    loads, unloads = indexed["loads"], indexed["unloads"]

    assert len(loads) <= 2 and len(unloads) <= 2, (
        f"{FILES} files loaded the embedder {len(loads)} times and unloaded a "
        f"model {len(unloads)} times"
    )
    assert (
        indexed["elapsed"] < INDEX_BUDGET_S
    ), f"{FILES} small files took {indexed['elapsed']:.0f} s"


_DIM = 8


class _Recall(MemoryMixin):
    """The memory search, over a real store, with fixed-size test vectors."""

    def __init__(self, store: MemoryStore, ids: list):
        self._memory_store = store
        self._memory_context = "global"
        self._ids = ids

    def _finish_deferred_memory_init(self):
        pass

    def _embed_text(self, text):
        return np.ones(_DIM, dtype=np.float32)

    def _faiss_search(self, query_vec, top_k):
        return [(kid, 0.9) for kid in self._ids[:top_k]]


@pytest.fixture
def memories(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    ids = []
    for i in range(MEMORIES):
        kid = store.store(
            category="fact",
            content=f"User's cousin number {i} is nicknamed quartz-{i:04d}",
            context="global",
        )
        store.store_embedding(kid, np.full(_DIM, i, dtype=np.float32).tobytes())
        ids.append(kid)
    yield store, ids
    store.close()


def test_a_search_over_500_memories_returns_something_json_can_carry(memories):
    store, ids = memories

    results = _Recall(store, ids)._hybrid_search(
        "cousin number 7", category="fact", top_k=100
    )

    assert len(results) >= 50
    json.dumps(results)


def test_the_memory_api_reaches_every_one_of_500_memories(
    memories, client, monkeypatch
):
    store, ids = memories
    monkeypatch.setattr("gaia.ui.routers.memory._get_store", lambda: store)

    seen: set = set()
    offset = 0
    total = None
    while total is None or offset < total:
        page = client.get(
            "/api/memory/knowledge", params={"limit": 200, "offset": offset}
        ).json()
        total = page["total"]
        assert page["items"], f"an empty page at offset {offset} of {total}"
        seen.update(item["id"] for item in page["items"])
        offset += len(page["items"])

    assert total == MEMORIES
    assert seen == set(ids)
