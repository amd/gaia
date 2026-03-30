# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for GAIA Agent UI folder indexing and document lifecycle.

Tests the following endpoints through the HTTP API layer:
- POST /api/documents/index-folder  -- bulk index all files in a folder
- GET  /api/documents/monitor/status -- document file monitor status
- GET  /api/documents/{doc_id}/status -- per-document indexing status
- POST /api/documents/{doc_id}/cancel -- cancel background indexing
- Full document lifecycle (upload -> list -> attach -> detach -> delete)

LLM/RAG calls are mocked via ``gaia.ui.server._index_document`` so
the tests run without a Lemonade server.  Temporary files are created
inside ``Path.home() / ".gaia" / "test_temp"`` to satisfy the
``ensure_within_home`` security check.
"""

import logging
import platform
import shutil
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

logger = logging.getLogger(__name__)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Create FastAPI app with in-memory database."""
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    """Create test client for the app."""
    return TestClient(app)


@pytest.fixture
def db(app):
    """Access the database from app state."""
    return app.state.db


@pytest.fixture
def session_id(client):
    """Create a session and return its ID."""
    resp = client.post("/api/sessions", json={"title": "Folder Indexing Test"})
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.fixture
def temp_folder():
    """Create a temporary folder inside the user's home directory.

    The folder is placed under ``~/.gaia/test_temp`` so it passes the
    ``ensure_within_home`` security check enforced by the index-folder
    endpoint.  Cleaned up after each test.
    """
    base = Path.home() / ".gaia" / "test_temp"
    base.mkdir(parents=True, exist_ok=True)
    yield base
    shutil.rmtree(str(base), ignore_errors=True)


def _create_file(folder: Path, name: str, content: str = "test content") -> Path:
    """Helper: create a file with the given name and content."""
    filepath = folder / name
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    return filepath


# ── TestFolderIndexing ──────────────────────────────────────────────────────


