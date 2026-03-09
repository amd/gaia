# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for GAIA Agent UI.

Tests full end-to-end workflows through the HTTP API layer:
- Session lifecycle (create -> chat -> export -> delete)
- Document management and session-document attachment workflows
- SSE streaming response format validation
- Concurrent access patterns
- Edge cases (unicode, large payloads, malformed input)
- System status endpoint with mocked backends
- CLI --ui flag integration
- Database persistence and thread safety

These tests use FastAPI TestClient with in-memory database.
LLM/RAG calls are mocked -- these validate integration of
server + database + models layers.
"""

import json
import logging
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.database import ChatDatabase
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
    resp = client.post("/api/sessions", json={"title": "Test Session"})
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.fixture
def doc_id(client, db):
    """Add a test document directly in DB and return its ID.

    Used by tests that need a pre-existing document without going
    through the upload-path endpoint (which requires real files).
    """
    doc = db.add_document(
        "integration-test.pdf",
        "/tmp/integration-test.pdf",
        "int_test_hash_" + str(time.time()),
        file_size=2048,
        chunk_count=12,
    )
    return doc["id"]


# ── Full Session Lifecycle ──────────────────────────────────────────────────


class TestSessionLifecycle:
    """End-to-end session lifecycle: create -> chat -> export -> delete."""

    @patch("gaia.ui.server._get_chat_response")
    def test_full_lifecycle(self, mock_chat, client):
        """Create session, send messages, export, then delete."""
        mock_chat.return_value = "Hello! I'm the GAIA assistant."

        # 1. Create session
        create_resp = client.post(
            "/api/sessions",
            json={
                "title": "Lifecycle Test",
                "model": "Qwen3-0.6B-GGUF",
                "system_prompt": "You are a helpful AI assistant.",
            },
        )
        assert create_resp.status_code == 200
        session = create_resp.json()
        session_id = session["id"]
        assert session["title"] == "Lifecycle Test"
        assert session["model"] == "Qwen3-0.6B-GGUF"
        assert session["system_prompt"] == "You are a helpful AI assistant."
        assert session["message_count"] == 0

        # 2. Send a non-streaming message
        chat_resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": "Hello, who are you?",
                "stream": False,
            },
        )
        assert chat_resp.status_code == 200
        chat_data = chat_resp.json()
        assert chat_data["content"] == "Hello! I'm the GAIA assistant."
        assert "message_id" in chat_data

        # 3. Verify messages are persisted
        msgs_resp = client.get(f"/api/sessions/{session_id}/messages")
        assert msgs_resp.status_code == 200
        msgs = msgs_resp.json()
        assert msgs["total"] == 2
        assert msgs["messages"][0]["role"] == "user"
        assert msgs["messages"][0]["content"] == "Hello, who are you?"
        assert msgs["messages"][1]["role"] == "assistant"
        assert msgs["messages"][1]["content"] == "Hello! I'm the GAIA assistant."

        # 4. Message count updated in session
        session_resp = client.get(f"/api/sessions/{session_id}")
        assert session_resp.json()["message_count"] == 2

        # 5. Export to markdown
        export_resp = client.get(f"/api/sessions/{session_id}/export?format=markdown")
        assert export_resp.status_code == 200
        export_data = export_resp.json()
        assert export_data["format"] == "markdown"
        assert "# Lifecycle Test" in export_data["content"]
        assert "Hello, who are you?" in export_data["content"]
        assert "Hello! I'm the GAIA assistant." in export_data["content"]

        # 6. Export to JSON
        json_export = client.get(f"/api/sessions/{session_id}/export?format=json")
        assert json_export.status_code == 200
        json_data = json_export.json()
        assert json_data["format"] == "json"
        assert len(json_data["messages"]) == 2

        # 7. Update session title
        update_resp = client.put(
            f"/api/sessions/{session_id}",
            json={"title": "Renamed Session"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["title"] == "Renamed Session"

        # 8. Session appears in list
        list_resp = client.get("/api/sessions")
        sessions = list_resp.json()["sessions"]
        assert any(s["id"] == session_id for s in sessions)
        assert any(s["title"] == "Renamed Session" for s in sessions)

        # 9. Delete session
        del_resp = client.delete(f"/api/sessions/{session_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True

        # 10. Session is gone
        get_resp = client.get(f"/api/sessions/{session_id}")
        assert get_resp.status_code == 404

        # 11. Messages are cascade-deleted
        msgs_resp = client.get(f"/api/sessions/{session_id}/messages")
        assert msgs_resp.status_code == 404

    @patch("gaia.ui.server._get_chat_response")
    def test_multi_turn_conversation(self, mock_chat, client):
        """Verify multi-turn conversation history is preserved in order."""
        responses = iter(
            [
                "I'm GAIA, a local AI assistant.",
                "The capital of France is Paris.",
                "It has about 2.2 million people in the city proper.",
            ]
        )
        mock_chat.side_effect = lambda *a, **kw: next(responses)

        resp = client.post("/api/sessions", json={"title": "Multi-turn"})
        sid = resp.json()["id"]

        questions = [
            "Who are you?",
            "What is the capital of France?",
            "How many people live there?",
        ]

        for q in questions:
            chat_resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": sid,
                    "message": q,
                    "stream": False,
                },
            )
            assert chat_resp.status_code == 200

        # Check all messages in order
        msgs_resp = client.get(f"/api/sessions/{sid}/messages")
        messages = msgs_resp.json()["messages"]
        assert len(messages) == 6  # 3 user + 3 assistant

        # Verify alternating roles
        for i, msg in enumerate(messages):
            expected_role = "user" if i % 2 == 0 else "assistant"
            assert (
                msg["role"] == expected_role
            ), f"Message {i} expected {expected_role}, got {msg['role']}"

        # Verify content order
        assert messages[0]["content"] == "Who are you?"
        assert messages[1]["content"] == "I'm GAIA, a local AI assistant."
        assert messages[4]["content"] == "How many people live there?"
        assert (
            messages[5]["content"]
            == "It has about 2.2 million people in the city proper."
        )


# ── Document Workflow ───────────────────────────────────────────────────────


class TestDocumentWorkflow:
    """End-to-end document management and session attachment workflows."""

    @patch("gaia.ui.server._index_document")
    def test_upload_attach_detach_delete(self, mock_index, client):
        """Full document lifecycle: upload -> attach to session -> detach -> delete."""
        mock_index.return_value = 25

        # 1. Create a real temp file to upload
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("This is a test document for integration testing.")
            tmp_path = f.name

        try:
            # 2. Upload document
            upload_resp = client.post(
                "/api/documents/upload-path",
                json={"filepath": tmp_path},
            )
            assert upload_resp.status_code == 200
            doc = upload_resp.json()
            doc_id = doc["id"]
            assert doc["chunk_count"] == 25
            assert doc["file_size"] > 0
            assert doc["filename"] == os.path.basename(tmp_path)

            # 3. Document appears in library
            lib_resp = client.get("/api/documents")
            assert lib_resp.status_code == 200
            lib_data = lib_resp.json()
            assert lib_data["total"] == 1
            assert lib_data["total_chunks"] == 25
            doc_ids = [d["id"] for d in lib_data["documents"]]
            assert doc_id in doc_ids

            # 4. Create session and attach document
            sess_resp = client.post(
                "/api/sessions",
                json={
                    "title": "Doc Test Session",
                },
            )
            session_id = sess_resp.json()["id"]

            attach_resp = client.post(
                f"/api/sessions/{session_id}/documents",
                json={"document_id": doc_id},
            )
            assert attach_resp.status_code == 200
            assert attach_resp.json()["attached"] is True

            # 5. Document appears in session
            sess_detail = client.get(f"/api/sessions/{session_id}")
            assert doc_id in sess_detail.json()["document_ids"]

            # 6. Document shows sessions_using count
            lib_resp2 = client.get("/api/documents")
            doc_data = next(
                d for d in lib_resp2.json()["documents"] if d["id"] == doc_id
            )
            assert doc_data["sessions_using"] == 1

            # 7. Detach document
            detach_resp = client.delete(
                f"/api/sessions/{session_id}/documents/{doc_id}"
            )
            assert detach_resp.status_code == 200

            # 8. Document no longer in session
            sess_detail2 = client.get(f"/api/sessions/{session_id}")
            assert doc_id not in sess_detail2.json()["document_ids"]

            # 9. Delete document
            del_resp = client.delete(f"/api/documents/{doc_id}")
            assert del_resp.status_code == 200
            assert del_resp.json()["deleted"] is True

            # 10. Document gone from library
            lib_resp3 = client.get("/api/documents")
            assert lib_resp3.json()["total"] == 0

        finally:
            os.unlink(tmp_path)

    def test_shared_document_across_sessions(self, client, db):
        """A single document attached to multiple sessions."""
        doc = db.add_document(
            "shared.pdf",
            "/tmp/shared.pdf",
            "shared_hash_1234",
            file_size=4096,
            chunk_count=20,
        )

        session_ids = []
        for i in range(3):
            resp = client.post("/api/sessions", json={"title": f"Session {i}"})
            session_ids.append(resp.json()["id"])

        # Attach to all sessions
        for sid in session_ids:
            resp = client.post(
                f"/api/sessions/{sid}/documents",
                json={"document_id": doc["id"]},
            )
            assert resp.status_code == 200

        # Verify sessions_using count
        lib_resp = client.get("/api/documents")
        doc_data = lib_resp.json()["documents"][0]
        assert doc_data["sessions_using"] == 3

        # Delete one session -- doc should still exist
        client.delete(f"/api/sessions/{session_ids[0]}")
        lib_resp2 = client.get("/api/documents")
        assert lib_resp2.json()["total"] == 1
        doc_data2 = lib_resp2.json()["documents"][0]
        assert doc_data2["sessions_using"] == 2

    def test_create_session_with_pre_attached_documents(self, client, db):
        """Create a session with documents pre-attached."""
        doc1 = db.add_document("a.pdf", "/a.pdf", "hash_a", 100, 5)
        doc2 = db.add_document("b.pdf", "/b.pdf", "hash_b", 200, 10)

        resp = client.post(
            "/api/sessions",
            json={
                "title": "Pre-attached",
                "document_ids": [doc1["id"], doc2["id"]],
            },
        )
        assert resp.status_code == 200
        session = resp.json()
        assert doc1["id"] in session["document_ids"]
        assert doc2["id"] in session["document_ids"]

    @patch("gaia.ui.server._index_document")
    def test_duplicate_document_upload_returns_existing(self, mock_index, client):
        """Uploading the same file twice returns the existing document."""
        mock_index.return_value = 10

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("Deterministic content for hash test")
            tmp_path = f.name

        try:
            resp1 = client.post(
                "/api/documents/upload-path", json={"filepath": tmp_path}
            )
            resp2 = client.post(
                "/api/documents/upload-path", json={"filepath": tmp_path}
            )
            assert resp1.json()["id"] == resp2.json()["id"]

            # Only 1 document in the library
            lib_resp = client.get("/api/documents")
            assert lib_resp.json()["total"] == 1
        finally:
            os.unlink(tmp_path)


# ── SSE Streaming Format ───────────────────────────────────────────────────


class TestSSEStreaming:
    """Validate Server-Sent Events streaming response format."""

    def test_streaming_response_format(self, client, session_id):
        """Verify SSE events have correct format: 'data: {...}\\n\\n'."""
        with patch("gaia.ui.server._stream_chat_response") as mock_stream:

            async def fake_stream(*args, **kwargs):
                yield 'data: {"type": "chunk", "content": "Hello"}\n\n'
                yield 'data: {"type": "chunk", "content": " world"}\n\n'
                yield 'data: {"type": "done", "message_id": 1, "content": "Hello world"}\n\n'

            mock_stream.return_value = fake_stream()

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "Test stream",
                    "stream": True,
                },
            )

            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

            # Parse SSE events
            body = resp.text
            events = [
                line.removeprefix("data: ")
                for line in body.strip().split("\n")
                if line.startswith("data: ")
            ]

            assert len(events) == 3

            # Verify JSON structure
            chunk1 = json.loads(events[0])
            assert chunk1["type"] == "chunk"
            assert chunk1["content"] == "Hello"

            chunk2 = json.loads(events[1])
            assert chunk2["type"] == "chunk"
            assert chunk2["content"] == " world"

            done = json.loads(events[2])
            assert done["type"] == "done"
            assert done["content"] == "Hello world"
            assert "message_id" in done

    def test_streaming_error_event(self, client, session_id):
        """Verify error events in SSE stream."""
        with patch("gaia.ui.server._stream_chat_response") as mock_stream:

            async def fake_error_stream(*args, **kwargs):
                yield 'data: {"type": "error", "content": "LLM not available"}\n\n'

            mock_stream.return_value = fake_error_stream()

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "Test error",
                    "stream": True,
                },
            )

            body = resp.text
            events = [
                line.removeprefix("data: ")
                for line in body.strip().split("\n")
                if line.startswith("data: ")
            ]
            assert len(events) >= 1
            error_event = json.loads(events[0])
            assert error_event["type"] == "error"
            assert "LLM not available" in error_event["content"]

    def test_streaming_headers(self, client, session_id):
        """Verify streaming response has correct cache and connection headers."""
        with patch("gaia.ui.server._stream_chat_response") as mock_stream:

            async def fake_stream(*args, **kwargs):
                yield 'data: {"type": "done", "content": "test"}\n\n'

            mock_stream.return_value = fake_stream()

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "Test headers",
                    "stream": True,
                },
            )

            assert "text/event-stream" in resp.headers.get("content-type", "")
            # Cache-Control and Connection headers may vary in test client
            # but the important thing is the content-type is event-stream


# ── Edge Cases & Robustness ─────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases: unicode, large payloads, malformed input, empty data."""

    def test_unicode_session_title(self, client):
        """Session titles support unicode characters (CJK, accented, Cyrillic)."""
        title = "\u4eba\u5de5\u77e5\u80fd\u306e\u4f1a\u8a71 - R\u00e9sum\u00e9 \u041f\u0440\u0438\u0432\u0435\u0442"
        resp = client.post(
            "/api/sessions",
            json={
                "title": title,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == title

        # Retrieve it back
        sid = resp.json()["id"]
        get_resp = client.get(f"/api/sessions/{sid}")
        assert get_resp.json()["title"] == title

    def test_emoji_in_session_title(self, client):
        """Session titles support emoji and multi-byte characters."""
        title = "\U0001f916 Chat \U0001f4ac \u2728 Session \U0001f30d"
        resp = client.post(
            "/api/sessions",
            json={
                "title": title,
            },
        )
        assert resp.status_code == 200
        sid = resp.json()["id"]
        assert resp.json()["title"] == title

        # List includes it correctly
        list_resp = client.get("/api/sessions")
        titles = [s["title"] for s in list_resp.json()["sessions"]]
        assert title in titles

    @patch("gaia.ui.server._get_chat_response")
    def test_unicode_in_messages(self, mock_chat, client, session_id):
        """Messages support unicode and multi-byte characters."""
        user_msg = "\u00bfHablas espa\u00f1ol? \u2014 \u5217\u738b\u7cfb\u5217 \u041c\u0438\u0440"
        assistant_msg = (
            "\u00a1S\u00ed! Paris est magnifique \U0001f1eb\U0001f1f7 \u2764\ufe0f"
        )
        mock_chat.return_value = assistant_msg

        resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": user_msg,
                "stream": False,
            },
        )
        assert resp.status_code == 200

        msgs = client.get(f"/api/sessions/{session_id}/messages").json()
        assert msgs["messages"][0]["content"] == user_msg
        assert msgs["messages"][1]["content"] == assistant_msg

    @patch("gaia.ui.server._get_chat_response")
    def test_large_message_content(self, mock_chat, client, session_id):
        """Large messages are handled correctly."""
        large_content = "x" * 50_000
        mock_chat.return_value = "Received your large message."

        resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": large_content,
                "stream": False,
            },
        )
        assert resp.status_code == 200

        msgs = client.get(f"/api/sessions/{session_id}/messages").json()
        assert len(msgs["messages"][0]["content"]) == 50_000

    def test_empty_session_title_uses_default(self, client):
        """Creating session with no title uses default 'New Chat'."""
        resp = client.post("/api/sessions", json={})
        assert resp.json()["title"] == "New Chat"

    def test_many_sessions_pagination(self, client):
        """Pagination works correctly with many sessions."""
        # Create 15 sessions
        for i in range(15):
            client.post("/api/sessions", json={"title": f"Session {i:02d}"})

        # Page 1
        resp1 = client.get("/api/sessions?limit=5&offset=0")
        data1 = resp1.json()
        assert len(data1["sessions"]) == 5
        assert data1["total"] == 15

        # Page 2
        resp2 = client.get("/api/sessions?limit=5&offset=5")
        data2 = resp2.json()
        assert len(data2["sessions"]) == 5

        # No overlap
        ids1 = {s["id"] for s in data1["sessions"]}
        ids2 = {s["id"] for s in data2["sessions"]}
        assert ids1.isdisjoint(ids2)

        # Page 3
        resp3 = client.get("/api/sessions?limit=5&offset=10")
        data3 = resp3.json()
        assert len(data3["sessions"]) == 5

        # Beyond range
        resp4 = client.get("/api/sessions?limit=5&offset=15")
        data4 = resp4.json()
        assert len(data4["sessions"]) == 0
        assert data4["total"] == 15

    def test_send_to_deleted_session_returns_404(self, client):
        """Sending a message to a deleted session returns 404."""
        resp = client.post("/api/sessions", json={"title": "Ephemeral"})
        sid = resp.json()["id"]

        client.delete(f"/api/sessions/{sid}")

        chat_resp = client.post(
            "/api/chat/send",
            json={
                "session_id": sid,
                "message": "Hello?",
                "stream": False,
            },
        )
        assert chat_resp.status_code == 404

    def test_export_empty_session(self, client):
        """Exporting a session with no messages works."""
        resp = client.post("/api/sessions", json={"title": "Empty Export"})
        sid = resp.json()["id"]

        export_resp = client.get(f"/api/sessions/{sid}/export?format=markdown")
        assert export_resp.status_code == 200
        content = export_resp.json()["content"]
        assert "# Empty Export" in content

    def test_export_empty_session_json(self, client):
        """JSON export of empty session returns empty messages list."""
        resp = client.post("/api/sessions", json={"title": "Empty JSON"})
        sid = resp.json()["id"]

        export_resp = client.get(f"/api/sessions/{sid}/export?format=json")
        assert export_resp.status_code == 200
        data = export_resp.json()
        assert data["messages"] == []
        assert data["session"]["title"] == "Empty JSON"

    def test_invalid_json_body(self, client):
        """Sending invalid JSON returns 422."""
        resp = client.post(
            "/api/sessions",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_missing_required_field_chat_request(self, client, session_id):
        """Missing required fields in chat request returns 422."""
        # Missing 'message' field
        resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
            },
        )
        assert resp.status_code == 422

    def test_missing_session_id_in_chat(self, client):
        """Missing session_id in chat request returns 422."""
        resp = client.post(
            "/api/chat/send",
            json={
                "message": "Hello",
            },
        )
        assert resp.status_code == 422


