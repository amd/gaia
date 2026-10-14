# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An embedder that cannot run is an error, not "no relevant information" (#5111).

With Lemonade down, every embedding search raised, the tool swallowed each one
and answered as if the documents held nothing on the topic.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.llm.lemonade_client import LemonadeClientError
from gaia.rag.sdk import RAGSDK, EmbeddingUnavailableError, RAGConfig

MODEL = "nomic-embed-text-v2-moe-GGUF"


def _lemonade_down() -> LemonadeClientError:
    """The exception chain LemonadeClient.embeddings raises with no server."""
    try:
        try:
            raise requests.exceptions.ConnectionError(
                "HTTPConnectionPool(host='localhost', port=13305): Max retries "
                "exceeded (Caused by NewConnectionError('Failed to establish a "
                "new connection: [WinError 10061]'))"
            )
        except requests.exceptions.RequestException as e:
            raise LemonadeClientError(f"Request failed: {e}")
    except LemonadeClientError as e:
        try:
            raise LemonadeClientError(f"Error generating embeddings: {e}")
        except LemonadeClientError as outer:
            return outer


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def _start_hint(monkeypatch):
    hint = MagicMock()
    hint.instruction = "Start Lemonade Server from the tray icon."
    monkeypatch.setattr(
        "gaia.llm.lemonade_launcher.describe_start_hint", lambda *a, **k: hint
    )


class _RagHost(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query):
        return [query, f"{query} details"]


def _rag(chunks, retrieve_error=None):
    rag = MagicMock()
    rag.index = object()
    rag.chunks = chunks
    rag.chunk_to_file = {i: "/docs/handbook.md" for i in range(len(chunks))}
    rag.indexed_files = {"/docs/handbook.md"}
    rag.file_to_chunk_indices = {"/docs/handbook.md": list(range(len(chunks)))}
    rag.file_metadata = {"/docs/handbook.md": {"full_text": "\n".join(chunks)}}
    if retrieve_error is not None:
        rag._retrieve_chunks.side_effect = retrieve_error
        rag._retrieve_chunks_from_file.side_effect = retrieve_error
    else:
        rag._retrieve_chunks.return_value = ([], [])
        rag._retrieve_chunks_from_file.return_value = ([], [])
    return rag


def _tool(name, **kwargs):
    return _TOOL_REGISTRY[name]["function"](**kwargs)


class TestClassification:
    def test_unreachable_server_is_named(self):
        error = EmbeddingUnavailableError(MODEL, _lemonade_down())
        assert error.unreachable
        assert str(error).startswith("Lemonade Server is not reachable")
        assert MODEL in str(error)

    def test_server_error_is_not_called_unreachable(self):
        error = EmbeddingUnavailableError(
            MODEL, LemonadeClientError("Error generating embeddings: 500 model crashed")
        )
        assert not error.unreachable
        assert str(error).startswith(f"The embedding model {MODEL} failed")


class TestSdk:
    @pytest.fixture
    def sdk(self, tmp_path):
        pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
        with patch("gaia.rag.sdk.AgentSDK"):
            rag = RAGSDK(
                RAGConfig(
                    cache_dir=str(tmp_path / "cache"), allowed_paths=[str(tmp_path)]
                )
            )
        rag._get_hmac_key = lambda: b"test-index-key" * 3
        return rag

    def test_encode_failure_raises_embedding_unavailable(self, sdk, monkeypatch):
        monkeypatch.setattr("gaia.rag.sdk.time.sleep", lambda _s: None)
        sdk.embedder = MagicMock()
        sdk.embedder.embeddings.side_effect = _lemonade_down()
        with pytest.raises(EmbeddingUnavailableError) as exc:
            sdk._encode_texts(["hello"])
        assert exc.value.unreachable

    def test_embedder_load_failure_raises_embedding_unavailable(self, sdk):
        sdk.llm_client = MagicMock()
        sdk.llm_client.load_model.side_effect = _lemonade_down()
        with patch("gaia.daemon.broker_client.model_lease"):
            with pytest.raises(EmbeddingUnavailableError) as exc:
                sdk._load_embedder()
        assert exc.value.unreachable

    def test_index_document_reports_the_embedding_cause(self, sdk, tmp_path):
        doc = tmp_path / "doc.txt"
        doc.write_text("some content to index", encoding="utf-8")
        sdk._load_embedder = lambda: None

        def _down(texts, **kwargs):
            raise EmbeddingUnavailableError(MODEL, _lemonade_down())

        sdk._encode_texts = _down
        stats = sdk.index_document(str(doc))
        assert stats["success"] is False
        assert stats["error_type"] == "embedding_unavailable"
        assert stats["lemonade_unreachable"] is True
        assert "not reachable" in stats["error"]

    def test_search_still_works_when_embedding_succeeds(self, sdk, tmp_path):
        doc = tmp_path / "doc.txt"
        doc.write_text("some content to index", encoding="utf-8")
        sdk._load_embedder = lambda: None
        sdk._encode_texts = lambda texts, **kw: np.ones((len(texts), 4), "float32")
        assert sdk.index_document(str(doc))["success"]
        chunks, _ = sdk._retrieve_chunks("content")
        assert chunks