class TestFolderIndexing:
    """Tests for POST /api/documents/index-folder."""

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_index_folder_with_mixed_files(self, mock_index, client, temp_folder):
        """Create a temp folder with .txt, .md, .csv files -- all should be indexed."""
        _create_file(temp_folder, "readme.txt", "Hello world")
        _create_file(temp_folder, "notes.md", "# Notes\nSome notes")
        _create_file(temp_folder, "data.csv", "a,b,c\n1,2,3")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexed"] == 3
        assert body["failed"] == 0
        assert len(body["documents"]) == 3
        assert body["errors"] == []

        # Each document should report chunk_count=5 from mock
        for doc in body["documents"]:
            assert doc["chunk_count"] == 5
            assert doc["indexing_status"] == "complete"

        # _index_document should have been called once per file
        assert mock_index.call_count == 3

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=3)
    def test_index_folder_recursive(self, mock_index, client, temp_folder):
        """Files in nested subdirectories should be indexed when recursive=True."""
        _create_file(temp_folder, "top.txt", "top level")
        _create_file(temp_folder, "sub1/nested.md", "nested file")
        _create_file(temp_folder, "sub1/sub2/deep.py", "print('deep')")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": True},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexed"] == 3
        assert body["failed"] == 0
        assert len(body["documents"]) == 3

        filenames = sorted(d["filename"] for d in body["documents"])
        assert filenames == ["deep.py", "nested.md", "top.txt"]

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=3)
    def test_index_folder_non_recursive(self, mock_index, client, temp_folder):
        """With recursive=False only top-level files should be indexed."""
        _create_file(temp_folder, "top.txt", "top level")
        _create_file(temp_folder, "sub/nested.md", "nested file")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexed"] == 1
        assert body["failed"] == 0
        assert len(body["documents"]) == 1
        assert body["documents"][0]["filename"] == "top.txt"

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_index_folder_skips_unsupported_extensions(
        self, mock_index, client, temp_folder
    ):
        """Files with unsupported extensions (.exe, .dll, .mp3) should be skipped."""
        _create_file(temp_folder, "good.txt", "valid")
        _create_file(temp_folder, "program.exe", "MZ")
        _create_file(temp_folder, "library.dll", "binary")
        _create_file(temp_folder, "song.mp3", "audio data")
        _create_file(temp_folder, "photo.png", "image data")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexed"] == 1
        assert body["failed"] == 0
        assert len(body["documents"]) == 1
        assert body["documents"][0]["filename"] == "good.txt"

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlink creation requires elevated privileges on Windows",
    )
    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_index_folder_skips_symlinks(self, mock_index, client, temp_folder):
        """Symlinked files inside the folder should be skipped."""
        real_file = _create_file(temp_folder, "real.txt", "real content")
        link_path = temp_folder / "link.txt"
        link_path.symlink_to(real_file)

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        body = resp.json()

        # Only the real file should be indexed, not the symlink
        assert body["indexed"] == 1
        assert len(body["documents"]) == 1
        assert body["documents"][0]["filename"] == "real.txt"

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_index_folder_empty_folder(self, mock_index, client, temp_folder):
        """Empty folder returns indexed=0, failed=0, empty documents list."""
        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": True},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexed"] == 0
        assert body["failed"] == 0
        assert body["documents"] == []
        assert body["errors"] == []
        assert mock_index.call_count == 0

    def test_index_folder_nonexistent_path(self, client):
        """A nonexistent folder path should return 404."""
        fake_path = str(Path.home() / ".gaia" / "test_temp" / "does_not_exist_xyz")
        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": fake_path},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_index_folder_file_not_directory(self, mock_index, client, temp_folder):
        """Passing a file path (not a directory) should return 400."""
        filepath = _create_file(temp_folder, "just_a_file.txt", "content")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(filepath)},
        )
        assert resp.status_code == 400
        assert "not a directory" in resp.json()["detail"].lower()

    def test_index_folder_null_byte_path(self, client):
        """Paths containing null bytes should be rejected with 400."""
        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": "/home/user\x00/evil"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    def test_index_folder_outside_home(self, client):
        """Paths outside the user's home directory should return 403."""
        # Use a path that is unlikely to be inside home (root / temp)
        outside_path = (
            "C:\\Windows\\System32" if platform.system() == "Windows" else "/tmp"
        )
        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": outside_path},
        )
        assert resp.status_code == 403
        assert "restricted" in resp.json()["detail"].lower()

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock)
    def test_index_folder_partial_failure(self, mock_index, client, temp_folder):
        """When _index_document fails for some files, verify mixed results."""
        _create_file(temp_folder, "good1.txt", "content 1")
        _create_file(temp_folder, "good2.md", "content 2")
        _create_file(temp_folder, "bad.csv", "will fail")

        # Succeed for .txt and .md, fail for .csv
        call_count = 0

        async def _side_effect(filepath):
            nonlocal call_count
            call_count += 1
            if filepath.suffix == ".csv":
                raise RuntimeError("Simulated indexing failure")
            return 5

        mock_index.side_effect = _side_effect

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexed"] == 2
        assert body["failed"] == 1
        assert len(body["documents"]) == 2
        assert len(body["errors"]) == 1
        assert "bad.csv" in body["errors"][0]

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=4)
    def test_index_folder_counts_correct(self, mock_index, client, temp_folder):
        """Verify indexed + failed = total candidate files."""
        # Create 5 supported files
        for i in range(5):
            _create_file(temp_folder, f"file_{i}.txt", f"content {i}")
        # Create 3 unsupported files (should not be counted at all)
        for i in range(3):
            _create_file(temp_folder, f"binary_{i}.exe", "MZ")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        body = resp.json()

        total_candidates = body["indexed"] + body["failed"]
        assert total_candidates == 5  # Only .txt files are candidates
        assert body["indexed"] == 5
        assert body["failed"] == 0
        assert len(body["documents"]) == 5

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=2)
    def test_index_folder_documents_persisted_in_db(
        self, mock_index, client, db, temp_folder
    ):
        """Indexed documents should be queryable via GET /api/documents."""
        _create_file(temp_folder, "persist_test.txt", "persistence check")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        assert resp.json()["indexed"] == 1

        # Verify document appears in the list endpoint
        list_resp = client.get("/api/documents")
        assert list_resp.status_code == 200
        docs = list_resp.json()["documents"]
        filenames = [d["filename"] for d in docs]
        assert "persist_test.txt" in filenames

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_index_folder_default_recursive_true(self, mock_index, client, temp_folder):
        """When recursive is omitted, it defaults to True."""
        _create_file(temp_folder, "top.txt", "top")
        _create_file(temp_folder, "deep/nested.md", "nested")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder)},
            # No "recursive" key -- should default to True
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexed"] == 2
        filenames = sorted(d["filename"] for d in body["documents"])
        assert filenames == ["nested.md", "top.txt"]

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=7)
    def test_index_folder_many_extensions(self, mock_index, client, temp_folder):
        """Verify a broad set of allowed extensions are accepted."""
        extensions = [".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".yaml"]
        for ext in extensions:
            _create_file(temp_folder, f"file{ext}", f"content for {ext}")

        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexed"] == len(extensions)
        assert body["failed"] == 0


# ── TestDocumentMonitor ─────────────────────────────────────────────────────


class TestDocumentMonitor:
    """Tests for GET /api/documents/monitor/status."""

    def test_monitor_status_default(self, client):
        """Monitor status should return running, interval, and reindexing fields."""
        resp = client.get("/api/documents/monitor/status")
        assert resp.status_code == 200
        body = resp.json()

        # The app lifespan starts the monitor, so it should be present
        assert "running" in body
        assert "interval_seconds" in body
        assert "reindexing" in body
        assert isinstance(body["running"], bool)
        assert isinstance(body["interval_seconds"], (int, float))
        assert isinstance(body["reindexing"], list)

    def test_monitor_status_no_monitor(self, client, app):
        """When no monitor exists on app.state, returns running=False."""
        # Temporarily remove the monitor from app state
        original_monitor = getattr(app.state, "document_monitor", None)
        try:
            if hasattr(app.state, "document_monitor"):
                delattr(app.state, "document_monitor")

            resp = client.get("/api/documents/monitor/status")
            assert resp.status_code == 200
            body = resp.json()
            assert body["running"] is False
            assert body["interval_seconds"] == 0
            assert body["reindexing"] == []
        finally:
            # Restore the monitor
            if original_monitor is not None:
                app.state.document_monitor = original_monitor

    def test_monitor_status_with_mock_monitor(self, client, app):
        """When a monitor is present on app.state, its fields are reflected."""
        mock_monitor = MagicMock()
        mock_monitor.is_running = True
        mock_monitor._interval = 30.0
        mock_monitor.reindexing_docs = {"doc-abc"}

        app.state.document_monitor = mock_monitor

        resp = client.get("/api/documents/monitor/status")
        assert resp.status_code == 200
        body = resp.json()

        assert set(body.keys()) == {"running", "interval_seconds", "reindexing"}
        assert body["running"] is True
        assert body["interval_seconds"] == 30.0
        assert body["reindexing"] == ["doc-abc"]


# ── TestDocumentStatus ──────────────────────────────────────────────────────


class TestDocumentStatus:
    """Tests for GET /api/documents/{doc_id}/status."""

    def test_document_status_complete(self, client, db):
        """Verify status for a successfully indexed document."""
        doc = db.add_document(
            filename="status_test.pdf",
            filepath=str(Path.home() / ".gaia" / "status_test.pdf"),
            file_hash="status_hash_" + str(time.time()),
            file_size=1024,
            chunk_count=10,
        )
        doc_id = doc["id"]

        resp = client.get(f"/api/documents/{doc_id}/status")
        assert resp.status_code == 200
        body = resp.json()

        assert body["id"] == doc_id
        assert body["indexing_status"] == "complete"
        assert body["chunk_count"] == 10
        assert body["is_active"] is False

    def test_document_status_not_found(self, client):
        """Requesting status for an unknown doc_id should return 404."""
        resp = client.get("/api/documents/nonexistent-doc-id-12345/status")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_document_status_is_active(self, client, db, app):
        """is_active should be True when doc_id is in indexing_tasks."""
        doc = db.add_document(
            filename="active_test.pdf",
            filepath=str(Path.home() / ".gaia" / "active_test.pdf"),
            file_hash="active_hash_" + str(time.time()),
            file_size=2048,
            chunk_count=0,
        )
        doc_id = doc["id"]
        db.update_document_status(doc_id, "indexing")

        # Simulate an active background task
        mock_task = MagicMock()
        app.state.indexing_tasks[doc_id] = mock_task

        try:
            resp = client.get(f"/api/documents/{doc_id}/status")
            assert resp.status_code == 200
            body = resp.json()

            assert body["id"] == doc_id
            assert body["indexing_status"] == "indexing"
            assert body["is_active"] is True
        finally:
            # Clean up the fake task
            app.state.indexing_tasks.pop(doc_id, None)

    def test_document_status_after_explicit_update(self, client, db):
        """Verify status reflects DB updates (e.g. failed)."""
        doc = db.add_document(
            filename="fail_status.txt",
            filepath=str(Path.home() / ".gaia" / "fail_status.txt"),
            file_hash="fail_hash_" + str(time.time()),
            file_size=512,
            chunk_count=0,
        )
        doc_id = doc["id"]
        db.update_document_status(doc_id, "failed")

        resp = client.get(f"/api/documents/{doc_id}/status")
        assert resp.status_code == 200
        body = resp.json()

        assert body["indexing_status"] == "failed"
        assert body["chunk_count"] == 0
        assert body["is_active"] is False


# ── TestCancelIndexing ──────────────────────────────────────────────────────


class TestCancelIndexing:
    """Tests for POST /api/documents/{doc_id}/cancel."""

    def test_cancel_active_task(self, client, db, app):
        """Cancelling an active task should call task.cancel() and update DB."""
        doc = db.add_document(
            filename="cancel_me.pdf",
            filepath=str(Path.home() / ".gaia" / "cancel_me.pdf"),
            file_hash="cancel_hash_" + str(time.time()),
            file_size=10_000_000,
            chunk_count=0,
        )
        doc_id = doc["id"]
        db.update_document_status(doc_id, "indexing")

        # Create a mock asyncio.Task
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        app.state.indexing_tasks[doc_id] = mock_task

        resp = client.post(f"/api/documents/{doc_id}/cancel")
        assert resp.status_code == 200
        body = resp.json()

        assert body["cancelled"] is True
        assert body["id"] == doc_id

        # Verify task.cancel() was called
        mock_task.cancel.assert_called_once()

        # Verify task was removed from indexing_tasks
        assert doc_id not in app.state.indexing_tasks

        # Verify DB status updated to cancelled
        status_resp = client.get(f"/api/documents/{doc_id}/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["indexing_status"] == "cancelled"

    def test_cancel_no_active_task(self, client, db):
        """Cancelling when no active task exists should return 404."""
        doc = db.add_document(
            filename="no_task.pdf",
            filepath=str(Path.home() / ".gaia" / "no_task.pdf"),
            file_hash="no_task_hash_" + str(time.time()),
            file_size=1024,
            chunk_count=5,
        )
        doc_id = doc["id"]

        resp = client.post(f"/api/documents/{doc_id}/cancel")
        assert resp.status_code == 404
        assert "no active" in resp.json()["detail"].lower()

    def test_cancel_unknown_document(self, client):
        """Cancelling an unknown doc_id should return 404."""
        resp = client.post("/api/documents/nonexistent-doc-999/cancel")
        assert resp.status_code == 404


# ── TestDocumentLifecycleE2E ────────────────────────────────────────────────


class TestDocumentLifecycleE2E:
    """End-to-end document lifecycle tests combining multiple endpoints."""

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=8)
    def test_full_lifecycle(self, mock_index, client, session_id, temp_folder):
        """Upload doc -> verify listed -> attach to session -> detach -> delete -> verify gone."""
        filepath = _create_file(temp_folder, "lifecycle.txt", "lifecycle test content")

        # 1. Upload document via upload-path
        upload_resp = client.post(
            "/api/documents/upload-path",
            json={"filepath": str(filepath)},
        )
        assert upload_resp.status_code == 200
        doc = upload_resp.json()
        doc_id = doc["id"]
        assert doc["filename"] == "lifecycle.txt"
        assert doc["chunk_count"] == 8

        # 2. Verify document appears in list
        list_resp = client.get("/api/documents")
        assert list_resp.status_code == 200
        doc_ids_in_list = [d["id"] for d in list_resp.json()["documents"]]
        assert doc_id in doc_ids_in_list

        # 3. Attach document to session
        attach_resp = client.post(
            f"/api/sessions/{session_id}/documents",
            json={"document_id": doc_id},
        )
        assert attach_resp.status_code == 200
        assert attach_resp.json()["attached"] is True

        # 4. Verify session lists the document
        session_resp = client.get(f"/api/sessions/{session_id}")
        assert session_resp.status_code == 200
        assert doc_id in session_resp.json()["document_ids"]

        # 5. Detach document from session
        detach_resp = client.delete(
            f"/api/sessions/{session_id}/documents/{doc_id}",
        )
        assert detach_resp.status_code == 200
        assert detach_resp.json()["detached"] is True

        # 6. Verify session no longer lists the document
        session_resp2 = client.get(f"/api/sessions/{session_id}")
        assert doc_id not in session_resp2.json()["document_ids"]

        # 7. Delete document
        delete_resp = client.delete(f"/api/documents/{doc_id}")
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] is True

        # 8. Verify document is gone from list
        list_resp2 = client.get("/api/documents")
        doc_ids_after = [d["id"] for d in list_resp2.json()["documents"]]
        assert doc_id not in doc_ids_after

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_upload_then_reupload_same_file(self, mock_index, client, temp_folder):
        """Re-uploading the same file should return the existing document (hash dedup)."""
        filepath = _create_file(temp_folder, "dedup.txt", "identical content")

        # First upload
        resp1 = client.post(
            "/api/documents/upload-path",
            json={"filepath": str(filepath)},
        )
        assert resp1.status_code == 200
        doc1 = resp1.json()

        # Second upload of the same file (same content, same hash)
        resp2 = client.post(
            "/api/documents/upload-path",
            json={"filepath": str(filepath)},
        )
        assert resp2.status_code == 200
        doc2 = resp2.json()

        # Should return the same document ID due to hash deduplication
        assert doc1["id"] == doc2["id"]

        # Verify only one document exists in the list
        list_resp = client.get("/api/documents")
        docs = list_resp.json()["documents"]
        matching = [d for d in docs if d["id"] == doc1["id"]]
        assert len(matching) == 1

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=6)
    def test_upload_index_query_delete(self, mock_index, client, temp_folder):
        """Upload -> check status -> delete -> verify removed from list."""
        filepath = _create_file(temp_folder, "query_del.md", "# Query Delete Test")

        # Upload
        upload_resp = client.post(
            "/api/documents/upload-path",
            json={"filepath": str(filepath)},
        )
        assert upload_resp.status_code == 200
        doc_id = upload_resp.json()["id"]

        # Check status
        status_resp = client.get(f"/api/documents/{doc_id}/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["indexing_status"] == "complete"
        assert status_resp.json()["chunk_count"] == 6

        # Delete
        del_resp = client.delete(f"/api/documents/{doc_id}")
        assert del_resp.status_code == 200

        # Verify removed
        list_resp = client.get("/api/documents")
        doc_ids = [d["id"] for d in list_resp.json()["documents"]]
        assert doc_id not in doc_ids

        # Status should now return 404
        status_resp2 = client.get(f"/api/documents/{doc_id}/status")
        assert status_resp2.status_code == 404

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=4)
    def test_multi_document_session(self, mock_index, client, session_id, temp_folder):
        """Create session, attach 3 docs, verify all attached, detach one, verify 2 remain."""
        doc_ids = []
        for i in range(3):
            filepath = _create_file(
                temp_folder, f"multi_{i}.txt", f"content {i} {time.time()}"
            )
            resp = client.post(
                "/api/documents/upload-path",
                json={"filepath": str(filepath)},
            )
            assert resp.status_code == 200
            doc_ids.append(resp.json()["id"])

        # Attach all 3 documents
        for did in doc_ids:
            attach_resp = client.post(
                f"/api/sessions/{session_id}/documents",
                json={"document_id": did},
            )
            assert attach_resp.status_code == 200

        # Verify all 3 are attached
        session_resp = client.get(f"/api/sessions/{session_id}")
        assert session_resp.status_code == 200
        attached_ids = session_resp.json()["document_ids"]
        for did in doc_ids:
            assert did in attached_ids

        # Detach the middle document
        detach_resp = client.delete(
            f"/api/sessions/{session_id}/documents/{doc_ids[1]}",
        )
        assert detach_resp.status_code == 200

        # Verify 2 remain
        session_resp2 = client.get(f"/api/sessions/{session_id}")
        remaining_ids = session_resp2.json()["document_ids"]
        assert len(remaining_ids) == 2
        assert doc_ids[0] in remaining_ids
        assert doc_ids[1] not in remaining_ids
        assert doc_ids[2] in remaining_ids

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=3)
    def test_folder_index_then_list_and_delete_all(
        self, mock_index, client, temp_folder
    ):
        """Bulk-index a folder, verify all in list, then delete each individually."""
        for name in ["a.txt", "b.md", "c.json"]:
            _create_file(temp_folder, name, f"content of {name}")

        # Index folder
        resp = client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["indexed"] == 3

        indexed_ids = [d["id"] for d in body["documents"]]

        # Verify all appear in /api/documents
        list_resp = client.get("/api/documents")
        all_ids = [d["id"] for d in list_resp.json()["documents"]]
        for did in indexed_ids:
            assert did in all_ids

        # Delete each document
        for did in indexed_ids:
            del_resp = client.delete(f"/api/documents/{did}")
            assert del_resp.status_code == 200

        # Verify all gone
        list_resp2 = client.get("/api/documents")
        remaining_ids = [d["id"] for d in list_resp2.json()["documents"]]
        for did in indexed_ids:
            assert did not in remaining_ids

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_delete_nonexistent_document(self, mock_index, client):
        """Deleting a nonexistent document should return 404."""
        resp = client.delete("/api/documents/does-not-exist-xyz")
        assert resp.status_code == 404

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_attach_nonexistent_document_to_session(
        self, mock_index, client, session_id
    ):
        """Attaching a nonexistent document to a session should return 404."""
        resp = client.post(
            f"/api/sessions/{session_id}/documents",
            json={"document_id": "nonexistent-doc-abc"},
        )
        assert resp.status_code == 404

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=5)
    def test_attach_document_to_nonexistent_session(
        self, mock_index, client, db, temp_folder
    ):
        """Attaching a document to a nonexistent session should return 404."""
        filepath = _create_file(temp_folder, "orphan.txt", "orphan content")
        upload_resp = client.post(
            "/api/documents/upload-path",
            json={"filepath": str(filepath)},
        )
        doc_id = upload_resp.json()["id"]

        resp = client.post(
            "/api/sessions/nonexistent-session-xyz/documents",
            json={"document_id": doc_id},
        )
        assert resp.status_code == 404

    @patch("gaia.ui.server._index_document", new_callable=AsyncMock, return_value=10)
    def test_document_list_totals(self, mock_index, client, temp_folder):
        """Verify total, total_size_bytes, and total_chunks in list response."""
        _create_file(temp_folder, "sized1.txt", "a" * 100)
        _create_file(temp_folder, "sized2.txt", "b" * 200)

        # Index both files
        client.post(
            "/api/documents/index-folder",
            json={"folder_path": str(temp_folder), "recursive": False},
        )

        list_resp = client.get("/api/documents")
        assert list_resp.status_code == 200
        body = list_resp.json()

        assert body["total"] == 2
        assert body["total_chunks"] == 20  # 10 chunks each
        assert body["total_size_bytes"] > 0  # Both files have non-zero size