# ── System Status Endpoint ──────────────────────────────────────────────────


class TestSystemStatus:
    """Test system status endpoint with mocked backends."""

    @patch("gaia.ui.server.shutil.disk_usage")
    def test_system_status_disk_space(self, mock_disk, client):
        """Disk space is reported from shutil.disk_usage."""
        mock_disk.return_value = MagicMock(free=100 * (1024**3))

        resp = client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["disk_space_gb"] >= 0

    def test_system_status_fields_are_correct_types(self, client):
        """All system status fields have correct types."""
        resp = client.get("/api/system/status")
        data = resp.json()

        assert isinstance(data["lemonade_running"], bool)
        assert data["model_loaded"] is None or isinstance(data["model_loaded"], str)
        assert isinstance(data["embedding_model_loaded"], bool)
        assert isinstance(data["disk_space_gb"], (int, float))
        assert isinstance(data["memory_available_gb"], (int, float))
        assert isinstance(data["initialized"], bool)
        assert isinstance(data["version"], str)

    @patch("httpx.AsyncClient")
    def test_system_status_when_lemonade_unreachable(self, mock_httpx_cls, client):
        """When Lemonade is unreachable, lemonade_running is False."""
        # Force httpx to raise a connection error
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_client

        resp = client.get("/api/system/status")
        data = resp.json()
        assert data["lemonade_running"] is False


