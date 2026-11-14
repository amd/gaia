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


def _index(tmp_path, **config):
    """A real index with fake vectors and a log of the documents it pushed out."""
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                use_llm_chunking=False,
                **config,
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
    return sdk, evicted


@pytest.fixture
def library(tmp_path):
    """150 one-passage station files: past the 100-document cap."""
    sdk, evicted = _index(tmp_path)
    docs = tmp_path / "stations"
    docs.mkdir()
    paths = []
    for n in range(ATTACHED):
        path = docs / f"station_{n:03d}.txt"
        path.write_text(f"Station {n:03d} code is CODE-{n:03d}.", encoding="utf-8")
        paths.append(str(path))
    return SimpleNamespace(rag=sdk, paths=paths, evicted=evicted, root=tmp_path)


@pytest.fixture
def books(tmp_path):
    """Five ten-passage books in a 25-passage index: full long before 100 files."""
    sdk, evicted = _index(tmp_path, chunk_size=20, chunk_overlap=0, max_total_chunks=25)
    shelf = tmp_path / "books"
    shelf.mkdir()
    paths = []
    for n in range(5):
        path = shelf / f"book_{n}.txt"
        path.write_text(
            "\n\n".join(
                f"Book {n} part {k} says the code is BOOK-{n}-{k}, a long "
                "passage of words to fill the chunk."
                for k in range(10)
            ),
            encoding="utf-8",
        )
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
    headline = summary["summary"].split("\n")[0]
    assert "50 more not loaded" in headline
    assert "holds at most 100 documents at once" in headline
    assert "passages" not in headline


def test_a_full_passage_cap_stops_loading_and_names_it(library):
    library.rag.config.max_total_chunks = 40
    _, handler = _load_in_the_ui(library)

    assert library.evicted == []
    assert len(library.rag.chunks) == 40
    headline = handler.summary()["summary"].split("\n")[0]
    assert "110 more not loaded" in headline
    assert "holds at most 40 passages at once" in headline
    assert "100 documents" not in headline


def test_long_documents_stop_loading_at_the_first_one_pushed_out(books):
    _, handler = _load_in_the_ui(books)

    # The third book pushed the first out to fit; loading stopped there.
    assert books.evicted == [books.paths[0]]
    assert set(books.rag.indexed_files) == set(books.paths[1:3])
    headline = handler.summary()["summary"].split("\n")[0]
    assert headline.startswith("Indexed 2 document(s)")
    assert "3 more not loaded" in headline
    assert "documents are long" in headline
    assert "holds at most 25 passages at once" in headline
    assert "100 documents" not in headline


def test_long_documents_are_not_reloaded_on_the_next_turn(books):
    from gaia.ui._chat_helpers import _index_rag_with_progress

    host, _ = _load_in_the_ui(books)
    handler = _Handler()
    _index_rag_with_progress(host, books.paths, handler)

    # Reloading one each turn would push another out: the index would churn.
    assert books.evicted == [books.paths[0]]
    assert "3 more not loaded" in handler.summary()["summary"]


def test_a_search_names_the_passage_limit_for_long_documents(books):
    host, _ = _load_in_the_ui(books)
    host.register_rag_tools()

    result = _TOOL_REGISTRY["query_documents"]["function"](query="Book 4 code")

    assert result["attached_not_searched"] == 3
    assert "holds at most 25 passages at once" in result["instruction"]
    assert "100 documents" not in result["instruction"]
    assert "never say one is not attached" in result["instruction"]


def test_a_long_document_left_out_loads_when_asked_for(books):
    host, _ = _load_in_the_ui(books)
    host.register_rag_tools()

    result = _TOOL_REGISTRY["query_specific_file"]["function"](
        file_path="book_4.txt", query="Book 4 code"
    )

    assert result["status"] == "success", result
    assert "BOOK-4-" in " ".join(c["content"] for c in result["chunks"])
    assert books.paths[4] in books.rag.indexed_files


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


def test_the_flagship_names_the_passage_limit_for_long_documents(books):
    gaia_agent = pytest.importorskip("gaia_agent.chat_agent")
    agent = gaia_agent.ChatAgent.__new__(gaia_agent.ChatAgent)
    agent.config = gaia_agent.ChatAgentConfig()
    agent._rag = books.rag
    agent.indexed_files = set()
    agent.path_validator = PathValidator([str(books.root)])
    agent.rebuild_system_prompt = lambda: None

    agent._index_documents(books.paths)
    line = agent._indexed_documents_line()

    assert books.evicted == [books.paths[0]]
    assert books.paths[0] not in agent.indexed_files
    assert "3 more documents are attached to this chat" in line
    assert "holds at most 25 passages at once" in line
    assert "100 documents" not in line
    assert "book_0.txt" in line


def test_a_relayed_flagship_names_the_passage_limit_for_long_documents(books):
    server = pytest.importorskip("gaia_agent.server")
    agent = SimpleNamespace(
        rag=books.rag,
        indexed_files=set(),
        path_validator=PathValidator([str(books.root / "cache")]),
        rebuild_system_prompt=lambda: None,
        _cancel_event=None,
    )
    handler = _Handler()

    server._index_attached_documents(agent, books.paths, handler)

    assert books.evicted == [books.paths[0]]
    summary = handler.summary()
    assert summary["success"] is True
    assert "3 more not loaded" in summary["summary"]
    assert "holds at most 25 passages at once" in summary["summary"]
    assert "100 documents" not in summary["summary"]
    assert agent.path_validator.is_path_allowed(books.paths[4], prompt_user=False)


