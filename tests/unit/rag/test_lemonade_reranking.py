# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Opt-in reranking is explicit, bounded, and score-compatible."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from gaia.llm.lemonade_client import LemonadeClient
from gaia.rag.sdk import RAGSDK


def _rag(client, **config):
    rag = object.__new__(RAGSDK)
    settings = {
        "enable_reranking": True,
        "reranking_model": None,
        "reranking_candidate_multiplier": 3,
        "reranking_timeout": 30,
        "base_url": None,
        "show_stats": False,
        "max_chunks": 2,
    }
    settings.update(config)
    rag.config = SimpleNamespace(**settings)
    rag.llm_client = client
    rag.log = MagicMock()
    rag._reranking_model = None
    rag._reranking_checked = False
    return rag


def test_client_rerank_posts_the_current_lemonade_request_shape():
    client = object.__new__(LemonadeClient)
    client.base_url = "http://localhost:13305/v1"
    client._send_request = MagicMock(return_value={"results": []})
    assert client.rerank("capital of France", ["Paris", "Berlin"], "jina-reranker") == {
        "results": []
    }
    client._send_request.assert_called_once_with(
        "POST",
        "http://localhost:13305/v1/rerank",
        data={
            "model": "jina-reranker",
            "query": "capital of France",
            "documents": ["Paris", "Berlin"],
        },
        timeout=900,
    )


def test_reranking_selects_only_a_downloaded_reranker():
    client = MagicMock()
    client.list_models.return_value = {
        "data": [
            {"id": "not-downloaded", "downloaded": False, "labels": ["reranking"]},
            {"id": "chat", "downloaded": True, "labels": ["chat"]},
            {"id": "jina-reranker", "downloaded": True, "labels": ["reranking"]},
        ]
    }
    rag = _rag(client)
    assert rag._resolve_reranking_model() == "jina-reranker"
    assert rag._resolve_reranking_model() == "jina-reranker"
    client.list_models.assert_called_once_with(show_all=False)


def test_missing_requested_reranker_is_actionable():
    client = MagicMock()
    client.list_models.return_value = {"data": []}
    rag = _rag(client, reranking_model="bge-reranker-v2-m3-GGUF")
    with pytest.raises(RuntimeError, match="not downloaded"):
        rag._resolve_reranking_model()
    client.rerank.assert_not_called()


def test_search_reranks_three_times_the_final_result_limit():
    rag = _rag(MagicMock())
    rag._load_embedder = MagicMock()
    rag._resolve_reranking_model = MagicMock(return_value="jina-reranker")
    rag._encode_query = MagicMock(return_value=np.ones((1, 2), dtype="float32"))
    index = MagicMock()
    index.search.return_value = (
        np.array([[0.0, 1.0, 2.0, 3.0]], dtype="float32"),
        np.array([[0, 1, 2, 3]], dtype="int64"),
    )
    rag._snapshot_query_state = MagicMock(
        return_value={
            "chunks": ["first", "second", "third", "fourth"],
            "index": index,
            "chunk_to_file": {},
        }
    )
    rag._rerank_candidates = MagicMock(
        return_value=([2, 0, 3, 1], [0.9, 0.8, 0.7, 0.6])
    )
    result = rag._search_chunks("needle")
    assert index.search.call_args.args[1] == 4
    assert result["retrieved_chunks"] == ["third", "first"]
    assert result["scores"] == [0.9, 0.8]


def test_reranking_normalizes_scores_to_retrieval_range():
    client = MagicMock()
    client.rerank.return_value = {
        "results": [
            {"index": 1, "relevance_score": 10.0},
            {"index": 0, "relevance_score": 0.0},
        ]
    }
    order, scores = _rag(client)._rerank_candidates(
        "needle", ["a", "b"], "jina-reranker"
    )
    assert order == [1, 0]
    assert scores[0] > 0.99
    assert scores[1] == pytest.approx(0.5)
    client.rerank.assert_called_once_with(
        "needle", ["a", "b"], model="jina-reranker", timeout=30
    )


def test_rerank_failure_is_visible_and_does_not_disable_future_attempts():
    client = MagicMock()
    client.rerank.side_effect = RuntimeError("model is unavailable")
    rag = _rag(client)
    with pytest.raises(RuntimeError, match="Lemonade rerank failed"):
        rag._rerank_candidates("needle", ["a", "b"], "jina-reranker")
    with pytest.raises(RuntimeError, match="Lemonade rerank failed"):
        rag._rerank_candidates("needle", ["a", "b"], "jina-reranker")
    assert client.rerank.call_count == 2


def test_search_surfaces_rerank_failure_instead_of_using_vector_results():
    rag = _rag(MagicMock())
    rag._load_embedder = MagicMock()
    rag._resolve_reranking_model = MagicMock(return_value="jina-reranker")
    rag._encode_query = MagicMock(return_value=np.ones((1, 2), dtype="float32"))
    index = MagicMock()
    index.search.return_value = (
        np.array([[0.0, 1.0, 2.0]], dtype="float32"),
        np.array([[0, 1, 2]], dtype="int64"),
    )
    rag._snapshot_query_state = MagicMock(
        return_value={"chunks": ["first", "second", "third"], "index": index}
    )
    rag._rerank_candidates = MagicMock(side_effect=RuntimeError("rerank failed"))
    with pytest.raises(RuntimeError, match="rerank failed"):
        rag._search_chunks("needle")