class TestQueryTools:
    def test_query_documents_outage_is_an_error(self):
        down = EmbeddingUnavailableError(MODEL, _lemonade_down())
        # A keyword hit must not pass a search that never ran off as a result.
        _RagHost(_rag(["the vacation policy is 20 days"], down)).register_rag_tools()
        result = _tool("query_documents", query="vacation policy")
        assert result["status"] == "error", result
        assert result["error_type"] == "embedding_unavailable"
        assert "not reachable" in result["error"]
        assert "tray icon" in result["next_step"]
        assert "No relevant information" not in str(result)

    def test_query_documents_genuine_empty_result_is_a_success(self):
        _RagHost(_rag(["the vacation policy is 20 days"])).register_rag_tools()
        result = _tool("query_documents", query="quarterly revenue")
        assert result["status"] == "success"
        assert result["num_chunks"] == 0
        assert "No relevant information" in result["message"]

    def test_query_documents_unexpected_error_is_not_a_fallback(self):
        class _Broken(_RagHost):
            def _generate_search_keys(self, query):
                raise RuntimeError("search-key model crashed")

        _Broken(_rag(["x"])).register_rag_tools()
        result = _tool("query_documents", query="anything")
        assert result["status"] == "error"
        assert "search-key model crashed" in result["error"]
        assert "general knowledge" not in str(result)

    def test_query_specific_file_outage_is_an_error(self):
        down = EmbeddingUnavailableError(
            MODEL, LemonadeClientError("Error generating embeddings: 500")
        )
        _RagHost(_rag(["the vacation policy is 20 days"], down)).register_rag_tools()
        result = _tool(
            "query_specific_file", file_path="/docs/handbook.md", query="vacation"
        )
        assert result["status"] == "error", result
        assert result["error_type"] == "embedding_unavailable"
        assert "gaia init --profile rag" in result["next_step"]

    def test_one_failed_search_key_is_not_an_outage(self):
        rag = _rag(["the vacation policy is 20 days"])
        rag._retrieve_chunks.side_effect = [
            EmbeddingUnavailableError(MODEL, LemonadeClientError("blip")),
            (["the vacation policy is 20 days"], [0.9]),
        ]
        _RagHost(rag).register_rag_tools()
        result = _tool("query_documents", query="vacation")
        assert result["status"] == "success"
        assert result["num_chunks"] == 1


class TestIndexTools:
    STATS = {
        "success": False,
        "file_name": "doc.txt",
        "error": f"Lemonade Server is not reachable, so the embedding model {MODEL} could not run",
        "error_type": "embedding_unavailable",
        "lemonade_unreachable": True,
    }

    def test_index_document_outage_names_the_cause(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_tools, "INDEX_FOREGROUND_BUDGET_S", 5, raising=False)
        doc = tmp_path / "doc.txt"
        doc.write_text("hello", encoding="utf-8")
        rag = MagicMock()
        rag.index_document.return_value = dict(self.STATS)
        host = _RagHost(rag)
        host.register_rag_tools()
        result = _tool("index_document", file_path=str(doc))
        assert result["status"] == "error"
        assert result["error_type"] == "embedding_unavailable"
        assert "tray icon" in result["next_step"]
        assert str(doc) not in host.indexed_files

    def test_index_directory_stops_at_an_outage(self, tmp_path):
        for name in ("a.txt", "b.txt", "c.txt"):
            (tmp_path / name).write_text("hello", encoding="utf-8")
        rag = MagicMock()
        rag.index_document.return_value = dict(self.STATS)
        _RagHost(rag).register_rag_tools()
        result = _tool("index_directory", directory_path=str(tmp_path))
        assert result["status"] == "error"
        assert result["error_type"] == "embedding_unavailable"
        assert rag.index_document.call_count == 1

    def test_index_directory_with_every_file_failing_is_an_error(self, tmp_path):
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("hello", encoding="utf-8")
        rag = MagicMock()
        rag.index_document.return_value = {"success": False, "error": "bad file"}
        _RagHost(rag).register_rag_tools()
        result = _tool("index_directory", directory_path=str(tmp_path))
        assert result["status"] == "error"
        assert result["failed_count"] == 2
        assert set(result["failure_reasons"].values()) == {"bad file"}

    def test_index_directory_partial_failure_is_a_success_with_reasons(self, tmp_path):
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("hello", encoding="utf-8")
        rag = MagicMock()
        rag.index_document.side_effect = [
            {"success": True},
            {"success": False, "error": "bad file"},
        ]
        _RagHost(rag).register_rag_tools()
        result = _tool("index_directory", directory_path=str(tmp_path))
        assert result["status"] == "success"
        assert result["indexed_count"] == 1
        assert result["failed_count"] == 1
        assert "1 failed" in result["message"]


