# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Opt-in reranking is explicit, bounded, and score-compatible."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

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


def _search_rag(client, chunks, distances, **config):
    rag = _rag(client, **config)
    rag._load_embedder = MagicMock()
    rag._encode_query = MagicMock(return_value=np.ones((1, 2), dtype="float32"))
    index = MagicMock()
    index.search.return_value = (
        np.array([distances], dtype="float32"),
        np.array([list(range(len(distances)))], dtype="int64"),
    )
    rag._snapshot_query_state = MagicMock(
        return_value={"chunks": chunks, "index": index}
    )
    return rag, index


def test_rerank_goes_only_to_the_configured_lemonade_base_url():
    client = LemonadeClient(base_url="http://lemonade.test:9123", api_key="k")
    with patch("gaia.llm.lemonade_client.requests.post") as post:
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"results": []}
        client.rerank("q", ["a"], "bge-reranker-v2-m3-GGUF", timeout=5)
    url = post.call_args.args[0]
    assert urlparse(url).netloc == "lemonade.test:9123"
    assert url == "http://lemonade.test:9123/api/v1/rerank"
    assert post.call_args.kwargs["json"] == {
        "model": "bge-reranker-v2-m3-GGUF",
        "query": "q",
        "documents": ["a"],
    }
    assert post.call_args.kwargs["timeout"] == 5


@pytest.mark.parametrize(
    "args",
    [("  ", ["a"], "m"), ("q", [], "m"), ("q", ["a"], " ")],
)
def test_client_rerank_rejects_empty_inputs_without_a_request(args):
    client = object.__new__(LemonadeClient)
    client.base_url = "http://localhost:13305/api/v1"
    client._send_request = MagicMock()
    with pytest.raises(ValueError):
        client.rerank(*args)
    client._send_request.assert_not_called()


def test_disabled_reranking_never_touches_lemonade_and_keeps_vector_order():
    client = MagicMock()
    rag, index = _search_rag(
        client, ["first", "second", "third"], [0.0, 1.0, 2.0], enable_reranking=False
    )
    result = rag._search_chunks("needle")
    assert index.search.call_args.args[1] == 2
    assert result["retrieved_chunks"] == ["first", "second"]
    assert result["scores"] == [1.0, 0.5]
    client.list_models.assert_not_called()
    client.rerank.assert_not_called()


def test_single_candidate_keeps_its_vector_score_without_a_rerank_call():
    client = MagicMock()
    client.list_models.return_value = {
        "data": [{"id": "r", "downloaded": True, "labels": ["reranking"]}]
    }
    rag, _ = _search_rag(client, ["only"], [1.0])
    result = rag._search_chunks("needle")
    assert result["retrieved_chunks"] == ["only"]
    assert result["scores"] == [0.5]
    client.rerank.assert_not_called()


def test_no_reranking_labelled_model_is_actionable():
    client = MagicMock()
    client.list_models.return_value = {
        "data": [
            {"id": "embed", "downloaded": True, "labels": ["embeddings"]},
            {"id": "chat", "downloaded": True, "labels": ["chat", "reranker-ish"]},
        ]
    }
    with pytest.raises(RuntimeError, match="no downloaded model has the reranking"):
        _rag(client)._resolve_reranking_model()


def test_requested_reranker_that_is_not_downloaded_is_refused():
    client = MagicMock()
    client.list_models.return_value = {
        "data": [{"id": "r", "downloaded": False, "labels": ["reranking"]}]
    }
    with pytest.raises(RuntimeError, match="not downloaded"):
        _rag(client, reranking_model="r")._resolve_reranking_model()


def test_unreachable_lemonade_during_selection_is_raised_with_context():
    client = MagicMock()
    client.list_models.side_effect = ConnectionError("refused")
    with pytest.raises(RuntimeError, match="Lemonade") as info:
        _rag(client)._resolve_reranking_model()
    assert isinstance(info.value.__cause__, ConnectionError)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": "nope"},
        {"results": [{"index": 0, "relevance_score": 1.0}]},
        {"results": [{"index": 0, "relevance_score": 1.0}] * 2},
        {
            "results": [
                {"index": 0, "relevance_score": 1},
                {"index": 2, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": -1, "relevance_score": 1},
                {"index": 0, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": True, "relevance_score": 1},
                {"index": 0, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": 1, "relevance_score": "1"},
                {"index": 0, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": 1, "relevance_score": float("nan")},
                {"index": 0, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": 1, "relevance_score": float("inf")},
                {"index": 0, "relevance_score": 1},
            ]
        },
        {"results": [None, {"index": 0, "relevance_score": 1}]},
    ],
)
def test_malformed_rerank_responses_raise_instead_of_reordering(payload):
    client = MagicMock()
    client.rerank.return_value = payload
    with pytest.raises(RuntimeError, match="Lemonade rerank failed"):
        _rag(client)._rerank_candidates("needle", ["a", "b"], "r")


def test_large_negative_and_positive_scores_stay_in_retrieval_range():
    client = MagicMock()
    client.rerank.return_value = {
        "results": [
            {"index": 0, "relevance_score": 700.0},
            {"index": 1, "relevance_score": -1000.0},
        ]
    }
    _, scores = _rag(client)._rerank_candidates("needle", ["a", "b"], "r")
    assert all(0.0 <= s <= 1.0 for s in scores)
