# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A ChatAgent built with memory off must not load the embedding model.

The Agent UI passes ``memory_incognito=True`` for a private chat or with memory
switched off; the embedder (a ~300 MB llama-server) is then overhead the
session never uses. The embedder is mocked — nothing here talks to Lemonade.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

from gaia.agents.base.memory import MemoryMixin


@pytest.fixture
def get_embedder(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
    monkeypatch.setenv("GAIA_MEMORY_DB", str(tmp_path / "memory.db"))
    embedder = MagicMock()
    embedder.embed.return_value = [np.ones(768, dtype=np.float32).tolist()]
    mock = MagicMock(return_value=embedder)
    with (
        patch.object(MemoryMixin, "_get_embedder", mock),
        patch.object(MemoryMixin, "_get_embedding_cache") as cache,
        patch.object(MemoryMixin, "_backfill_embeddings", return_value=0),
        patch.object(MemoryMixin, "_rebuild_faiss_index", return_value=None),
        patch.object(MemoryMixin, "_rebuild_proc_faiss_index", return_value=None),
        patch(
            "gaia.agents.base.memory._system_context_is_enabled",
            return_value=False,
        ),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch("gaia.agents.base.agent.AgentSDK"),
        patch("gaia.rag.sdk.AgentSDK"),
        patch("gaia.llm.lemonade_client.LemonadeClient"),
    ):
        cache.return_value.get.return_value = None
        yield mock


def _build(tmp_path, **overrides):
    config = ChatAgentConfig(
        silent_mode=True,
        debug=False,
        max_steps=1,
        allowed_paths=[str(tmp_path)],
        **overrides,
    )
    agent = ChatAgent(config)
    agent.stop_watching()
    return agent


def test_memory_incognito_skips_the_embedder_at_construction(tmp_path, get_embedder):
    agent = _build(tmp_path, memory_incognito=True)

    assert get_embedder.call_count == 0
    assert agent._incognito is True
    assert agent.memory_store is not None


def test_memory_on_still_validates_the_embedder(tmp_path, get_embedder):
    agent = _build(tmp_path)

    assert get_embedder.call_count >= 1
    assert agent._incognito is False
