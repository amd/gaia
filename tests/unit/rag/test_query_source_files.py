# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every chunk ``query_specific_file`` returns names its file (#5089).

The single-file search put the path only at the top of its result, so each
chunk reached the agent and the chunk cards with no source: the cards read
``"source": null`` and ``"source_files": []``, and a fact could not be traced
back to the document it came from.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.ui.sse_handler import SSEOutputHandler

_CHUNKS = [
    "Liquidation preference: 1x non-participating.",
    "Valuation cap of $12M post-money.",
]


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(RAGToolsMixin):
    max_chunks = 5

    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None

    def _generate_search_keys(self, query):
        return [query]


def _rag_with(doc: Path) -> MagicMock:
    rag = MagicMock()
    rag.indexed_files = {str(doc)}
    rag.chunks = list(_CHUNKS)
    rag.chunk_to_file = {0: str(doc), 1: str(doc)}
    rag.file_to_chunk_indices = {str(doc): [0, 1]}
    rag.file_metadata = {}
    rag._retrieve_chunks_from_file.return_value = (list(_CHUNKS), [0.9, 0.8])
    return rag


def _query(doc: Path) -> dict:
    _RagHost(_rag_with(doc)).register_rag_tools()
    return _TOOL_REGISTRY["query_specific_file"]["function"](
        file_path=str(doc), query="valuation cap"
    )


def test_every_chunk_names_its_file(tmp_path):
    doc = tmp_path / "Term Sheet.docx"
    result = _query(doc)

    expected = str(doc.resolve())
    assert result["status"] == "success", result
    assert [c["source_file"] for c in result["chunks"]] == [expected, expected]
    assert result["source_files"] == [expected]


def test_the_chunk_cards_show_the_file(tmp_path):
    doc = tmp_path / "Term Sheet.docx"
    handler = SSEOutputHandler()
    handler.pretty_print_json(_query(doc), title="Result")
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())

    (card,) = [e["result_data"] for e in events if "result_data" in e]
    assert card["source_files"] == [str(doc.resolve())]
    assert {c["source"] for c in card["chunks"]} == {"Term Sheet.docx"}
    assert {c["sourcePath"] for c in card["chunks"]} == {str(doc.resolve())}
