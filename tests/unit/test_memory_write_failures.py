# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A memory saved without a search vector must be visible, not silent.

When the embedder or the FAISS index fails on a write, the row is still saved
but recall by meaning cannot find it. These tests pin that the failure warns
once per session per kind, the row survives, the session counts it, and
``gaia memory status`` reports the unembedded rows.
"""

import argparse
import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.memory import (
    WRITE_FAILURE_EMBED,
    WRITE_FAILURE_INDEX,
    WRITE_FAILURE_STORE,
    MemoryMixin,
)
from gaia.agents.base.memory_store import MemoryStore

LOGGER = "gaia.agents.base.memory"


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


@pytest.fixture
def host(tmp_path):
    agent = _Agent(tmp_path / "memory.db")
    yield agent
    agent._memory_store.close()


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def _save(agent, content):
    kid = agent._memory_store.store(category="fact", content=content)
    agent._embed_and_index(kid, content, "remembered fact")
    return kid


def test_embed_failure_warns_once_keeps_the_row_and_counts_it(host, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with patch.object(
        MemoryMixin, "_embed_text", side_effect=RuntimeError("embedder down")
    ):
        first = _save(host, "The deploy target is the staging cluster")
        second = _save(host, "Release notes live in the docs folder")

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert first in message and "without a search vector" in message
    assert "embedder down" in message and "Rebuild Embeddings" in message

    for kid in (first, second):
        assert host._memory_store.get_item(kid) is not None
    assert host.memory_write_failures() == {WRITE_FAILURE_EMBED: 2}
    assert host._memory_store.get_embedding_coverage()["without_embedding"] == 2


def test_each_failure_kind_warns_on_its_own_first_occurrence(host, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    with patch.object(MemoryMixin, "_embed_text", side_effect=RuntimeError("down")):
        _save(host, "first fact")
    host._note_memory_write_failure(WRITE_FAILURE_STORE, "lost a row: %s", "boom")
    host._note_memory_write_failure(WRITE_FAILURE_STORE, "lost a row: %s", "again")

    assert len(_warnings(caplog)) == 2
    assert host.memory_write_failures() == {
        WRITE_FAILURE_EMBED: 1,
        WRITE_FAILURE_STORE: 2,
    }


def test_successful_write_stores_the_vector_and_counts_nothing(host, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    vec = np.ones(8, dtype=np.float32) / np.sqrt(8)
    with patch.object(MemoryMixin, "_embed_text", return_value=vec):
        _save(host, "The build runs on Linux")

    assert not _warnings(caplog)
    assert host.memory_write_failures() == {}
    assert host._memory_store.get_embedding_coverage()["without_embedding"] == 0


def test_faiss_add_failure_warns_once_and_counts_an_index_gap(host, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    broken_index = MagicMock()
    broken_index.add.side_effect = RuntimeError("index corrupt")
    host._faiss_index = broken_index
    vec = np.ones(8, dtype=np.float32)

    host._faiss_add("id-1", vec)
    host._faiss_add("id-2", vec)

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "id-1" in warnings[0].getMessage()
    assert host.memory_write_failures() == {WRITE_FAILURE_INDEX: 2}


def test_a_failed_re_embed_after_an_edit_leaves_no_stale_vector(host, caplog):
    """The row holds the new text, so the old vector must not keep matching."""
    kid = host._memory_store.store(category="fact", content="Deploys go to staging")
    with (
        patch.object(MemoryMixin, "_faiss_remove") as remove,
        patch.object(MemoryMixin, "_faiss_add") as add,
        patch.object(
            MemoryMixin, "_embed_text", side_effect=RuntimeError("embedder down")
        ),
    ):
        stored = host._embed_and_index(
            kid, "Deploys go to production", "edited fact", replace=True
        )

    assert stored is False
    remove.assert_called_once_with(kid)
    add.assert_not_called()
    assert host.memory_write_failures() == {WRITE_FAILURE_EMBED: 1}


def test_remember_tool_still_stores_when_the_embedder_fails(host, caplog):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    caplog.set_level(logging.WARNING, logger=LOGGER)
    host.register_memory_tools()
    remember = _TOOL_REGISTRY["remember"]["function"]
    with patch.object(MemoryMixin, "_embed_text", side_effect=RuntimeError("down")):
        result = remember(fact="Our CI runs nightly at 2am", category="fact")

    assert result["status"] == "stored"
    assert host._memory_store.get_item(result["knowledge_id"]) is not None
    assert len(_warnings(caplog)) == 1
    assert host.memory_write_failures() == {WRITE_FAILURE_EMBED: 1}


def test_memory_status_reports_entries_without_a_vector(tmp_path, monkeypatch, capsys):
    from gaia.cli import handle_memory_command

    db_path = tmp_path / "status.db"
    monkeypatch.setenv("GAIA_MEMORY_DB", str(db_path))
    store = MemoryStore(db_path=db_path)
    embedded = store.store(category="fact", content="Embedded fact about builds")
    store.store_embedding(embedded, np.ones(8, dtype=np.float32).tobytes())
    store.store(category="fact", content="Fact the embedder never reached")
    store.close()

    handle_memory_command(argparse.Namespace(memory_action="status"))
    out = capsys.readouterr().out

    assert "Search vectors: 1 of 2 active entries" in out
    assert "Without vector: 1" in out
    assert "Rebuild Embeddings" in out


def test_memory_status_omits_the_repair_hint_when_everything_is_embedded(
    tmp_path, monkeypatch, capsys
):
    from gaia.cli import handle_memory_command

    db_path = tmp_path / "status.db"
    monkeypatch.setenv("GAIA_MEMORY_DB", str(db_path))
    store = MemoryStore(db_path=db_path)
    kid = store.store(category="fact", content="Embedded fact about builds")
    store.store_embedding(kid, np.ones(8, dtype=np.float32).tobytes())
    store.close()

    handle_memory_command(argparse.Namespace(memory_action="status"))
    out = capsys.readouterr().out

    assert "Search vectors: 1 of 1 active entries" in out
    assert "Without vector" not in out
