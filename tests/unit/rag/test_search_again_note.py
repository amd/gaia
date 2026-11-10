# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A search asks for one more search before "not in the document" — once a turn.

Gemma gave up after one search on long reports and said a figure wasn't there.
The search result now asks it to reword and search again; the ask must not
repeat on that second search, or a fact that really is absent would never get
its "not in the document" answer.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import (
    _SEARCH_AGAIN_NOTE,
    RAGToolsMixin,
    _answers_in_question_language,
    _asks_once_for_another_search,
)

_CHUNKS = ["Revenue grew 4% in Q3.", "Headcount was flat."]


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(RAGToolsMixin):
    max_chunks = 5
    _turn_seq = 1

    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None

    def _generate_search_keys(self, query):
        return [query]


def _rag_with(doc: Path, chunks=_CHUNKS) -> MagicMock:
    rag = MagicMock()
    rag.indexed_files = {str(doc)}
    rag.chunks = list(chunks)
    rag.chunk_to_file = {i: str(doc) for i in range(len(chunks))}
    rag.file_to_chunk_indices = {str(doc): list(range(len(chunks)))}
    rag.file_metadata = {}
    rag._retrieve_chunks_from_file.return_value = (
        list(chunks),
        [0.9] * len(chunks),
    )
    rag._retrieve_chunks.return_value = (list(chunks), [0.9] * len(chunks))
    return rag


def _host(tmp_path, chunks=_CHUNKS):
    doc = tmp_path / "annual report.md"
    host = _RagHost(_rag_with(doc, chunks))
    host.register_rag_tools()
    return host, doc


def _ask_file(doc, query="operating margin"):
    return _TOOL_REGISTRY["query_specific_file"]["function"](
        file_path=str(doc), query=query
    )


def test_the_first_search_of_a_turn_asks_for_another(tmp_path):
    _, doc = _host(tmp_path)

    result = _ask_file(doc)

    assert result["status"] == "success", result
    assert result["instruction"].endswith(_SEARCH_AGAIN_NOTE)


def test_the_second_search_lets_the_model_say_it_is_not_there(tmp_path):
    """A fact that truly is absent must get its answer after the re-search."""
    _, doc = _host(tmp_path)

    first = _ask_file(doc, "operating margin")
    second = _ask_file(doc, "EBIT margin profit percentage")
    third = _ask_file(doc, "margin")

    assert _SEARCH_AGAIN_NOTE in first["instruction"]
    assert _SEARCH_AGAIN_NOTE not in second["instruction"]
    assert _SEARCH_AGAIN_NOTE not in third["instruction"]


def test_the_ask_is_shared_across_both_search_tools(tmp_path):
    """Switching tools for the re-search must not earn a second ask."""
    _, doc = _host(tmp_path)

    first = _TOOL_REGISTRY["query_documents"]["function"](query="operating margin")
    second = _ask_file(doc, "EBIT margin")

    assert first["status"] == "success", first
    assert first["instruction"].endswith(_SEARCH_AGAIN_NOTE)
    assert _SEARCH_AGAIN_NOTE not in second["instruction"]


def test_a_new_turn_gets_the_ask_again(tmp_path):
    host, doc = _host(tmp_path)

    _ask_file(doc)
    host._turn_seq += 1
    again = _ask_file(doc)

    assert again["instruction"].endswith(_SEARCH_AGAIN_NOTE)


def test_a_search_with_no_chunks_is_left_alone_and_keeps_the_ask(tmp_path):
    """An empty result already says "nothing found"; it must not spend the ask."""
    host, doc = _host(tmp_path, chunks=[])

    empty = _ask_file(doc)

    assert empty["status"] == "success", empty
    assert _SEARCH_AGAIN_NOTE not in empty.get("instruction", "")

    host.rag._retrieve_chunks_from_file.return_value = (list(_CHUNKS), [0.9, 0.8])
    host.rag.chunks = list(_CHUNKS)
    host.rag.chunk_to_file = {0: str(doc), 1: str(doc)}
    host.rag.file_to_chunk_indices = {str(doc): [0, 1]}
    found = _ask_file(doc)

    assert found["instruction"].endswith(_SEARCH_AGAIN_NOTE)


@pytest.mark.parametrize(
    "result",
    [
        {"status": "error", "error": "File not found"},
        {"status": "no_documents", "message": "Nothing indexed"},
        {"status": "success", "chunks": [], "instruction": "Say nothing was found."},
    ],
)
def test_results_without_chunks_are_returned_untouched(result):
    host = SimpleNamespace(_turn_seq=3)
    search = _asks_once_for_another_search(host)(lambda: dict(result))

    assert search() == result
    assert not hasattr(host, "_search_again_turn")


def test_a_host_with_no_turn_counter_asks_only_once():
    """Without turns to reset on, the safe side is never to ask twice."""
    host = SimpleNamespace()
    search = _asks_once_for_another_search(host)(
        lambda: {"status": "success", "chunks": [{"content": "x"}], "instruction": ""}
    )

    assert search()["instruction"] == _SEARCH_AGAIN_NOTE
    assert search()["instruction"] == ""


def test_the_question_reminder_stays_last():
    """The language note is written to come right before the answer."""
    host = SimpleNamespace(_turn_seq=1, _memory_turn_query="When is it given?")
    search = _answers_in_question_language(host)(
        _asks_once_for_another_search(host)(
            lambda: {
                "status": "success",
                "instruction": "Cite the page.",
                "chunks": [{"content": "تعطى الجرعة الثانية بعد شهر"}],
            }
        )
    )

    instruction = search()["instruction"]

    assert instruction.index(_SEARCH_AGAIN_NOTE) < instruction.index(
        "When is it given?"
    )


def test_the_ask_names_its_own_limit():
    assert "search once more" in _SEARCH_AGAIN_NOTE
    assert "If that search misses too" in _SEARCH_AGAIN_NOTE
    assert "document's language" in _SEARCH_AGAIN_NOTE
