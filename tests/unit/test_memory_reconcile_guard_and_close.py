# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Reconciliation refuses an unsafe FAISS call loudly; a failed start closes its store."""

import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base import memory as memory_mod
from gaia.agents.base.memory import MemoryMixin, _embedding_to_blob
from gaia.agents.base.memory_store import MemoryStore

DIM = 16


class _Host(MemoryMixin):
    pass


def _axis(i):
    vec = np.zeros(DIM, dtype=np.float32)
    vec[i] = 1.0
    return vec


@pytest.fixture
def reconciling_host(tmp_path):
    faiss = pytest.importorskip("faiss")
    store = MemoryStore(db_path=tmp_path / "memory.db")
    for i in range(3):
        kid = store.store(category="fact", content=f"fact {i}", context="work")
        store.store_embedding(kid, _embedding_to_blob(_axis(i)))
    host = _Host()
    host._memory_store = store
    host._embedding_dim = DIM
    host._faiss_index = faiss.IndexFlatIP(DIM)
    host._faiss_index.add(np.stack([_axis(i) for i in range(3)]))
    yield host
    store.close()


def test_reconcile_raises_when_a_second_openmp_runtime_is_loaded(
    reconciling_host, monkeypatch
):
    monkeypatch.delenv(memory_mod._OMP_OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(
        memory_mod,
        "_loaded_omp_runtimes",
        lambda: ("/x/faiss/.dylibs/libomp.dylib", "/x/torch/lib/libomp.dylib"),
    )

    with pytest.raises(RuntimeError, match="OpenMP"):
        reconciling_host.reconcile_memory()


def test_reconcile_search_failure_is_logged_as_a_warning(reconciling_host, caplog):
    with patch("faiss.IndexFlatIP.search", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.WARNING, logger=memory_mod.logger.name):
            result = reconciling_host.reconcile_memory()

    assert result["pairs_checked"] == 0
    assert any(
        r.levelno == logging.WARNING and "boom" in r.getMessage()
        for r in caplog.records
    )


def test_a_failed_memory_start_closes_the_store(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
    closed = []
    real_close = MemoryStore.close

    def recording_close(self):
        closed.append(self)
        real_close(self)

    embedder = MagicMock()
    embedder.embed.side_effect = ConnectionError("Connection refused")
    host = _Host()
    with (
        patch.object(MemoryStore, "close", recording_close),
        patch.object(MemoryMixin, "_get_embedder", return_value=embedder),
        patch.object(MemoryMixin, "_get_embedding_cache") as cache,
        patch("gaia.agents.base.memory._system_context_is_enabled", return_value=False),
    ):
        cache.return_value.get.return_value = None
        host.init_memory(db_path=tmp_path / "memory.db")

    assert host._memory_store is None
    assert len(closed) == 1
