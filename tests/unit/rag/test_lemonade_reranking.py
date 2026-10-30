# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Reranking stays optional and never changes retrieval on a failed request."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from gaia.llm.lemonade_client import LemonadeClient
from gaia.rag.sdk import RAGSDK


def _rag(client, **config):
    rag = object.__new__(RAGSDK)
    settings = {
        "enable_reranking": True,
        "reranking_model": None,
        "reranking_candidate_multiplier": 3,
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
    rag._reranking_unavailable_logged = False
    rag._reranking_failure_logged = False
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


def test_search_reranks_three_times_the_final_result_limit():
    client = MagicMock()
    rag = _rag(client)
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
        return_value=([2, 0, 3, 1], [9.0, 8.0, 7.0, 6.0])
    )

    result = rag._search_chunks("needle")

    assert index.search.call_args.args[1] == 4
    rag._rerank_candidates.assert_called_once_with(
        "needle", ["first", "second", "third", "fourth"], "jina-reranker"
    )
    assert result["retrieved_chunks"] == ["third", "first"]
    assert result["scores"] == [9.0, 8.0]


def test_search_keeps_vector_order_when_reranking_fails():
    client = MagicMock()
    rag = _rag(client)
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
    rag._rerank_candidates = MagicMock(return_value=None)

    result = rag._search_chunks("needle")

    assert result["retrieved_chunks"] == ["first", "second"]
    assert result["scores"] == [1.0, 0.5]


def test_rerank_failure_keeps_vector_result_available():
    client = MagicMock()
    client.rerank.side_effect = RuntimeError("model is unavailable")
    rag = _rag(client)

    assert rag._rerank_candidates("needle", ["a", "b"], "jina-reranker") is None
    assert rag._rerank_candidates("needle", ["a", "b"], "jina-reranker") is None
    assert rag.log.warning.call_count == 1


def test_missing_requested_reranker_is_not_downloaded_or_loaded():
    client = MagicMock()
    client.list_models.return_value = {"data": []}
    rag = _rag(client, reranking_model="bge-reranker-v2-m3-GGUF")

    assert rag._resolve_reranking_model() is None
    client.rerank.assert_not_called()
    client.load_model.assert_not_called()
    client.ensure_model_downloaded.assert_not_called()
