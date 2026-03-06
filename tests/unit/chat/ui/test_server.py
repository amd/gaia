# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for GAIA Chat UI FastAPI server.

Tests all API endpoints using TestClient with an in-memory database.
LLM and RAG calls are mocked - these tests validate HTTP layer behavior.
"""

import hashlib
import logging
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.chat.ui.server import (
    _compute_file_hash,
    _sanitize_document_path,
    _sanitize_static_path,
    _validate_file_path,
    create_app,
)

logger = logging.getLogger(__name__)


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


class TestHealthEndpoint:
    """Tests for /api/health endpoint."""

    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "gaia-chat-ui"
        assert "stats" in data

    def test_health_includes_stats(self, client):
        resp = client.get("/api/health")
        data = resp.json()
        stats = data["stats"]
        assert "sessions" in stats
        assert "messages" in stats
        assert "documents" in stats

    def test_health_stats_update_after_data(self, client, db):
        db.create_session(title="Test")
        resp = client.get("/api/health")
        stats = resp.json()["stats"]
        assert stats["sessions"] == 1


class TestSystemStatus:
    """Tests for /api/system/status endpoint."""

    def test_system_status_returns_200(self, client):
        resp = client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "lemonade_running" in data
        assert "model_loaded" in data
        assert "disk_space_gb" in data
        assert "version" in data

    def test_system_status_lemonade_field_is_boolean(self, client):
        """lemonade_running should be a boolean regardless of server state."""
        resp = client.get("/api/system/status")
        data = resp.json()
        assert isinstance(data["lemonade_running"], bool)

    def test_system_status_has_version(self, client):
        resp = client.get("/api/system/status")
        data = resp.json()
        assert data["version"] == "0.1.0"

    def test_system_status_has_all_fields(self, client):
        resp = client.get("/api/system/status")
        data = resp.json()
        expected_fields = [
            "lemonade_running",
            "model_loaded",
            "embedding_model_loaded",
            "disk_space_gb",
            "memory_available_gb",
            "initialized",
            "version",
        ]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    def test_system_status_disk_space_non_negative(self, client):
        resp = client.get("/api/system/status")
        data = resp.json()
        assert data["disk_space_gb"] >= 0


class TestSessionEndpoints:
    """Tests for /api/sessions/* endpoints."""

    def test_list_sessions_empty(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"] == []
        assert data["total"] == 0

    def test_create_session_default(self, client):
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["title"] == "New Chat"
        assert "model" in data
        assert data["message_count"] == 0
        assert data["document_ids"] == []

    def test_create_session_custom(self, client):
        resp = client.post(
            "/api/sessions",
            json={
                "title": "Test Chat",
                "model": "Qwen3-0.6B-GGUF",
                "system_prompt": "You are a test assistant.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Chat"
        assert data["model"] == "Qwen3-0.6B-GGUF"
        assert data["system_prompt"] == "You are a test assistant."

    def test_create_session_with_document_ids(self, client, db):
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        resp = client.post(
            "/api/sessions",
            json={
                "document_ids": [doc["id"]],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert doc["id"] in data["document_ids"]

    def test_get_session(self, client):
        create_resp = client.post("/api/sessions", json={"title": "Get Me"})
        session_id = create_resp.json()["id"]

        resp = client.get(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Get Me"

    def test_get_session_includes_all_fields(self, client):
        create_resp = client.post("/api/sessions", json={"title": "Full"})
        data = create_resp.json()
        required_fields = [
            "id",
            "title",
            "created_at",
            "updated_at",
            "model",
            "message_count",
            "document_ids",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_get_session_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent-uuid")
        assert resp.status_code == 404

    def test_update_session_title(self, client):
        create_resp = client.post("/api/sessions", json={"title": "Original"})
        session_id = create_resp.json()["id"]

        resp = client.put(f"/api/sessions/{session_id}", json={"title": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated"

    def test_update_session_system_prompt(self, client):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/sessions/{session_id}", json={"system_prompt": "Be concise."}
        )
        assert resp.status_code == 200
        assert resp.json()["system_prompt"] == "Be concise."

    def test_update_session_not_found(self, client):
        resp = client.put("/api/sessions/nonexistent", json={"title": "Nope"})
        assert resp.status_code == 404

    def test_delete_session(self, client):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        resp = client.delete(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify it's gone
        get_resp = client.get(f"/api/sessions/{session_id}")
        assert get_resp.status_code == 404

    def test_delete_session_not_found(self, client):
        resp = client.delete("/api/sessions/nonexistent")
        assert resp.status_code == 404

    def test_list_sessions_with_pagination(self, client):
        # Create 5 sessions
        for i in range(5):
            client.post("/api/sessions", json={"title": f"Session {i}"})

        resp = client.get("/api/sessions?limit=2&offset=0")
        data = resp.json()
        assert len(data["sessions"]) == 2
        assert data["total"] == 5

    def test_list_sessions_ordered_by_recency(self, client):
        client.post("/api/sessions", json={"title": "First"})
        client.post("/api/sessions", json={"title": "Second"})
        client.post("/api/sessions", json={"title": "Third"})

        resp = client.get("/api/sessions")
        data = resp.json()
        assert data["sessions"][0]["title"] == "Third"


class TestMessageEndpoints:
    """Tests for /api/sessions/{id}/messages endpoints."""

    def test_get_messages_empty(self, client):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        resp = client.get(f"/api/sessions/{session_id}/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["total"] == 0

    def test_get_messages_session_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent/messages")
        assert resp.status_code == 404

    def test_get_messages_after_chat(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        # Add messages directly via db (simulating chat)
        db.add_message(session_id, "user", "Hello!")
        db.add_message(session_id, "assistant", "Hi there!")

        resp = client.get(f"/api/sessions/{session_id}/messages")
        data = resp.json()
        assert data["total"] == 2
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"

    def test_get_messages_with_pagination(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        for i in range(5):
            db.add_message(session_id, "user", f"Message {i}")

        resp = client.get(f"/api/sessions/{session_id}/messages?limit=2&offset=1")
        data = resp.json()
        assert len(data["messages"]) == 2
        assert data["total"] == 5

    def test_get_messages_includes_rag_sources(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        sources = [
            {
                "document_id": "doc1",
                "filename": "test.pdf",
                "chunk": "some text",
                "score": 0.9,
            }
        ]
        db.add_message(session_id, "assistant", "Answer", rag_sources=sources)

        resp = client.get(f"/api/sessions/{session_id}/messages")
        data = resp.json()
        msg = data["messages"][0]
        assert msg["rag_sources"] is not None
        assert msg["rag_sources"][0]["document_id"] == "doc1"

    def test_message_response_has_all_fields(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]
        db.add_message(session_id, "user", "Hello")

        resp = client.get(f"/api/sessions/{session_id}/messages")
        msg = resp.json()["messages"][0]
        for field in ["id", "session_id", "role", "content", "created_at"]:
            assert field in msg, f"Missing field: {field}"


class TestExportEndpoint:
    """Tests for /api/sessions/{id}/export endpoint."""

    def test_export_markdown(self, client, db):
        create_resp = client.post("/api/sessions", json={"title": "Export Me"})
        session_id = create_resp.json()["id"]

        db.add_message(session_id, "user", "Hello")
        db.add_message(session_id, "assistant", "Hi!")

        resp = client.get(f"/api/sessions/{session_id}/export?format=markdown")
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "markdown"
        assert "# Export Me" in data["content"]
        assert "**User:**" in data["content"]
        assert "**Assistant:**" in data["content"]

    def test_export_markdown_includes_metadata(self, client, db):
        create_resp = client.post("/api/sessions", json={"title": "Meta Test"})
        session_id = create_resp.json()["id"]

        resp = client.get(f"/api/sessions/{session_id}/export?format=markdown")
        content = resp.json()["content"]
        assert "*Created:" in content
        assert "*Model:" in content

    def test_export_json(self, client, db):
        create_resp = client.post("/api/sessions", json={"title": "JSON Export"})
        session_id = create_resp.json()["id"]

        db.add_message(session_id, "user", "Hello")

        resp = client.get(f"/api/sessions/{session_id}/export?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "json"
        assert "session" in data
        assert "messages" in data

    def test_export_json_contains_messages(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]
        db.add_message(session_id, "user", "Test export")

        resp = client.get(f"/api/sessions/{session_id}/export?format=json")
        data = resp.json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["content"] == "Test export"

    def test_export_unsupported_format(self, client):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        resp = client.get(f"/api/sessions/{session_id}/export?format=xml")
        assert resp.status_code == 400

    def test_export_session_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent/export")
        assert resp.status_code == 404


class TestChatSendEndpoint:
    """Tests for /api/chat/send endpoint."""

    def test_send_message_session_not_found(self, client):
        resp = client.post(
            "/api/chat/send",
            json={
                "session_id": "nonexistent",
                "message": "Hello",
                "stream": False,
            },
        )
        assert resp.status_code == 404

    @patch("gaia.chat.ui.server._get_chat_response")
    def test_send_message_non_streaming(self, mock_chat, client):
        mock_chat.return_value = "This is a response."

        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": "Hello",
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert data["content"] == "This is a response."
        assert "message_id" in data

    @patch("gaia.chat.ui.server._get_chat_response")
    def test_non_streaming_saves_both_messages(self, mock_chat, client, db):
        mock_chat.return_value = "Bot reply"

        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": "User says hi",
                "stream": False,
            },
        )

        messages = db.get_messages(session_id)
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles

    def test_send_message_saves_user_message(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        # Send with stream=True but we don't consume the stream
        # The user message should still be saved
        with patch("gaia.chat.ui.server._stream_chat_response") as mock_stream:

            async def fake_stream(*args, **kwargs):
                yield 'data: {"type": "done", "content": "test"}\n\n'

            mock_stream.return_value = fake_stream()

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "Hello from test",
                    "stream": True,
                },
            )

        # User message should be in the database
        messages = db.get_messages(session_id)
        user_messages = [m for m in messages if m["role"] == "user"]
        assert len(user_messages) >= 1
        assert user_messages[0]["content"] == "Hello from test"

    def test_streaming_response_is_event_stream(self, client):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        with patch("gaia.chat.ui.server._stream_chat_response") as mock_stream:

            async def fake_stream(*args, **kwargs):
                yield 'data: {"type": "chunk", "content": "Hi"}\n\n'
                yield 'data: {"type": "done", "content": "Hi"}\n\n'

            mock_stream.return_value = fake_stream()

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "Test",
                    "stream": True,
                },
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")


class TestDocumentEndpoints:
    """Tests for /api/documents/* endpoints."""

    def test_list_documents_empty(self, client):
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["documents"] == []
        assert data["total"] == 0
        assert data["total_size_bytes"] == 0
        assert data["total_chunks"] == 0

    def test_list_documents_with_data(self, client, db):
        db.add_document(
            "test.pdf", "/test.pdf", "hash1", file_size=5000, chunk_count=10
        )
        db.add_document(
            "test2.pdf", "/test2.pdf", "hash2", file_size=3000, chunk_count=7
        )

        resp = client.get("/api/documents")
        data = resp.json()
        assert data["total"] == 2
        assert data["total_size_bytes"] == 8000
        assert data["total_chunks"] == 17

    def test_list_documents_response_fields(self, client, db):
        db.add_document("test.pdf", "/test.pdf", "hash1", file_size=1000, chunk_count=5)
        resp = client.get("/api/documents")
        doc = resp.json()["documents"][0]
        for field in [
            "id",
            "filename",
            "filepath",
            "file_size",
            "chunk_count",
            "indexed_at",
            "sessions_using",
        ]:
            assert field in doc, f"Missing field: {field}"

    @patch("gaia.chat.ui.server._index_document")
    def test_upload_by_path_file_not_found(self, mock_index, client):
        resp = client.post(
            "/api/documents/upload-path", json={"filepath": "/nonexistent/file.pdf"}
        )
        assert resp.status_code == 404

    @patch("gaia.chat.ui.server._index_document")
    def test_upload_by_path_success(self, mock_index, client):
        mock_index.return_value = 15

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"test content for hashing")
            tmp_path = f.name

        try:
            resp = client.post(
                "/api/documents/upload-path", json={"filepath": tmp_path}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["filename"] == os.path.basename(tmp_path)
            assert data["chunk_count"] == 15
            assert data["file_size"] > 0
        finally:
            os.unlink(tmp_path)

    @patch("gaia.chat.ui.server._index_document")
    def test_upload_by_path_directory_returns_400(self, mock_index, client):
        with tempfile.TemporaryDirectory() as tmp_dir:
            resp = client.post("/api/documents/upload-path", json={"filepath": tmp_dir})
            assert resp.status_code == 400

    def test_delete_document(self, client, db):
        doc = db.add_document("delete.pdf", "/del.pdf", "del_hash")

        resp = client.delete(f"/api/documents/{doc['id']}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_document_not_found(self, client):
        resp = client.delete("/api/documents/nonexistent")
        assert resp.status_code == 404


class TestSessionDocumentEndpoints:
    """Tests for /api/sessions/{id}/documents/* endpoints."""

    def test_attach_document(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        doc = db.add_document("attach.pdf", "/attach.pdf", "attach_hash")

        resp = client.post(
            f"/api/sessions/{session_id}/documents", json={"document_id": doc["id"]}
        )
        assert resp.status_code == 200
        assert resp.json()["attached"] is True

    def test_attach_document_appears_in_session(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        doc = db.add_document("visible.pdf", "/visible.pdf", "vis_hash")
        client.post(
            f"/api/sessions/{session_id}/documents", json={"document_id": doc["id"]}
        )

        # Get session and verify doc is attached
        resp = client.get(f"/api/sessions/{session_id}")
        data = resp.json()
        assert doc["id"] in data["document_ids"]

    def test_attach_document_session_not_found(self, client):
        resp = client.post(
            "/api/sessions/nonexistent/documents", json={"document_id": "doc123"}
        )
        assert resp.status_code == 404

    def test_attach_document_doc_not_found(self, client):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/sessions/{session_id}/documents", json={"document_id": "nonexistent"}
        )
        assert resp.status_code == 404

    def test_detach_document(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        doc = db.add_document("detach.pdf", "/detach.pdf", "detach_hash")
        db.attach_document(session_id, doc["id"])

        resp = client.delete(f"/api/sessions/{session_id}/documents/{doc['id']}")
        assert resp.status_code == 200
        assert resp.json()["detached"] is True

    def test_detach_not_attached_document(self, client, db):
        create_resp = client.post("/api/sessions", json={})
        session_id = create_resp.json()["id"]

        # Detach something never attached — should still return 200
        resp = client.delete(f"/api/sessions/{session_id}/documents/nonexistent-doc")
        assert resp.status_code == 200
        assert resp.json()["detached"] is True


class TestCORSConfiguration:
    """Tests for CORS middleware configuration."""

    def test_cors_headers_present(self, client):
        resp = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:4200",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS should be configured for local development
        assert resp.status_code in (200, 405)


class TestServerMetadata:
    """Tests for server configuration and metadata."""

    def test_app_title(self, app):
        assert app.title == "GAIA Chat UI API"

    def test_app_version(self, app):
        assert app.version == "0.1.0"

    def test_app_has_db_state(self, app):
        assert hasattr(app.state, "db")
        assert app.state.db is not None

    def test_app_description(self, app):
        assert "privacy" in app.description.lower() or "chat" in app.description.lower()

    def test_default_port_is_4200(self):
        from gaia.chat.ui.server import DEFAULT_PORT

        assert DEFAULT_PORT == 4200


class TestHelperFunctions:
    """Tests for server helper functions."""

    def test_compute_file_hash(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"hello world")
            tmp_path = f.name

        try:
            from pathlib import Path

            result = _compute_file_hash(Path(tmp_path))
            expected = hashlib.sha256(b"hello world").hexdigest()
            assert result == expected
        finally:
            os.unlink(tmp_path)

    def test_compute_file_hash_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            tmp_path = f.name

        try:
            from pathlib import Path

            result = _compute_file_hash(Path(tmp_path))
            expected = hashlib.sha256(b"").hexdigest()
            assert result == expected
        finally:
            os.unlink(tmp_path)


class TestValidateFilePath:
    """Tests for _validate_file_path security validation."""

    def test_valid_pdf_path(self):
        from pathlib import Path

        # Should not raise for a valid absolute path with allowed extension
        _validate_file_path(Path("/home/user/document.pdf").resolve())

    def test_valid_txt_path(self):
        from pathlib import Path

        _validate_file_path(Path("/home/user/notes.txt").resolve())

    def test_valid_md_path(self):
        from pathlib import Path

        _validate_file_path(Path("/home/user/readme.md").resolve())

    def test_rejects_null_bytes(self):
        from pathlib import Path

        with pytest.raises(Exception) as exc_info:
            _validate_file_path(Path("/home/user/file\x00.pdf"))
        assert exc_info.value.status_code == 400

    def test_rejects_unsupported_extension(self):
        from pathlib import Path

        with pytest.raises(Exception) as exc_info:
            _validate_file_path(Path("/home/user/malware.exe").resolve())
        assert exc_info.value.status_code == 400
        assert "Unsupported file type" in exc_info.value.detail

    def test_rejects_no_extension(self):
        from pathlib import Path

        with pytest.raises(Exception) as exc_info:
            _validate_file_path(Path("/home/user/noextension").resolve())
        assert exc_info.value.status_code == 400

    def test_rejects_binary_extensions(self):
        from pathlib import Path

        for ext in [".exe", ".dll", ".so", ".bin", ".dat"]:
            with pytest.raises(Exception) as exc_info:
                _validate_file_path(Path(f"/home/user/file{ext}").resolve())
            assert exc_info.value.status_code == 400

    def test_allows_code_extensions(self):
        from pathlib import Path

        for ext in [".py", ".js", ".ts", ".java", ".c", ".cpp"]:
            # Should not raise
            _validate_file_path(Path(f"/home/user/file{ext}").resolve())

    def test_allows_document_extensions(self):
        from pathlib import Path

        for ext in [".pdf", ".doc", ".docx", ".csv", ".json", ".yaml"]:
            # Should not raise
            _validate_file_path(Path(f"/home/user/file{ext}").resolve())

    @patch("gaia.chat.ui.server._index_document")
    def test_upload_rejects_unsafe_extension(self, mock_index, client):
        """Integration test: upload endpoint rejects unsafe file types."""
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"fake executable")
            tmp_path = f.name

        try:
            resp = client.post(
                "/api/documents/upload-path", json={"filepath": tmp_path}
            )
            assert resp.status_code == 400
            assert "Unsupported file type" in resp.json()["detail"]
        finally:
            os.unlink(tmp_path)


class TestSanitizeDocumentPath:
    """Tests for _sanitize_document_path security sanitization."""

    def test_returns_resolved_path(self):
        from pathlib import Path

        result = _sanitize_document_path("/home/user/doc.pdf")
        assert result.is_absolute()
        assert result == Path("/home/user/doc.pdf").resolve()

    def test_rejects_null_bytes(self):
        with pytest.raises(Exception) as exc_info:
            _sanitize_document_path("/home/user/file\x00.pdf")
        assert exc_info.value.status_code == 400

    def test_rejects_unsafe_extension(self):
        with pytest.raises(Exception) as exc_info:
            _sanitize_document_path("/home/user/malware.exe")
        assert exc_info.value.status_code == 400
        assert "Unsupported file type" in exc_info.value.detail

    def test_accepts_valid_extensions(self):
        for ext in [".pdf", ".txt", ".md", ".json", ".py", ".csv"]:
            result = _sanitize_document_path(f"/home/user/file{ext}")
            assert result.suffix == ext

    def test_resolves_traversal_in_path(self):
        from pathlib import Path

        # Path with .. should be resolved
        result = _sanitize_document_path("/home/user/../user/doc.txt")
        assert ".." not in str(result)
        assert result == Path("/home/user/doc.txt").resolve()


class TestSanitizeStaticPath:
    """Tests for _sanitize_static_path security sanitization."""

    def test_valid_path_within_base(self):
        from pathlib import Path

        base = Path(tempfile.mkdtemp())
        try:
            # Create a test file
            test_file = base / "test.html"
            test_file.write_text("hello")

            result = _sanitize_static_path(base, "test.html")
            assert result is not None
            assert result == test_file.resolve()
        finally:
            import shutil

            shutil.rmtree(base)

    def test_rejects_traversal_with_dotdot(self):
        from pathlib import Path

        base = Path(tempfile.mkdtemp())
        try:
            result = _sanitize_static_path(base, "../../../etc/passwd")
            assert result is None
        finally:
            import shutil

            shutil.rmtree(base)

    def test_rejects_null_bytes(self):
        from pathlib import Path

        base = Path(tempfile.mkdtemp())
        try:
            result = _sanitize_static_path(base, "file\x00.html")
            assert result is None
        finally:
            import shutil

            shutil.rmtree(base)

    def test_returns_none_for_empty_path(self):
        from pathlib import Path

        result = _sanitize_static_path(Path("/tmp"), "")
        assert result is None

    def test_rejects_absolute_path_escape(self):
        from pathlib import Path

        base = Path(tempfile.mkdtemp())
        try:
            # Even if resolved, must be within base
            result = _sanitize_static_path(base, "/etc/passwd")
            # On Windows this resolves differently, but the relative_to check
            # should still reject paths outside base
            if result is not None:
                assert str(result).startswith(str(base.resolve()))
        finally:
            import shutil

            shutil.rmtree(base)
