# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A session that starts with memory off must not load the embedding model.

``init_memory(incognito=True)`` is what the Agent UI passes for a private chat
or with memory switched off in Settings. The embedder (a ~300 MB llama-server)
then loads only once memory is actually used. Every test mocks the embedder;
nothing here talks to Lemonade.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.memory import (
    MEMORY_UNAVAILABLE_DISABLED_BY_ENV,
    MEMORY_UNAVAILABLE_SERVICE_UNREACHABLE,
    MemoryMixin,
)
from gaia.agents.base.tools import _TOOL_REGISTRY


class _FakeAgent:
    def process_query(self, user_input, **kwargs):
        return {"result": f"Response to: {user_input}"}

    def warm_up(self, progress=None):
        return None


class _Host(MemoryMixin, _FakeAgent):
    pass


@contextmanager
def _mocked_embedder(embed_side_effect=None):
    """Patch the embedder and the steps that need FAISS or an LLM.

    Yields the ``_get_embedder`` mock: its call count is how the tests tell
    whether the embedding model was touched.
    """
    embedder = MagicMock()
    if embed_side_effect is not None:
        embedder.embed.side_effect = embed_side_effect
    else:
        embedder.embed.return_value = [np.ones(768, dtype=np.float32).tolist()]
    get_embedder = MagicMock(return_value=embedder)
    with (
        patch.object(MemoryMixin, "_get_embedder", get_embedder),
        patch.object(MemoryMixin, "_get_embedding_cache") as cache,
        patch.object(MemoryMixin, "_backfill_embeddings", return_value=0),
        patch.object(MemoryMixin, "_rebuild_faiss_index", return_value=None),
        patch.object(MemoryMixin, "_rebuild_proc_faiss_index", return_value=None),
        patch.object(MemoryMixin, "_run_memory_post_init", return_value=None),
        patch(
            "gaia.agents.base.memory._system_context_is_enabled",
            return_value=False,
        ),
    ):
        cache.return_value.get.return_value = None
        yield get_embedder


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "memory.db"


def test_memory_off_at_start_never_touches_the_embedder(db_path):
    with _mocked_embedder() as get_embedder:
        host = _Host()
        host.init_memory(db_path=db_path, incognito=True)
        host.process_query("hello")
        host.process_query("and again")

    assert get_embedder.call_count == 0
    assert host.memory_store is not None
    assert host._incognito is True


def test_memory_on_at_start_still_validates_the_embedder(db_path):
    with _mocked_embedder() as get_embedder:
        host = _Host()
        host.init_memory(db_path=db_path)

    assert get_embedder.call_count >= 1
    assert host._memory_post_init_pending is True


def test_turning_memory_back_on_loads_the_embedder_on_the_next_turn(db_path):
    with _mocked_embedder() as get_embedder:
        host = _Host()
        host.init_memory(db_path=db_path, incognito=True)
        host.process_query("memory is off")
        assert get_embedder.call_count == 0

        host._incognito = False  # what the UI does when memory is switched on
        host.process_query("memory is on")
        loads = get_embedder.call_count
        host.process_query("still on")

    assert loads >= 1
    assert get_embedder.call_count == loads, "the deferred init runs once"
    assert host.memory_store is not None
    assert host._embedding_dim == 768


def test_warm_up_skips_the_embedder_while_memory_is_off(db_path):
    with _mocked_embedder() as get_embedder:
        host = _Host()
        host.init_memory(db_path=db_path, incognito=True)
        host.warm_up()

    assert get_embedder.call_count == 0


def test_recall_while_memory_is_off_loads_the_embedder(db_path):
    """An explicit memory read is real use, so it pays for the embedder."""
    with _mocked_embedder() as get_embedder:
        host = _Host()
        host.init_memory(db_path=db_path, incognito=True)
        with patch.object(MemoryMixin, "_faiss_search", return_value=[]):
            host._hybrid_search("what did I say about the gateway?")

    assert get_embedder.call_count >= 1


def test_deferred_embedder_failure_fails_loudly(db_path):
    def _down(*_args, **_kwargs):
        raise ConnectionError("Connection refused")

    with _mocked_embedder(embed_side_effect=_down):
        host = _Host()
        host.init_memory(db_path=db_path, incognito=True)
        saved_registry = dict(_TOOL_REGISTRY)
        host.register_memory_tools()
        try:
            recall = _TOOL_REGISTRY["recall"]["function"]
            host._incognito = False
            host.process_query("memory is on")

            assert host.memory_store is None
            assert (
                host._memory_unavailable_reason
                == MEMORY_UNAVAILABLE_SERVICE_UNREACHABLE
            )
            result = recall(query="gateway")
            assert result["status"] == "error"
            assert "Memory is unavailable" in result["message"]
            with pytest.raises(RuntimeError, match="Memory is unavailable"):
                host._hybrid_search("gateway")
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved_registry)


def test_gaia_memory_disabled_still_wins(db_path, monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with _mocked_embedder() as get_embedder:
        host = _Host()
        host.init_memory(db_path=db_path, incognito=True)
        host._incognito = False
        host.process_query("hello")

    assert get_embedder.call_count == 0
    assert host.memory_store is None
    assert host._memory_unavailable_reason == MEMORY_UNAVAILABLE_DISABLED_BY_ENV
