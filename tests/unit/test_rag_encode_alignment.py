# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A short embedding batch must fail loudly, not pair chunks with wrong vectors."""

from unittest.mock import MagicMock

import pytest

from gaia.rag.sdk import RAGSDK


def _sdk(embeddings):
    sdk = RAGSDK.__new__(RAGSDK)  # skip heavy __init__
    sdk.config = MagicMock()
    sdk.config.embedding_model = "nomic-embed-text-v2-moe-GGUF"
    sdk.config.show_stats = False
    sdk.log = MagicMock()
    sdk.embedder = MagicMock()
    sdk.embedder.embeddings.side_effect = embeddings
    return sdk


def _vectors(texts):
    return {"data": [{"embedding": [float(len(t)), 1.0]} for t in texts]}


def test_full_batch_stays_aligned():
    out = _sdk(lambda texts, **kw: _vectors(texts))._encode_texts(["a", "bb", "ccc"])
    assert out.shape == (3, 2)
    assert list(out[:, 0]) == [1.0, 2.0, 3.0]


def test_short_batch_raises():
    sdk = _sdk(lambda texts, **kw: _vectors(texts[:-1]))
    with pytest.raises(RuntimeError, match="2/3 usable"):
        sdk._encode_texts(["a", "bb", "ccc"])


def test_hollow_vector_raises():
    def embeddings(texts, **kw):
        data = _vectors(texts)
        data["data"][1]["embedding"] = []
        return data

    with pytest.raises(RuntimeError, match="2/3 usable"):
        _sdk(embeddings)._encode_texts(["a", "bb", "ccc"])


def test_one_by_one_skip_raises_instead_of_shifting_chunks():
    def embeddings(texts, **kw):
        if len(texts) > 1:
            return {"data": []}  # whole batch came back empty
        return {"data": []} if texts == ["bb"] else _vectors(texts)

    with pytest.raises(RuntimeError, match="2/3 usable"):
        _sdk(embeddings)._encode_texts(["a", "bb", "ccc"])
