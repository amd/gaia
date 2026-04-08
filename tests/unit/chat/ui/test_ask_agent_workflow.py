# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the Ask Agent workflow: index → attach → chat with document context.

Validates that when documents are indexed and attached to a session,
the chat endpoint passes those document_ids to _get_chat_response,
enabling RAG-powered answers about the file content.
"""

import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

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


class TestAskAgentWorkflow:
    """Tests that the index → attach → chat pipeline passes document context."""

    @patch("gaia.ui.server._get_chat_response")
    def test_upload_attach_then_chat_has_document_context(self, mock_chat, client, db):
        """Core test: attached documents appear in session.document_ids
        when _get_chat_response is called."""
        mock_chat.return_value = "Here is my analysis of the document."

        # 1. Create session
        session_resp = client.post("/api/sessions", json={})
        session_id = session_resp.json()["id"]

        # 2. Add a document directly via DB (simulates successful upload)
        doc = db.add_document("report.pdf", "/home/user/report.pdf", "abc123hash")

        # 3. Attach document to session
        attach_resp = client.post(
            f"/api/sessions/{session_id}/documents",
            json={"document_id": doc["id"]},
        )
        assert attach_resp.status_code == 200

        # 4. Send chat message
        chat_resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": "Please analyze this document for me: report.pdf",
                "stream": False,
            },
        )
        assert chat_resp.status_code == 200

        # 5. Verify _get_chat_response received session with document_ids
        mock_chat.assert_called_once()
        call_args = mock_chat.call_args
        session_arg = call_args[0][1]  # second positional arg is session dict
        assert doc["id"] in session_arg.get("document_ids", [])

    @patch("gaia.ui.server._get_chat_response")
    def test_chat_without_documents_has_no_rag_context(self, mock_chat, client, db):
        """Chat without attached docs passes empty document_ids."""
        mock_chat.return_value = "I have no document context."

        session_resp = client.post("/api/sessions", json={})
        session_id = session_resp.json()["id"]

        chat_resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": "Tell me about the document",
                "stream": False,
            },
        )
        assert chat_resp.status_code == 200

        mock_chat.assert_called_once()
        session_arg = mock_chat.call_args[0][1]
        assert session_arg.get("document_ids", []) == []

    @patch("gaia.ui.server._get_chat_response")
    def test_upload_multiple_attach_then_chat(self, mock_chat, client, db):
        """Multiple attached documents all appear in session.document_ids."""
        mock_chat.return_value = "Analysis of both documents."

        session_resp = client.post("/api/sessions", json={})
        session_id = session_resp.json()["id"]

        doc1 = db.add_document("report.pdf", "/home/user/report.pdf", "hash1")
        doc2 = db.add_document("notes.md", "/home/user/notes.md", "hash2")

        client.post(
            f"/api/sessions/{session_id}/documents",
            json={"document_id": doc1["id"]},
        )
        client.post(
            f"/api/sessions/{session_id}/documents",
            json={"document_id": doc2["id"]},
        )

        chat_resp = client.post(
            "/api/chat/send",
            json={
                "session_id": session_id,
                "message": "Analyze these documents",
                "stream": False,
            },
        )
        assert chat_resp.status_code == 200

        session_arg = mock_chat.call_args[0][1]
        doc_ids = session_arg.get("document_ids", [])
        assert doc1["id"] in doc_ids
        assert doc2["id"] in doc_ids