class TestOutageAdversarial:
    """An outage, an empty result and a partial failure stay distinguishable."""

    @pytest.mark.parametrize(
        "errors",
        [
            # Every key fails, for different reasons.
            [
                EmbeddingUnavailableError(MODEL, _lemonade_down()),
                EmbeddingUnavailableError(MODEL, LemonadeClientError("500")),
            ],
            # Every key fails the same way.
            [EmbeddingUnavailableError(MODEL, LemonadeClientError("timeout"))] * 2,
        ],
    )
    def test_every_key_failing_is_an_outage_whatever_the_mix(self, errors):
        rag = _rag(["the vacation policy is 20 days"])
        rag._retrieve_chunks.side_effect = errors
        _RagHost(rag).register_rag_tools()

        result = _tool("query_documents", query="vacation policy")

        assert result["status"] == "error", result
        assert result["error_type"] == "embedding_unavailable"
        assert "No relevant information" not in str(result)

    def test_a_key_that_worked_but_found_nothing_beside_a_failed_one(self):
        """One search ran and found nothing: an empty answer, never an outage."""
        rag = _rag(["the vacation policy is 20 days"])
        rag._retrieve_chunks.side_effect = [
            ([], []),
            EmbeddingUnavailableError(MODEL, LemonadeClientError("blip")),
        ]
        _RagHost(rag).register_rag_tools()

        result = _tool("query_documents", query="quarterly revenue")

        assert result["status"] == "success", result
        assert result["num_chunks"] == 0

    def test_the_last_key_failing_after_a_hit_keeps_the_hit(self):
        rag = _rag(["the vacation policy is 20 days"])
        rag._retrieve_chunks.side_effect = [
            (["the vacation policy is 20 days"], [0.9]),
            EmbeddingUnavailableError(MODEL, _lemonade_down()),
        ]
        _RagHost(rag).register_rag_tools()

        result = _tool("query_documents", query="vacation")

        assert result["status"] == "success"
        assert result["num_chunks"] == 1

    def test_a_non_embedding_failure_on_every_key_is_an_error_not_empty(self):
        rag = _rag(["the vacation policy is 20 days"])
        rag._retrieve_chunks.side_effect = RuntimeError("faiss index corrupt")
        _RagHost(rag).register_rag_tools()

        result = _tool("query_documents", query="vacation")

        assert result["status"] == "error", result
        assert "No relevant information" not in str(result)
        assert "general knowledge" not in str(result)

    def test_a_keyword_only_hit_is_not_returned_when_no_search_ran(self):
        down = EmbeddingUnavailableError(MODEL, _lemonade_down())
        chunks = ["quarterly revenue was $4M", "quarterly revenue table"]
        _RagHost(_rag(chunks, down)).register_rag_tools()

        result = _tool("query_documents", query="quarterly revenue")

        assert result["status"] == "error"
        assert "quarterly revenue was $4M" not in str(result)

    def test_a_server_error_is_not_mislabelled_unreachable(self):
        cause = LemonadeClientError("Error generating embeddings: 503 overloaded")
        error = EmbeddingUnavailableError(MODEL, cause)

        assert not error.unreachable
        assert "not reachable" not in str(error)

    def test_a_deeply_chained_connection_failure_is_unreachable(self):
        try:
            try:
                raise _lemonade_down()
            except LemonadeClientError as inner:
                raise RuntimeError("embed batch 3 of 9 failed") from inner
        except RuntimeError as outer:
            error = EmbeddingUnavailableError(MODEL, outer)

        assert error.unreachable

    def test_a_file_search_whose_every_key_failed_is_an_error_not_a_hit(self):
        rag = _rag(["the vacation policy is 20 days"])
        rag._retrieve_chunks_from_file.side_effect = RuntimeError("faiss index corrupt")
        _RagHost(rag).register_rag_tools()

        result = _tool(
            "query_specific_file", file_path="/docs/handbook.md", query="vacation"
        )

        assert result["status"] == "error", result
        assert "faiss index corrupt" in result["error"]

    def test_one_key_failing_for_another_reason_is_still_a_search(self):
        rag = _rag(["the vacation policy is 20 days"])
        rag._retrieve_chunks.side_effect = [
            RuntimeError("one bad key"),
            (["the vacation policy is 20 days"], [0.9]),
        ]
        _RagHost(rag).register_rag_tools()

        result = _tool("query_documents", query="vacation")

        assert result["status"] == "success"
        assert result["num_chunks"] == 1
