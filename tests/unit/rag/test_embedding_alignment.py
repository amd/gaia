# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Malformed embedding batches must never shift document/vector positions."""

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from gaia.rag.sdk import RAGSDK


@pytest.fixture
def sdk():
    instance = RAGSDK.__new__(RAGSDK)
    instance.config = SimpleNamespace(embedding_model="test-embedder", show_stats=False)
    instance.log = Mock()
    instance.embedder = Mock()
    return instance


@pytest.mark.parametrize("count", [0, 1, 24, 26])
def test_short_or_extra_batch_fails_without_individual_fallback(sdk, count):
    sdk.embedder.embeddings.return_value = {"data": [{"embedding": [1, 2]}] * count}
    with pytest.raises(RuntimeError, match=f"{count}/25 vectors"):
        sdk._encode_texts([f"chunk {i}" for i in range(25)])
    sdk.embedder.embeddings.assert_called_once()


@pytest.mark.parametrize("vectors", [[[], []], [[1, 2], [1]], [[1, 2], []]])
def test_empty_or_inconsistent_vectors_fail(sdk, vectors):
    sdk.embedder.embeddings.return_value = {"data": [{"embedding": v} for v in vectors]}
    with pytest.raises(RuntimeError, match="empty or inconsistent"):
        sdk._encode_texts(["first", "second"])


def test_multiple_batches_preserve_every_text_position(sdk):
    def embeddings(texts, **kwargs):
        return {"data": [{"embedding": [int(text), 1]} for text in texts]}

    sdk.embedder.embeddings.side_effect = embeddings
    encoded = sdk._encode_texts([str(i) for i in range(51)])
    np.testing.assert_array_equal(encoded[:, 0], np.arange(51))
    assert encoded.shape == (51, 2)
    assert encoded.dtype == np.float32


def test_later_batch_cannot_change_dimension(sdk):
    sdk.embedder.embeddings.side_effect = [
        {"data": [{"embedding": [1, 2]}] * 25},
        {"data": [{"embedding": [1]}]},
    ]
    with pytest.raises(RuntimeError, match="empty or inconsistent"):
        sdk._encode_texts(["chunk"] * 26)
