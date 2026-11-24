# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A query snapshot never sees its vector index being appended to."""

import hashlib
from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig

DIM = 8


def _vector(text: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "little")
    return np.random.default_rng(seed).random(DIM).astype("float32")


@pytest.fixture
def rag(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path / "cache"), allowed_paths=[str(tmp_path)])
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **kw: (
        np.stack([_vector(t) for t in texts]) if texts else np.zeros((0, DIM))
    )
    sdk._get_hmac_key = lambda: b"test-index-key" * 3
    return sdk


def _index(rag, tmp_path, name):
    doc = tmp_path / f"{name}.txt"
    doc.write_text(f"{name} talks about topic {name}.", encoding="utf-8")
    assert rag.index_document(str(doc))["success"]


def test_snapshot_taken_before_indexing_is_unchanged_after(rag, tmp_path):
    _index(rag, tmp_path, "first")
    snapshot = rag._snapshot_query_state()
    before = snapshot["index"].ntotal

    _index(rag, tmp_path, "second")

    assert snapshot["index"].ntotal == before
    assert rag.index is not snapshot["index"]
    assert rag.index.ntotal == len(rag.chunks) > before
