# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which limit a full index reached: the document count or its passages.

A few long documents fill the index on passages long before 100 files. Told
"100 documents", the model and the user would be wrong about why only nine
of ten attached books are searched.
"""

from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig


def _index(tmp_path, **config):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                use_llm_chunking=False,
                chunk_size=20,
                chunk_overlap=0,
                **config,
            )
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **_: np.ones((len(texts), 4), dtype="float32")
    sdk._encode_query = lambda _q: np.ones((1, 4), dtype="float32")
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


def _doc(tmp_path, name, passages):
    path = tmp_path / f"{name}.txt"
    path.write_text(
        "\n\n".join(
            f"{name} part {k} says the code is {name.upper()}-{k}, a long "
            "passage of words to fill the chunk."
            for k in range(passages)
        ),
        encoding="utf-8",
    )
    return str(path)


def test_an_index_with_room_is_not_full(tmp_path):
    sdk = _index(tmp_path, max_indexed_files=3, max_total_chunks=100)
    sdk.index_document(_doc(tmp_path, "a", 2))

    assert sdk.full_by() is None
    assert sdk.is_full() is False


def test_the_document_count_fills_it(tmp_path):
    sdk = _index(tmp_path, max_indexed_files=2, max_total_chunks=100)
    for name in ("a", "b"):
        sdk.index_document(_doc(tmp_path, name, 1))

    assert sdk.full_by() == "files"
    assert sdk.is_full() is True


def test_passages_reaching_the_limit_exactly_fill_it(tmp_path):
    sdk = _index(tmp_path, max_indexed_files=100, max_total_chunks=20)
    for name in ("a", "b"):
        assert sdk.index_document(_doc(tmp_path, name, 10)).get("evicted") is None

    assert sdk.full_by() == "size"


def test_a_long_document_that_pushes_one_out_leaves_it_full(tmp_path):
    sdk = _index(tmp_path, max_indexed_files=100, max_total_chunks=25)
    for name in ("a", "b"):
        sdk.index_document(_doc(tmp_path, name, 10))
    assert sdk.full_by() is None

    result = sdk.index_document(_doc(tmp_path, "c", 10))

    # Back under the limit after pushing "a" out, yet one more would push again.
    assert result["evicted"] == [str((tmp_path / "a.txt").absolute())]
    assert len(sdk.chunks) == 20
    assert sdk.full_by() == "size"


def test_long_documents_fill_it_below_the_document_count(tmp_path):
    sdk = _index(tmp_path, max_indexed_files=2, max_total_chunks=15)
    for name in ("a", "b", "c"):
        sdk.index_document(_doc(tmp_path, name, 10))

    assert len(sdk.indexed_files) == 1
    assert sdk.full_by() == "size"


def test_removing_a_document_makes_room_again(tmp_path):
    sdk = _index(tmp_path, max_indexed_files=100, max_total_chunks=25)
    paths = [_doc(tmp_path, name, 10) for name in ("a", "b", "c")]
    for path in paths:
        sdk.index_document(path)
    assert sdk.full_by() == "size"

    assert sdk.remove_document(paths[2]) is True

    assert sdk.full_by() is None


def test_one_document_longer_than_the_limit_fills_it(tmp_path):
    sdk = _index(tmp_path, max_indexed_files=100, max_total_chunks=5)
    result = sdk.index_document(_doc(tmp_path, "a", 10))

    # Nothing is pushed out to fit the only document.
    assert result.get("evicted") is None
    assert sdk.full_by() == "size"
