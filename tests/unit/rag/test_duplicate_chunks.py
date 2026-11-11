# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document indexed under two names counts once in a search (#4740).

Both copies came back from the search, so the copy took a place in the top k
that other evidence should have had, and the agent cited whichever copy was
indexed first.
"""

import hashlib
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin, _left_out
from gaia.rag.sdk import RAGSDK, RAGConfig

DIM = 8
POLICY = "Remote work is allowed three days a week with manager approval."
OTHERS = [
    "The office opens at eight and closes at six on weekdays.",
    "Expense reports are due on the fifth working day of each month.",
    "New laptops are issued every three years by the IT desk.",
]


def _vector(text: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "little")
    return np.random.default_rng(seed).random(DIM).astype("float32")


@pytest.fixture
def rag(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                max_chunks=3,
            )
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **kw: (
        np.stack([_vector(t) for t in texts]) if texts else np.zeros((0, DIM))
    )
    # The question is the policy itself, so its copies are the closest chunks.
    sdk._encode_query = lambda query: _vector(POLICY)[None, :]
    sdk._get_hmac_key = lambda: b"test-index-key" * 3
    return sdk


@pytest.fixture
def docs(tmp_path, rag):
    """The policy saved under two names, plus three other documents."""
    paths = {}
    for name, text in [
        ("policy.txt", POLICY),
        ("policy (copy).txt", POLICY),
        *[(f"other{n}.txt", text) for n, text in enumerate(OTHERS)],
    ]:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        assert rag.index_document(str(path))["success"]
        paths[name] = str(path)
    assert rag.chunks.count(POLICY) == 2
    return paths


def test_the_model_gets_k_different_chunks(rag, docs):
    snapshot = rag._search_chunks("How many days can I work from home?")

    chunks = snapshot["retrieved_chunks"]
    assert len(chunks) == 3
    assert len(set(chunks)) == 3, "a copy took one of the 3 places"
    assert chunks[0] == POLICY


def test_a_collapsed_chunk_keeps_both_sources(rag, docs):
    snapshot = rag._search_chunks("How many days can I work from home?")
    _, _, metadata = rag._retrieve_chunks_with_metadata("", snapshot)

    assert sorted(metadata[0]["source_paths"]) == sorted(
        [docs["policy.txt"], docs["policy (copy).txt"]]
    )
    assert sorted(metadata[0]["source_files"]) == ["policy (copy).txt", "policy.txt"]
    assert metadata[0]["source_path"] in metadata[0]["source_paths"]
    for other in metadata[1:]:
        assert other["source_paths"] == [other["source_path"]]


def test_query_names_both_copies_and_k_chunks(rag, docs):
    rag.chat.send.return_value.text = "Three days."
    rag.chat.send.return_value.stats = {}

    response = rag.query("How many days can I work from home?")

    assert len(response.chunks) == 3
    assert len(set(response.chunks)) == 3
    assert {"policy.txt", "policy (copy).txt"} <= set(
        response.query_metadata["source_files"]
    )
    prompt = rag.chat.send.call_args.args[0]
    assert prompt.count(POLICY) == 1


def test_a_document_with_no_copy_is_unchanged(rag, tmp_path):
    for n, text in enumerate([POLICY, *OTHERS]):
        path = tmp_path / f"doc{n}.txt"
        path.write_text(text, encoding="utf-8")
        assert rag.index_document(str(path))["success"]

    snapshot = rag._search_chunks("How many days can I work from home?")

    assert len(snapshot["retrieved_chunks"]) == 3
    assert snapshot["duplicate_indices"] == [[], [], []]


def test_an_index_of_only_copies_returns_what_there_is(rag, tmp_path):
    for name in ("a.txt", "b.txt", "c.txt"):
        path = tmp_path / name
        path.write_text(POLICY, encoding="utf-8")
        assert rag.index_document(str(path))["success"]

    snapshot = rag._search_chunks("How many days can I work from home?")

    assert snapshot["retrieved_chunks"] == [POLICY]
    assert len(snapshot["duplicate_indices"][0]) == 2


@pytest.fixture
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(RAGToolsMixin):
    max_chunks = 3

    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set(rag.indexed_files)
        self.current_session = None

    def _generate_search_keys(self, query):
        return [query]


def _tool(rag, name):
    _RagHost(rag).register_rag_tools()
    return _TOOL_REGISTRY[name]["function"]


@pytest.mark.usefixtures("_clean_registry")
def test_the_agent_cites_every_file_holding_the_text(rag, docs):
    result = _tool(rag, "query_documents")(query="work from home days")

    assert result["status"] == "success", result
    (policy,) = [c for c in result["chunks"] if c["content"] == POLICY]
    cited = {policy["source_file"], *policy["also_in"]}
    copies = {str(Path(docs[n]).resolve()) for n in ("policy.txt", "policy (copy).txt")}
    assert cited == copies
    assert copies <= set(result["source_files"])
    for chunk in result["chunks"]:
        if chunk["content"] != POLICY:
            assert "also_in" not in chunk


@pytest.mark.usefixtures("_clean_registry")
def test_a_file_search_resolves_the_chunk_in_that_file(rag, docs):
    copy = docs["policy (copy).txt"]

    result = _tool(rag, "query_specific_file")(file_path=copy, query="work from home")

    assert result["status"] == "success", result
    (policy,) = [c for c in result["chunks"] if c["content"] == POLICY]
    assert rag.chunk_to_file[policy["_debug_chunk_index"]] == copy


@pytest.mark.usefixtures("_clean_registry")
def test_a_copy_in_a_left_out_file_does_not_hide_the_served_one(rag, docs, tmp_path):
    # Too much text to hand the agent whole, so the search alone decides.
    handbook = tmp_path / "handbook.txt"
    handbook.write_text(
        " ".join(f"Rule {n} covers desk booking in building {n}." for n in range(400)),
        encoding="utf-8",
    )
    assert rag.index_document(str(handbook))["success"]
    # A query with no words in common, so only the vector search can find the
    # policy. Leave out whichever copy it kept: its twin is the one served.
    query = "qqq"
    snapshot = rag._search_chunks(query)
    kept_by_search = rag.chunk_to_file[snapshot["retrieved_indices"][0]]
    served = next(
        path
        for name, path in docs.items()
        if name.startswith("policy") and path != kept_by_search
    )
    left_out = {kept_by_search: _left_out(kept_by_search, "stale", "It changed")}

    with patch("gaia.agents.tools.rag_tools._bring_current", return_value=left_out):
        result = _tool(rag, "query_documents")(query=query)

    assert result["status"] == "success", result
    (policy,) = [c for c in result["chunks"] if c["content"] == POLICY]
    assert policy["source_file"] == str(Path(served).resolve())
    assert "also_in" not in policy
