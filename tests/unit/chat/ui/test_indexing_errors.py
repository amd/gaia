# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for _index_document error propagation (Issue #590).

Verifies that indexing failures raise RuntimeError instead of silently
returning 0 chunks, and that callers set indexing_status='failed'.
"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ── _index_document unit tests ──────────────────────────────────────────────


@pytest.fixture
def mock_rag_module():
    """Create a mock RAGSDK module for patching."""
    mock_sdk = MagicMock()
    mock_config = MagicMock()
    return mock_sdk, mock_config


class TestIndexDocumentErrorPropagation:
    """Tests that _index_document raises on failure instead of returning 0."""

    @pytest.mark.asyncio
    async def test_returns_chunks_on_success(self):
        """Successful indexing returns the chunk count."""
        from gaia.ui._chat_helpers import _index_document

        mock_rag = MagicMock()
        mock_rag.index_document.return_value = {
            "success": True,
            "num_chunks": 5,
        }

        with (
            patch("gaia.rag.sdk.RAGSDK", return_value=mock_rag),
            patch("gaia.rag.sdk.RAGConfig"),
        ):
            result = await _index_document(Path("/tmp/test.txt"))

        assert result == 5

    @pytest.mark.asyncio
    async def test_raises_on_rag_error(self):
        """RAG returning an error dict must raise RuntimeError."""
        from gaia.ui._chat_helpers import _index_document

        mock_rag = MagicMock()
        mock_rag.index_document.return_value = {
            "success": False,
            "num_chunks": 0,
            "error": "embedder not loaded",
        }

        with (
            patch("gaia.rag.sdk.RAGSDK", return_value=mock_rag),
            patch("gaia.rag.sdk.RAGConfig"),
        ):
            with pytest.raises(RuntimeError, match="embedder not loaded"):
                await _index_document(Path("/tmp/test.txt"))

    @pytest.mark.asyncio
    async def test_raises_on_success_false(self):
        """RAG returning success=False without error must raise RuntimeError."""
        from gaia.ui._chat_helpers import _index_document

        mock_rag = MagicMock()
        mock_rag.index_document.return_value = {
            "success": False,
            "num_chunks": 0,
        }

        with (
            patch("gaia.rag.sdk.RAGSDK", return_value=mock_rag),
            patch("gaia.rag.sdk.RAGConfig"),
        ):
            with pytest.raises(RuntimeError, match="unsuccessful"):
                await _index_document(Path("/tmp/test.txt"))

    @pytest.mark.asyncio
    async def test_raises_on_non_dict_result(self):
        """RAG returning a non-dict must raise RuntimeError."""
        from gaia.ui._chat_helpers import _index_document

        mock_rag = MagicMock()
        mock_rag.index_document.return_value = None

        with (
            patch("gaia.rag.sdk.RAGSDK", return_value=mock_rag),
            patch("gaia.rag.sdk.RAGConfig"),
        ):
            with pytest.raises(RuntimeError, match="unexpected type"):
                await _index_document(Path("/tmp/test.txt"))

    @pytest.mark.asyncio
    async def test_raises_on_executor_exception(self):
        """Exceptions from RAG SDK must propagate as RuntimeError."""
        from gaia.ui._chat_helpers import _index_document

        mock_rag = MagicMock()
        mock_rag.index_document.side_effect = ConnectionError("server down")

        with (
            patch("gaia.rag.sdk.RAGSDK", return_value=mock_rag),
            patch("gaia.rag.sdk.RAGConfig"),
        ):
            with pytest.raises(RuntimeError, match="server down"):
                await _index_document(Path("/tmp/test.txt"))


# ── Upload endpoint failure handling tests ───────────────────────────────────


@pytest.fixture
def home_tmp_dir():
    """Create a temporary directory under $HOME for tests.

    Must be under $HOME because safe_open_document rejects paths
    outside the user's home directory.
    """
    d = Path.home() / ".gaia_test_indexing"
    d.mkdir(exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestUploadIndexingFailure:
    """Tests that upload endpoint sets indexing_status='failed' on errors."""

    @pytest.fixture
    def app(self):
        from gaia.ui.server import create_app

        return create_app(db_path=":memory:")

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    @pytest.fixture
    def db(self, app):
        return app.state.db

    def test_small_file_failure_sets_status_failed(self, home_tmp_dir, client, db):
        """When _index_document raises, the document should be stored with
        indexing_status='failed' and chunk_count=0."""
        doc_file = home_tmp_dir / "test.txt"
        doc_file.write_text("hello world")

        async def mock_index_fail(path):
            raise RuntimeError("embedder not loaded")

        with patch("gaia.ui.server._index_document", mock_index_fail):
            resp = client.post(
                "/api/documents/upload-path",
                json={"filepath": str(doc_file)},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["chunk_count"] == 0
        assert data["indexing_status"] == "failed"

    def test_small_file_success_has_chunks(self, home_tmp_dir, client):
        """Successful indexing returns chunk_count > 0."""
        doc_file = home_tmp_dir / "test.txt"
        doc_file.write_text("hello world")

        async def mock_index_ok(path):
            return 5

        with patch("gaia.ui.server._index_document", mock_index_ok):
            resp = client.post(
                "/api/documents/upload-path",
                json={"filepath": str(doc_file)},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["chunk_count"] == 5

    def test_large_file_zero_chunks_sets_failed(self, home_tmp_dir, app, db):
        """Background indexing returning 0 chunks should set status='failed'."""
        doc_file = home_tmp_dir / "large.txt"
        # Create a file larger than LARGE_FILE_THRESHOLD (5MB)
        doc_file.write_bytes(b"x" * (6 * 1024 * 1024))

        async def mock_index_zero(path):
            return 0

        error_client = TestClient(app, raise_server_exceptions=False)
        with patch("gaia.ui.server._index_document", mock_index_zero):
            resp = error_client.post(
                "/api/documents/upload-path",
                json={"filepath": str(doc_file)},
            )

        assert resp.status_code == 200
        data = resp.json()
        doc_id = data["id"]

        # Poll DB until background task updates the status
        import time

        for _ in range(20):
            doc = db.get_document(doc_id)
            if doc and doc["indexing_status"] != "indexing":
                break
            time.sleep(0.1)

        # Background task should have set status to "failed"
        doc = db.get_document(doc_id)
        assert doc is not None
        assert doc["indexing_status"] == "failed"
        assert doc["chunk_count"] == 0