def test_the_agent_ui_warns_at_the_same_cap():
    bar = (
        Path(__file__).parents[4]
        / "src/gaia/apps/webui/src/components/SessionDocsBar.tsx"
    )
    source = bar.read_text(encoding="utf-8")
    files = re.search(r"SEARCHED_AT_ONCE = (\d+);", source)
    passages = re.search(r"PASSAGES_AT_ONCE = (\d+);", source)
    assert int(files.group(1)) == RAGConfig().max_indexed_files
    assert int(passages.group(1)) == RAGConfig().max_total_chunks


def test_the_last_document_pushing_one_out_is_reported_in_the_agent_ui(books):
    from gaia.ui._chat_helpers import _index_rag_with_progress

    three = books.paths[:3]
    host = _Host(books.rag, three)
    handler = _Handler()
    _index_rag_with_progress(host, three, handler)

    # The third and last book pushed the first out; no later pass saw it full.
    assert books.evicted == [books.paths[0]]
    summary = handler.summary()["summary"]
    headline = summary.split("\n")[0]
    assert headline.startswith("Indexed 2 document(s)")
    assert "1 more not loaded" in headline
    assert "Not loaded: book_0.txt." in headline
    assert "holds at most 25 passages at once" in headline
    assert "book_0.txt — 10 chunks" in summary
    assert "(then pushed out to fit the index)" in summary


def test_the_last_document_pushing_one_out_is_reported_by_the_relay(books):
    server = pytest.importorskip("gaia_agent.server")
    three = books.paths[:3]
    agent = SimpleNamespace(
        rag=books.rag,
        indexed_files=set(),
        path_validator=PathValidator([str(books.root / "cache")]),
        rebuild_system_prompt=lambda: None,
        _cancel_event=None,
    )
    handler = _Handler()

    server._index_attached_documents(agent, three, handler)

    assert books.evicted == [books.paths[0]]
    summary = handler.summary()
    lines = summary["summary"].split("\n")
    assert "book_0.txt: 10 chunks (then pushed out to fit the index)" in lines
    assert "book_2.txt: 10 chunks" in lines
    assert lines[-1].startswith("1 more not loaded:")
    assert "Not loaded: book_0.txt." in lines[-1]
    assert "holds at most 25 passages at once" in lines[-1]
    assert summary["success"] is True
    assert summary["result_data"]["status"] == "indexed"
    assert agent.path_validator.is_path_allowed(books.paths[0], prompt_user=False)


def test_a_stop_right_after_a_push_out_reports_the_pushed_out_document(books):
    from gaia.ui._chat_helpers import _index_rag_with_progress

    four = books.paths[:4]
    host = _Host(books.rag, four)
    handler = _Handler()
    index = books.rag.index_document

    def index_and_stop(path):
        result = index(path)
        if result.get("evicted"):
            handler.cancelled.set()
        return result

    books.rag.index_document = index_and_stop
    _index_rag_with_progress(host, four, handler)

    assert books.evicted == [books.paths[0]]
    assert set(books.rag.indexed_files) == set(books.paths[1:3])
    assert books.rag.is_full()
    summary = handler.summary()
    text = summary["summary"]
    headline = text.split("\n")[0]
    assert summary["success"] is False
    # The stop count covers only the books never attempted.
    assert headline.startswith("Stopped: 1 of 4 document(s) not loaded.")
    assert "book_3.txt" not in text
    # The pushed-out book is named and marked, and the full index is explained.
    assert "1 more not loaded" in headline
    assert "Not loaded: book_0.txt." in headline
    assert "(then pushed out to fit the index)" in text
    assert "holds at most 25 passages at once" in headline
    assert "They load with your next message" not in headline
    assert ".." not in headline


def test_a_relayed_stop_right_after_a_push_out_reports_the_pushed_out_document(books):
    server = pytest.importorskip("gaia_agent.server")
    four = books.paths[:4]
    cancel = threading.Event()
    agent = SimpleNamespace(
        rag=books.rag,
        indexed_files=set(),
        path_validator=PathValidator([str(books.root / "cache")]),
        rebuild_system_prompt=lambda: None,
        _cancel_event=cancel,
    )
    handler = _Handler()
    index = books.rag.index_document

    def index_and_stop(path):
        result = index(path)
        if result.get("evicted"):
            cancel.set()
        return result

    books.rag.index_document = index_and_stop
    server._index_attached_documents(agent, four, handler)

    assert books.evicted == [books.paths[0]]
    assert set(books.rag.indexed_files) == set(books.paths[1:3])
    assert books.rag.is_full()
    summary = handler.summary()
    lines = summary["summary"].split("\n")
    stop_line = next(line for line in lines if line.startswith("Stopped:"))
    assert summary["success"] is False
    assert summary["result_data"]["status"] == "stopped"
    # The stop count covers only the book never attempted.
    assert "1 document was not loaded" in stop_line
    assert "book_3.txt" not in summary["summary"]
    assert "They load with your next message" not in stop_line
    assert "holds at most 25 passages at once" in stop_line
    # The pushed-out book is named, marked, and still loadable by name.
    assert "book_0.txt: 10 chunks (then pushed out to fit the index)" in lines
    assert lines[-1].startswith("1 more not loaded:")
    assert "Not loaded: book_0.txt." in lines[-1]
    assert "holds at most 25 passages at once" in lines[-1]
    assert agent.path_validator.is_path_allowed(books.paths[0], prompt_user=False)
