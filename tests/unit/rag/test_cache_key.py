# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the RAG document cache is keyed on, and what it may reuse (#4606).

Cached chunks are cut to ``chunk_size`` / ``chunk_overlap`` / ``use_llm_chunking``,
so those settings are part of the key. The embedder is not: the cache stores
text and chunks only, and every load re-embeds with the configured model.
"""

import json
import os
from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag.sdk import _CACHE_OWNED_FILE, RAGSDK, RAGConfig


def _make(tmp_path, **overrides):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    config = RAGConfig(
        cache_dir=str(tmp_path / "cache"),
        allowed_paths=[str(tmp_path)],
        **{"use_llm_chunking": False, **overrides},
    )
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(config)
    sdk.encoded_with = []

    def _encode(texts, **_kwargs):
        sdk.encoded_with.append(sdk.config.embedding_model)
        return np.ones((len(texts), 4), dtype="float32")

    sdk._load_embedder = lambda: None
    sdk._encode_texts = _encode
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


@pytest.fixture
def document(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("The launch is on Tuesday. " * 40, encoding="utf-8")
    return str(path)


def test_same_config_gives_a_stable_path(tmp_path, document):
    first, second = _make(tmp_path), _make(tmp_path)
    assert first._get_cache_path(document) == second._get_cache_path(document)


@pytest.mark.parametrize(
    "change",
    [{"chunk_size": 250}, {"chunk_overlap": 10}, {"use_llm_chunking": True}],
    ids=["chunk_size", "chunk_overlap", "use_llm_chunking"],
)
def test_each_chunking_setting_changes_the_path(tmp_path, document, change):
    baseline = _make(tmp_path)._get_cache_path(document)
    assert _make(tmp_path, **change)._get_cache_path(document) != baseline


def test_embedder_does_not_change_the_path(tmp_path, document):
    a = _make(tmp_path, embedding_model="user.embedder-a")
    b = _make(tmp_path, embedding_model="user.embedder-b")
    assert a._get_cache_path(document) == b._get_cache_path(document)


def test_new_cache_names_are_ones_clear_cache_owns(tmp_path, document):
    name = os.path.basename(_make(tmp_path)._get_cache_path(document))
    assert _CACHE_OWNED_FILE.search(name)
    assert _CACHE_OWNED_FILE.search(name + ".sig")


def test_new_chunk_size_reindexes_instead_of_reusing_old_chunks(tmp_path, document):
    first = _make(tmp_path, chunk_size=500)
    assert first.index_document(document)["success"]

    resized = _make(tmp_path, chunk_size=50, chunk_overlap=10)
    stats = resized.index_document(document)
    assert stats["success"]
    assert not stats.get("from_cache"), "chunks cut to chunk_size=500 were reused"

    again = _make(tmp_path, chunk_size=50, chunk_overlap=10)
    assert again.index_document(document).get("from_cache")


def test_cache_holds_no_vectors_and_a_new_embedder_re_embeds(tmp_path, document):
    first = _make(tmp_path, embedding_model="user.embedder-a")
    assert first.index_document(document)["success"]
    with open(first._get_cache_path(document), encoding="utf-8") as f:
        assert set(json.load(f)) == {"chunks", "full_text", "metadata"}

    switched = _make(tmp_path, embedding_model="user.embedder-b")
    stats = switched.index_document(document)
    assert stats.get("from_cache")
    assert switched.encoded_with, "cache load reused vectors instead of re-embedding"
    assert set(switched.encoded_with) == {"user.embedder-b"}


def test_chunk_cache_and_markdown_share_one_content_key(tmp_path, document):
    sdk = _make(tmp_path)
    assert sdk.index_document(document)["success"]
    key = sdk._content_key(document)
    names = sorted(os.listdir(sdk.config.cache_dir))
    assert f"{key}_extracted.md" in names
    assert os.path.basename(sdk._get_cache_path(document)) in names
    assert all(name.startswith(key) for name in names), names


def test_an_edit_changes_the_content_key(tmp_path, document):
    sdk = _make(tmp_path)
    before = sdk._content_key(document)
    with open(document, "a", encoding="utf-8") as f:
        f.write("Moved to Wednesday.")
    assert sdk._content_key(document) != before


def test_unreadable_file_gets_a_name_clear_cache_owns(tmp_path):
    sdk = _make(tmp_path)
    missing = str(tmp_path / "gone.txt")
    key = sdk._content_key(missing)
    assert key.endswith("_notfound")
    name = os.path.basename(sdk._get_cache_path(missing))
    assert _CACHE_OWNED_FILE.search(name)
    assert _CACHE_OWNED_FILE.search(os.path.basename(sdk._extracted_markdown_path(key)))