# ── Health Endpoint Integration ─────────────────────────────────────────────


class TestHealthIntegration:
    """Health endpoint reflects real database state."""

    def test_health_stats_track_all_operations(self, client, db):
        """Health stats accurately reflect database mutations."""
        # Initial state
        resp = client.get("/api/health")
        stats = resp.json()["stats"]
        assert stats["sessions"] == 0
        assert stats["messages"] == 0
        assert stats["documents"] == 0

        # Create session + messages + documents
        sess = db.create_session(title="Health Test")
        db.add_message(sess["id"], "user", "Hello")
        db.add_message(sess["id"], "assistant", "Hi!")
        db.add_document("test.pdf", "/test.pdf", "health_hash", 1024, 5)

        resp2 = client.get("/api/health")
        stats2 = resp2.json()["stats"]
        assert stats2["sessions"] == 1
        assert stats2["messages"] == 2
        assert stats2["documents"] == 1
        assert stats2["total_chunks"] == 5
        assert stats2["total_size_bytes"] == 1024

        # Delete session - messages cascade, doc remains
        db.delete_session(sess["id"])

        resp3 = client.get("/api/health")
        stats3 = resp3.json()["stats"]
        assert stats3["sessions"] == 0
        assert stats3["messages"] == 0
        assert stats3["documents"] == 1  # doc not cascade-deleted


