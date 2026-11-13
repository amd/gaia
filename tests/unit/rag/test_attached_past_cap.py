# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat with more documents attached than the index holds at once (#7446).

The index holds 100 documents. Attaching 150 used to index all of them, each
past the 100th pushing out an earlier one: with 1500 attached that was ten
minutes of evictions, and then the model, shown only the last 100, answered
that a document the user had attached "is not attached".
"""

import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.security import PathValidator

ATTACHED = 150


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def library(tmp_path):
    """A real index with fake vectors, 150 station files, and an eviction log."""
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                use_llm_chunking=False,
            )
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **_: np.ones((len(texts), 4), dtype="float32")
    sdk._encode_query = lambda _q: np.ones((1, 4), dtype="float32")
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    evicted = []
    evict = sdk._evict_lru_document

    def counted():
        path = evict()
        evicted.append(path)
        return path

    sdk._evict_lru_document = counted
    docs = tmp_path / "stations"
    docs.mkdir()
    paths = []
    for n in range(ATTACHED):
        path = docs / f"station_{n:03d}.txt"
        path.write_text(f"Station {n:03d} code is CODE-{n:03d}.", encoding="utf-8")
        paths.append(str(path))
    return SimpleNamespace(rag=sdk, paths=paths, evicted=evicted, root=tmp_path)


class _Handler:
    def __init__(self):
        self.events = []
        self.cancelled = threading.Event()

    def _emit(self, event):
        self.events.append(event)

    def print_tool_usage(self, _name):
        pass

    def pretty_print_json(self, *_args, **_kwargs):
        pass

    def summary(self):
        [result] = [e for e in self.events if e.get("type") == "tool_result"]
        return result


class _Host(RAGToolsMixin):
    def __init__(self, rag, attached):
        self.rag = rag
        self.indexed_files = set()
        self.attached_documents = list(attached)
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query):
        return [query]


def _load_in_the_ui(library):
    from gaia.ui._chat_helpers import _index_rag_with_progress

    host = _Host(library.rag, library.paths)
    handler = _Handler()
    _index_rag_with_progress(host, library.paths, handler)
    return host, handler


def test_the_agent_ui_loads_up_to_the_cap_and_evicts_nothing(library):
    _, handler = _load_in_the_ui(library)

    assert library.evicted == []
    assert len(library.rag.indexed_files) == 100
    summary = handler.summary()
    assert summary["success"] is True
    assert "50 more not loaded" in summary["summary"]
    assert "at most 100 documents at once" in summary["summary"]


def test_an_attached_document_past_the_cap_loads_when_asked_for(library):
    host, _ = _load_in_the_ui(library)
    host.register_rag_tools()
    target = library.paths[149]
    assert target not in library.rag.indexed_files

    result = _TOOL_REGISTRY["query_specific_file"]["function"](
        file_path="station_149.txt", query="Station 149 code"
    )

    assert result["status"] == "success", result
    assert "CODE-149" in " ".join(c["content"] for c in result["chunks"])
    # One document made room for it, and never the one asked about.
    assert len(library.evicted) == 1
    assert library.evicted[0] != target
    assert target in library.rag.indexed_files


def test_a_search_says_which_attached_documents_it_could_not_reach(library):
    host, _ = _load_in_the_ui(library)
    host.register_rag_tools()

    result = _TOOL_REGISTRY["query_documents"]["function"](query="Station 149 code")

    assert result["attached_not_searched"] == 50
    assert "never say one is not attached" in result["instruction"]
    assert "query_specific_file" in result["instruction"]


def test_the_flagship_is_told_every_attached_document_is_attached(library):
    gaia_agent = pytest.importorskip("gaia_agent.chat_agent")
    agent = gaia_agent.ChatAgent.__new__(gaia_agent.ChatAgent)
    agent.config = gaia_agent.ChatAgentConfig()
    agent._rag = library.rag
    agent.indexed_files = set()
    agent.path_validator = PathValidator([str(library.root)])
    agent.rebuild_system_prompt = lambda: None

    agent._index_documents(library.paths)
    line = agent._indexed_documents_line()

    assert library.evicted == []
    assert len(library.rag.indexed_files) == 100
    assert "50 more documents are attached to this chat" in line
    assert "never say one is not attached" in line
    assert "station_100.txt" in line


def test_a_relayed_flagship_loads_up_to_the_cap_and_evicts_nothing(library):
    server = pytest.importorskip("gaia_agent.server")
    agent = SimpleNamespace(
        rag=library.rag,
        indexed_files=set(),
        path_validator=PathValidator([str(library.root / "cache")]),
        rebuild_system_prompt=lambda: None,
        _cancel_event=None,
    )
    handler = _Handler()

    server._index_attached_documents(agent, library.paths, handler)

    assert library.evicted == []
    assert len(library.rag.indexed_files) == 100
    summary = handler.summary()
    assert summary["success"] is True
    assert "50 more not loaded" in summary["summary"]
    # The 50 left out are still granted, so asking for one can load it.
    assert agent.path_validator.is_path_allowed(library.paths[149], prompt_user=False)


def test_the_agent_ui_warns_at_the_same_cap():
    bar = (
        Path(__file__).parents[3]
        / "src/gaia/apps/webui/src/components/SessionDocsBar.tsx"
    )
    shown = re.search(r"SEARCHED_AT_ONCE = (\d+);", bar.read_text(encoding="utf-8"))
    assert int(shown.group(1)) == RAGConfig().max_indexed_files
