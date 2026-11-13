# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A multi-document search reads every document, not just the loudest one.

Each search key took a global top 5, so one long document that matched the
question everywhere filled every slot and "list the total from each quote"
never saw the other quotes.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig

QUERY = "List the total price from every quote"
LOUD = "Vendor A quote: the total price of every quote line is listed here. "
QUIET = "Supplier {n} sends its offer. Amount due: {total} dollars."


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _vector(text: str) -> np.ndarray:
    """The loud document sits on the query; the quiet ones well away from it."""
    if text.startswith("Vendor A") or text == QUERY:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
    return np.array([0.0, 1.0, 0.0, 0.0], dtype="float32")


class _Host(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query):
        return [query]


@pytest.fixture
def quotes(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        rag = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                use_llm_chunking=False,
            )
        )
    rag._load_embedder = lambda: None
    rag._encode_texts = lambda texts, **_: np.stack([_vector(t) for t in texts])
    rag._encode_query = lambda q: _vector(q)[None, :]
    rag._get_hmac_key = lambda: b"test-cache-key" * 3
    host = _Host(rag)
    paths = []
    loud = tmp_path / "vendor_A.txt"
    loud.write_text(
        "\n\n".join(f"Vendor A section {i}. " + LOUD * 25 for i in range(60)),
        encoding="utf-8",
    )
    paths.append(loud)
    for n, total in enumerate([1200, 3400, 5600, 7800, 9100], start=1):
        quiet = tmp_path / f"quote_{n}.txt"
        quiet.write_text(QUIET.format(n=n, total=total), encoding="utf-8")
        paths.append(quiet)
    for path in paths:
        assert rag.index_document(str(path))["success"]
        host.indexed_files.add(str(path))
    assert len(rag.chunks) > 50  # past the small-library whole-index shortcut
    host.register_rag_tools()
    return rag, paths


def test_a_search_over_six_quotes_reads_all_six(quotes):
    _, paths = quotes

    result = _TOOL_REGISTRY["query_documents"]["function"](query=QUERY)

    assert result["status"] == "success", result
    sources = {Path(c["source_file"]).name for c in result["chunks"]}
    assert sources == {p.name for p in paths}
    text = " ".join(c["content"] for c in result["chunks"])
    for total in (1200, 3400, 5600, 7800, 9100):
        assert str(total) in text


def test_the_balanced_search_stays_within_its_budget(quotes):
    result = _TOOL_REGISTRY["query_documents"]["function"](query=QUERY)

    # The loud document puts the library on the 10- or 20-chunk tier.
    assert result["num_chunks"] <= 20
    scores = [c["relevance_score"] for c in result["chunks"]]
    assert scores == sorted(scores, reverse=True)


def test_one_document_alone_is_searched_as_before(quotes):
    rag, paths = quotes
    for path in paths[1:]:
        assert rag.remove_document(str(path))
    calls = []
    original = rag._retrieve_chunks_from_file
    rag._retrieve_chunks_from_file = lambda *a: calls.append(a) or original(*a)

    result = _TOOL_REGISTRY["query_documents"]["function"](query=QUERY)

    assert result["status"] == "success"
    assert calls == []