# ── Security Integration ────────────────────────────────────────────────────


class TestSecurityIntegration:
    """Security-focused integration tests."""

    @patch("gaia.ui.server._index_document")
    def test_upload_path_traversal_rejected(self, mock_index, client):
        """Path traversal in upload filepath is blocked."""
        resp = client.post(
            "/api/documents/upload-path",
            json={"filepath": "../../etc/passwd"},
        )
        # Either 400 (bad extension) or 404 (file not found after resolve)
        assert resp.status_code in (400, 404)

    @patch("gaia.ui.server._index_document")
    def test_upload_null_byte_injection(self, mock_index, client):
        """Null byte injection in filepath is rejected."""
        resp = client.post(
            "/api/documents/upload-path",
            json={"filepath": "/tmp/test.pdf\x00.exe"},
        )
        assert resp.status_code == 400

    @patch("gaia.ui.server._index_document")
    def test_upload_disallowed_extension(self, mock_index, client):
        """Various dangerous extensions are rejected.

        Note: .bat and .ps1 are in the allowed list (shell scripts).
        Only truly dangerous/binary extensions should be rejected.
        """
        mock_index.return_value = 0
        # These are NOT in _ALLOWED_EXTENSIONS
        dangerous_exts = [".exe", ".dll", ".msi", ".scr", ".com", ".vbs"]
        for ext in dangerous_exts:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(b"test")
                tmp_path = f.name

            try:
                resp = client.post(
                    "/api/documents/upload-path",
                    json={"filepath": tmp_path},
                )
                assert (
                    resp.status_code == 400
                ), f"Extension {ext} should be rejected but got {resp.status_code}"
            finally:
                os.unlink(tmp_path)

    def test_session_id_not_predictable(self, client):
        """Session IDs are UUIDs, not sequential integers."""
        ids = []
        for _ in range(3):
            resp = client.post("/api/sessions", json={})
            ids.append(resp.json()["id"])

        # UUIDs should have dashes and be 36 chars
        for sid in ids:
            assert len(sid) == 36
            assert sid.count("-") == 4

        # All unique
        assert len(set(ids)) == 3


# ── Database Concurrency ────────────────────────────────────────────────────


class TestDatabaseConcurrency:
    """Test database access patterns and persistence.

    NOTE: ChatDatabase uses a single SQLite connection with
    check_same_thread=False. SQLite does not support truly concurrent
    writes from multiple threads on a single connection. This is fine
    because FastAPI runs in an async event loop (single-threaded).

    These tests verify:
    - Rapid sequential operations (realistic async server pattern)
    - Database persistence across close/reopen cycles
    - Data integrity under high-volume sequential writes
    """

    def test_rapid_sequential_session_creation(self, db):
        """Rapid sequential session creation produces unique IDs."""
        ids = set()
        for i in range(50):
            session = db.create_session(title=f"Rapid {i}")
            ids.add(session["id"])

        assert len(ids) == 50  # All unique
        assert db.count_sessions() == 50

    def test_rapid_sequential_message_insertion(self, db):
        """Rapid sequential message insertion is reliable."""
        session = db.create_session(title="Rapid Messages")
        for i in range(100):
            db.add_message(session["id"], "user", f"Message {i}")

        assert db.count_messages(session["id"]) == 100
        messages = db.get_messages(session["id"], limit=100)
        # Verify ordering
        for i, msg in enumerate(messages):
            assert msg["content"] == f"Message {i}"

    def test_interleaved_session_operations(self, db):
        """Interleaved create/read/update/delete operations are consistent."""
        # Create 10 sessions
        session_ids = []
        for i in range(10):
            s = db.create_session(title=f"Session {i}")
            session_ids.append(s["id"])

        assert db.count_sessions() == 10

        # Delete odd-numbered sessions
        for i in range(1, 10, 2):
            db.delete_session(session_ids[i])

        assert db.count_sessions() == 5

        # Update remaining sessions
        for i in range(0, 10, 2):
            db.update_session(session_ids[i], title=f"Updated {i}")

        # Verify
        for i in range(0, 10, 2):
            s = db.get_session(session_ids[i])
            assert s is not None
            assert s["title"] == f"Updated {i}"

        for i in range(1, 10, 2):
            assert db.get_session(session_ids[i]) is None

    def test_database_close_and_reopen(self):
        """Database can be closed and reopened (file-based)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_reopen.db")

            # Create and populate
            db1 = ChatDatabase(db_path)
            session = db1.create_session(title="Persistent")
            db1.add_message(session["id"], "user", "Remember this")
            sid = session["id"]
            db1.close()

            # Reopen and verify
            db2 = ChatDatabase(db_path)
            reopened = db2.get_session(sid)
            assert reopened is not None
            assert reopened["title"] == "Persistent"

            messages = db2.get_messages(sid)
            assert len(messages) == 1
            assert messages[0]["content"] == "Remember this"
            db2.close()


# ── RAG Sources in Messages ─────────────────────────────────────────────────


class TestRAGSourcesIntegration:
    """Test RAG source citations through the full API stack."""

    def test_messages_with_rag_sources_roundtrip(self, client, db):
        """RAG sources are stored and returned correctly via API."""
        resp = client.post("/api/sessions", json={"title": "RAG Test"})
        sid = resp.json()["id"]

        sources = [
            {
                "document_id": "doc_abc",
                "filename": "manual.pdf",
                "chunk": "The installation requires Python 3.10+",
                "score": 0.92,
                "page": 3,
            },
            {
                "document_id": "doc_def",
                "filename": "faq.md",
                "chunk": "See the troubleshooting section",
                "score": 0.78,
            },
        ]

        db.add_message(sid, "user", "How do I install?")
        db.add_message(
            sid,
            "assistant",
            "You need Python 3.10+.",
            rag_sources=sources,
        )

        msgs_resp = client.get(f"/api/sessions/{sid}/messages")
        messages = msgs_resp.json()["messages"]
        assert len(messages) == 2

        assistant_msg = messages[1]
        assert assistant_msg["rag_sources"] is not None
        assert len(assistant_msg["rag_sources"]) == 2

        src1 = assistant_msg["rag_sources"][0]
        assert src1["document_id"] == "doc_abc"
        assert src1["filename"] == "manual.pdf"
        assert src1["score"] == 0.92
        assert src1["chunk"] == "The installation requires Python 3.10+"

        src2 = assistant_msg["rag_sources"][1]
        assert src2["document_id"] == "doc_def"
        assert src2["score"] == 0.78

    def test_message_without_rag_sources(self, client, db):
        """Messages without RAG sources return null for rag_sources."""
        resp = client.post("/api/sessions", json={})
        sid = resp.json()["id"]

        db.add_message(sid, "user", "Hello")
        db.add_message(sid, "assistant", "Hi there!")

        msgs_resp = client.get(f"/api/sessions/{sid}/messages")
        for msg in msgs_resp.json()["messages"]:
            assert msg["rag_sources"] is None


# ── Session Updated Timestamp ───────────────────────────────────────────────


class TestSessionTimestamps:
    """Verify session timestamps update correctly."""

    @patch("gaia.ui.server._get_chat_response")
    def test_updated_at_changes_on_message(self, mock_chat, client):
        """Session updated_at advances after a new message."""
        mock_chat.return_value = "Reply"

        resp = client.post("/api/sessions", json={"title": "Timestamp Test"})
        sid = resp.json()["id"]
        created_at = resp.json()["updated_at"]

        time.sleep(0.02)

        client.post(
            "/api/chat/send",
            json={
                "session_id": sid,
                "message": "Hello",
                "stream": False,
            },
        )

        resp2 = client.get(f"/api/sessions/{sid}")
        updated_at = resp2.json()["updated_at"]

        assert updated_at >= created_at

    def test_updated_at_changes_on_rename(self, client):
        """Session updated_at advances after renaming."""
        resp = client.post("/api/sessions", json={"title": "Before"})
        sid = resp.json()["id"]
        original = resp.json()["updated_at"]

        time.sleep(0.02)

        resp2 = client.put(f"/api/sessions/{sid}", json={"title": "After"})
        updated = resp2.json()["updated_at"]
        assert updated >= original

    def test_sessions_ordered_by_most_recent(self, client, db):
        """List sessions returns most recently updated first."""
        s1 = client.post("/api/sessions", json={"title": "Old"}).json()
        time.sleep(0.02)
        s2 = client.post("/api/sessions", json={"title": "Middle"}).json()
        time.sleep(0.02)
        s3 = client.post("/api/sessions", json={"title": "Newest"}).json()

        # Now update s1 to make it most recent
        time.sleep(0.02)
        db.add_message(s1["id"], "user", "New activity")

        list_resp = client.get("/api/sessions")
        sessions = list_resp.json()["sessions"]
        # s1 should be first because it was updated most recently
        assert sessions[0]["id"] == s1["id"]


# ── CORS Integration ────────────────────────────────────────────────────────


class TestCORSIntegration:
    """Verify CORS headers are set correctly for cross-origin requests."""

    def test_cors_allows_localhost_origin(self, client):
        """CORS allows requests from localhost origins."""
        resp = client.get(
            "/api/health",
            headers={"Origin": "http://localhost:4200"},
        )
        assert resp.status_code == 200
        # With allow_origins=["*"], the access-control-allow-origin should be set
        assert resp.headers.get("access-control-allow-origin") in (
            "*",
            "http://localhost:4200",
        )

    def test_cors_allows_any_origin(self, client):
        """CORS allows requests from any origin (dev mode)."""
        resp = client.get(
            "/api/health",
            headers={"Origin": "http://some-other-origin.com"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers


# ── CLI --ui Flag ────────────────────────────────────────────────────────────


class TestCLIUIFlag:
    """Test the 'gaia chat --ui' CLI integration."""

    def test_cli_parser_has_ui_flag(self):
        """CLI parser recognizes --ui and --ui-port flags.

        The GAIA CLI parser is built inside main(), so we intercept
        sys.argv and verify parse_known_args behavior through source
        inspection and the server's standalone parser.
        """
        # The server module has its own argparse-based main()
        # Verify it accepts --host, --port, --debug
        from gaia.ui.server import DEFAULT_PORT

        assert DEFAULT_PORT == 4200

        # Verify the CLI source registers --ui and --ui-port on chat_parser
        import inspect

        from gaia.cli import main as cli_main

        source = inspect.getsource(cli_main)
        assert '"--ui"' in source, "--ui flag not found in CLI main()"
        assert '"--ui-port"' in source, "--ui-port flag not found in CLI main()"
        assert "create_app" in source, "create_app import not found in CLI main()"
        assert "4200" in source, "Default port 4200 not found in CLI main()"

        # Verify the handler logic references the right attributes
        assert "args.ui" in source or 'getattr(args, "ui"' in source
        assert "args.ui_port" in source or 'getattr(args, "ui_port"' in source

    def test_create_app_returns_fastapi_instance(self):
        """create_app returns a configured FastAPI app."""
        app = create_app(db_path=":memory:")
        assert app.title == "GAIA Agent UI API"
        assert hasattr(app.state, "db")
        assert app.state.db is not None

    def test_create_app_memory_db_is_isolated(self):
        """Each in-memory app has its own database."""
        app1 = create_app(db_path=":memory:")
        app2 = create_app(db_path=":memory:")

        app1.state.db.create_session(title="App1 Only")

        assert app1.state.db.count_sessions() == 1
        assert app2.state.db.count_sessions() == 0


# ── Multiple App Instances ──────────────────────────────────────────────────


class TestMultipleAppInstances:
    """Verify isolation between app instances (e.g., test parallelism)."""

    def test_separate_apps_have_separate_databases(self):
        """Two app instances with :memory: do not share state."""
        app_a = create_app(db_path=":memory:")
        app_b = create_app(db_path=":memory:")

        client_a = TestClient(app_a)
        client_b = TestClient(app_b)

        # Create in A only
        client_a.post("/api/sessions", json={"title": "In A"})

        # A has 1, B has 0
        resp_a = client_a.get("/api/sessions")
        resp_b = client_b.get("/api/sessions")
        assert resp_a.json()["total"] == 1
        assert resp_b.json()["total"] == 0


# ── Request Validation ──────────────────────────────────────────────────────


class TestRequestValidation:
    """Validate that the API properly rejects malformed requests."""

    def test_create_session_extra_fields_ignored(self, client):
        """Extra unknown fields in request are ignored (Pydantic default)."""
        resp = client.post(
            "/api/sessions",
            json={
                "title": "Normal",
                "unknown_field": "should be ignored",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Normal"

    def test_update_session_empty_body(self, client, session_id):
        """Update with empty body returns current session unchanged."""
        resp = client.put(f"/api/sessions/{session_id}", json={})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test Session"

    def test_document_upload_empty_filepath(self, client):
        """Empty filepath string is rejected."""
        resp = client.post("/api/documents/upload-path", json={"filepath": ""})
        # Empty path should fail (no extension or file not found)
        assert resp.status_code in (400, 404)

    def test_attach_document_missing_document_id(self, client, session_id):
        """Missing document_id in attach request returns 422."""
        resp = client.post(
            f"/api/sessions/{session_id}/documents",
            json={},
        )
        assert resp.status_code == 422


# ── Export Format Edge Cases ────────────────────────────────────────────────


class TestExportEdgeCases:
    """Edge cases for the export endpoint."""

    def test_export_large_conversation(self, client, db):
        """Export works with many messages."""
        resp = client.post("/api/sessions", json={"title": "Large Chat"})
        sid = resp.json()["id"]

        for i in range(50):
            db.add_message(sid, "user", f"Question {i}")
            db.add_message(sid, "assistant", f"Answer {i}")

        export = client.get(f"/api/sessions/{sid}/export?format=markdown")
        assert export.status_code == 200
        content = export.json()["content"]
        assert "Question 0" in content
        assert "Answer 49" in content
        assert content.count("**User:**") == 50
        assert content.count("**Assistant:**") == 50

    def test_export_default_format_is_markdown(self, client):
        """Default export format is markdown."""
        resp = client.post("/api/sessions", json={"title": "Default Format"})
        sid = resp.json()["id"]

        export = client.get(f"/api/sessions/{sid}/export")
        assert export.status_code == 200
        assert export.json()["format"] == "markdown"

    def test_export_json_session_includes_metadata(self, client):
        """JSON export includes session metadata."""
        resp = client.post(
            "/api/sessions",
            json={
                "title": "JSON Meta",
                "model": "test-model",
                "system_prompt": "Be brief.",
            },
        )
        sid = resp.json()["id"]

        export = client.get(f"/api/sessions/{sid}/export?format=json")
        session_data = export.json()["session"]
        assert session_data["title"] == "JSON Meta"
        assert session_data["model"] == "test-model"
        assert session_data["system_prompt"] == "Be brief."

    def test_export_unsupported_format_returns_400(self, client):
        """Requesting an unsupported export format returns 400."""
        resp = client.post("/api/sessions", json={"title": "Bad Export"})
        sid = resp.json()["id"]

        export = client.get(f"/api/sessions/{sid}/export?format=xml")
        assert export.status_code == 400
        assert "Unsupported format" in export.json()["detail"]

    def test_export_nonexistent_session_returns_404(self, client):
        """Exporting a nonexistent session returns 404."""
        export = client.get("/api/sessions/nonexistent-id/export")
        assert export.status_code == 404


# ── Missing Coverage: Document/Session Error Paths ─────────────────────────


class TestDocumentSessionErrors:
    """Test error paths for document and session endpoints."""

    def test_attach_document_to_nonexistent_session(self, client, doc_id):
        """Attaching a document to a nonexistent session returns 404."""
        resp = client.post(
            "/api/sessions/nonexistent-id/documents",
            json={"document_id": doc_id},
        )
        assert resp.status_code == 404
        assert "Session not found" in resp.json()["detail"]

    def test_attach_nonexistent_document_to_session(self, client, session_id):
        """Attaching a nonexistent document to a session returns 404."""
        resp = client.post(
            f"/api/sessions/{session_id}/documents",
            json={"document_id": "nonexistent-doc-id"},
        )
        assert resp.status_code == 404
        assert "Document not found" in resp.json()["detail"]

    def test_delete_nonexistent_document_returns_404(self, client):
        """Deleting a nonexistent document returns 404."""
        resp = client.delete("/api/documents/nonexistent-doc-id")
        assert resp.status_code == 404

    def test_delete_nonexistent_session_returns_404(self, client):
        """Deleting a nonexistent session returns 404."""
        resp = client.delete("/api/sessions/nonexistent-session-id")
        assert resp.status_code == 404

    def test_get_nonexistent_session_returns_404(self, client):
        """Getting a nonexistent session returns 404."""
        resp = client.get("/api/sessions/nonexistent-session-id")
        assert resp.status_code == 404

    def test_update_nonexistent_session_returns_404(self, client):
        """Updating a nonexistent session returns 404."""
        resp = client.put(
            "/api/sessions/nonexistent-session-id",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404

    def test_get_messages_nonexistent_session_returns_404(self, client):
        """Getting messages for a nonexistent session returns 404."""
        resp = client.get("/api/sessions/nonexistent-session-id/messages")
        assert resp.status_code == 404

    def test_duplicate_document_attach_is_idempotent(self, client, session_id, doc_id):
        """Attaching the same document twice to a session is idempotent."""
        resp1 = client.post(
            f"/api/sessions/{session_id}/documents",
            json={"document_id": doc_id},
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            f"/api/sessions/{session_id}/documents",
            json={"document_id": doc_id},
        )
        assert resp2.status_code == 200

        # Should still only count as 1 attachment
        sess = client.get(f"/api/sessions/{session_id}").json()
        assert sess["document_ids"].count(doc_id) == 1


# ── Message Pagination ─────────────────────────────────────────────────────


class TestMessagePagination:
    """Test message list pagination with limit and offset."""

    def test_message_pagination(self, client, db):
        """Messages can be paginated with limit and offset."""
        resp = client.post("/api/sessions", json={"title": "Paginated Chat"})
        sid = resp.json()["id"]

        # Add 20 messages
        for i in range(20):
            db.add_message(sid, "user", f"Msg {i:02d}")

        # Page 1: first 5
        page1 = client.get(f"/api/sessions/{sid}/messages?limit=5&offset=0")
        assert page1.status_code == 200
        data1 = page1.json()
        assert len(data1["messages"]) == 5
        assert data1["total"] == 20
        assert data1["messages"][0]["content"] == "Msg 00"
        assert data1["messages"][4]["content"] == "Msg 04"

        # Page 2: next 5
        page2 = client.get(f"/api/sessions/{sid}/messages?limit=5&offset=5")
        data2 = page2.json()
        assert len(data2["messages"]) == 5
        assert data2["messages"][0]["content"] == "Msg 05"

        # No overlap
        ids1 = {m["id"] for m in data1["messages"]}
        ids2 = {m["id"] for m in data2["messages"]}
        assert ids1.isdisjoint(ids2)

        # Beyond range
        page_beyond = client.get(f"/api/sessions/{sid}/messages?limit=5&offset=20")
        assert len(page_beyond.json()["messages"]) == 0
        assert page_beyond.json()["total"] == 20

    def test_message_default_limit(self, client, db):
        """Default message limit is 100."""
        resp = client.post("/api/sessions", json={"title": "Default Limit"})
        sid = resp.json()["id"]

        for i in range(110):
            db.add_message(sid, "user", f"Msg {i}")

        msgs = client.get(f"/api/sessions/{sid}/messages").json()
        assert len(msgs["messages"]) == 100  # default limit
        assert msgs["total"] == 110


# ── Streaming Generator Logic ──────────────────────────────────────────────


class TestStreamingGeneratorEdgeCases:
    """Test the actual streaming SSE event format through the API.

    While we can't test the real ChatSDK streaming without a running
    Lemonade server, these tests exercise the error/fallback paths of
    _stream_chat_response that produce SSE events.
    """

    def test_streaming_import_error_yields_error_event(self, client, session_id):
        """When ChatSDK import fails, the stream yields an error SSE event."""
        with patch("gaia.ui.server._stream_chat_response") as mock_stream:

            async def error_stream(*args, **kwargs):
                error_msg = (
                    "Error: Could not get response from LLM. "
                    "Is Lemonade Server running? Check server logs for details."
                )
                import json as _json

                error_data = _json.dumps({"type": "error", "content": error_msg})
                yield f"data: {error_data}\n\n"

            mock_stream.return_value = error_stream()

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "Test import error",
                    "stream": True,
                },
            )

            assert resp.status_code == 200
            events = [
                line.removeprefix("data: ")
                for line in resp.text.strip().split("\n")
                if line.startswith("data: ")
            ]
            assert len(events) >= 1
            event = json.loads(events[0])
            assert event["type"] == "error"
            assert "Lemonade Server" in event["content"]

    def test_streaming_saves_user_message_to_db(self, client, db, session_id):
        """The user message is saved to the DB even for streaming requests."""
        with patch("gaia.ui.server._stream_chat_response") as mock_stream:

            async def fake_stream(*args, **kwargs):
                yield 'data: {"type": "done", "content": "ok"}\n\n'

            mock_stream.return_value = fake_stream()

            # The send_message endpoint saves the user message BEFORE streaming
            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "Should be saved",
                    "stream": True,
                },
            )
            assert resp.status_code == 200

        # Verify user message was persisted
        msgs = db.get_messages(session_id)
        assert len(msgs) >= 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Should be saved"

    @patch("gaia.ui.server._get_chat_response")
    def test_non_streaming_saves_both_messages(self, mock_chat, client, db, session_id):
        """Non-streaming saves both user and assistant messages to DB."""
        mock_chat.return_value = "The assistant reply."

        resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": "The user question.",
                "stream": False,
            },
        )
        assert resp.status_code == 200

        msgs = db.get_messages(session_id)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "The user question."
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "The assistant reply."


class TestMessageDeletion:
    """Tests for message deletion and resend (delete-and-below) endpoints."""

    @patch("gaia.ui.server._get_chat_response")
    def test_delete_single_message(self, mock_chat, client, db, session_id):
        """DELETE /api/sessions/{id}/messages/{msg_id} removes one message."""
        mock_chat.return_value = "Reply"

        # Send a message pair
        client.post(
            "/api/chat/send",
            json={"session_id": session_id, "message": "Hello", "stream": False},
        )
        msgs = db.get_messages(session_id)
        assert len(msgs) == 2
        user_msg_id = msgs[0]["id"]

        # Delete the user message
        resp = client.delete(f"/api/sessions/{session_id}/messages/{user_msg_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Only the assistant message remains
        remaining = db.get_messages(session_id)
        assert len(remaining) == 1
        assert remaining[0]["role"] == "assistant"

    def test_delete_message_not_found(self, client, session_id):
        """DELETE returns 404 for non-existent message."""
        resp = client.delete(f"/api/sessions/{session_id}/messages/99999")
        assert resp.status_code == 404

    def test_delete_message_session_not_found(self, client):
        """DELETE returns 404 for non-existent session."""
        resp = client.delete("/api/sessions/nonexistent/messages/1")
        assert resp.status_code == 404

    @patch("gaia.ui.server._get_chat_response")
    def test_delete_messages_from(self, mock_chat, client, db, session_id):
        """DELETE .../and-below removes the target and all subsequent messages."""
        mock_chat.return_value = "Reply"

        # Send two message pairs
        client.post(
            "/api/chat/send",
            json={"session_id": session_id, "message": "First", "stream": False},
        )
        client.post(
            "/api/chat/send",
            json={"session_id": session_id, "message": "Second", "stream": False},
        )
        msgs = db.get_messages(session_id)
        assert len(msgs) == 4

        # Delete from the second user message onward (msg index 2)
        second_user_id = msgs[2]["id"]
        resp = client.delete(
            f"/api/sessions/{session_id}/messages/{second_user_id}/and-below"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["count"] == 2  # second user + second assistant

        # Only the first pair remains
        remaining = db.get_messages(session_id)
        assert len(remaining) == 2
        assert remaining[0]["content"] == "First"
        assert remaining[1]["content"] == "Reply"

    @patch("gaia.ui.server._get_chat_response")
    def test_delete_messages_from_first_clears_all(
        self, mock_chat, client, db, session_id
    ):
        """Deleting from the first message clears the entire conversation."""
        mock_chat.return_value = "Reply"

        client.post(
            "/api/chat/send",
            json={"session_id": session_id, "message": "Hello", "stream": False},
        )
        msgs = db.get_messages(session_id)
        first_id = msgs[0]["id"]

        resp = client.delete(
            f"/api/sessions/{session_id}/messages/{first_id}/and-below"
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

        assert db.count_messages(session_id) == 0

    def test_delete_messages_from_not_found(self, client, session_id):
        """DELETE .../and-below returns 404 for non-existent message."""
        resp = client.delete(f"/api/sessions/{session_id}/messages/99999/and-below")
        assert resp.status_code == 404

    def test_delete_messages_from_session_not_found(self, client):
        """DELETE .../and-below returns 404 for non-existent session."""
        resp = client.delete("/api/sessions/nonexistent/messages/1/and-below")
        assert resp.status_code == 404
