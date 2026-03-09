# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for GAIA Chat UI Pydantic models.

Tests model validation, defaults, and serialization.
"""

from gaia.ui.models import (
    AttachDocumentRequest,
    ChatRequest,
    ChatResponse,
    CreateSessionRequest,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadRequest,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
    SourceInfo,
    SystemStatus,
    UpdateSessionRequest,
)


class TestSystemStatus:
    """Tests for SystemStatus model."""

    def test_defaults(self):
        status = SystemStatus()
        assert status.lemonade_running is False
        assert status.model_loaded is None
        assert status.embedding_model_loaded is False
        assert status.disk_space_gb == 0.0
        assert status.memory_available_gb == 0.0
        assert status.initialized is False
        assert status.version == "0.1.0"

    def test_custom_values(self):
        status = SystemStatus(
            lemonade_running=True,
            model_loaded="Qwen3-0.6B",
            embedding_model_loaded=True,
            disk_space_gb=50.5,
            memory_available_gb=16.0,
            initialized=True,
        )
        assert status.lemonade_running is True
        assert status.model_loaded == "Qwen3-0.6B"
        assert status.disk_space_gb == 50.5

    def test_serialization(self):
        status = SystemStatus(lemonade_running=True, model_loaded="test")
        data = status.model_dump()
        assert isinstance(data, dict)
        assert data["lemonade_running"] is True
        assert data["model_loaded"] == "test"


class TestCreateSessionRequest:
    """Tests for CreateSessionRequest model."""

    def test_all_optional(self):
        request = CreateSessionRequest()
        assert request.title is None
        assert request.model is None
        assert request.system_prompt is None
        assert request.document_ids == []

    def test_with_values(self):
        request = CreateSessionRequest(
            title="My Chat",
            model="Qwen3-0.6B",
            system_prompt="You are helpful.",
            document_ids=["doc1", "doc2"],
        )
        assert request.title == "My Chat"
        assert request.model == "Qwen3-0.6B"
        assert len(request.document_ids) == 2


class TestUpdateSessionRequest:
    """Tests for UpdateSessionRequest model."""

    def test_all_optional(self):
        request = UpdateSessionRequest()
        assert request.title is None
        assert request.system_prompt is None

    def test_title_only(self):
        request = UpdateSessionRequest(title="New Title")
        assert request.title == "New Title"
        assert request.system_prompt is None


class TestSessionResponse:
    """Tests for SessionResponse model."""

    def test_required_fields(self):
        session = SessionResponse(
            id="abc123",
            title="Test",
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
            model="Qwen3-0.6B",
        )
        assert session.id == "abc123"
        assert session.title == "Test"
        assert session.model == "Qwen3-0.6B"

    def test_defaults(self):
        session = SessionResponse(
            id="abc",
            title="Test",
            created_at="now",
            updated_at="now",
            model="model",
        )
        assert session.system_prompt is None
        assert session.message_count == 0
        assert session.document_ids == []

    def test_with_documents(self):
        session = SessionResponse(
            id="abc",
            title="Test",
            created_at="now",
            updated_at="now",
            model="model",
            document_ids=["doc1", "doc2"],
        )
        assert len(session.document_ids) == 2


class TestSessionListResponse:
    """Tests for SessionListResponse model."""

    def test_empty_list(self):
        resp = SessionListResponse(sessions=[], total=0)
        assert resp.sessions == []
        assert resp.total == 0

    def test_with_sessions(self):
        sessions = [
            SessionResponse(
                id=f"s{i}",
                title=f"Session {i}",
                created_at="now",
                updated_at="now",
                model="m",
            )
            for i in range(3)
        ]
        resp = SessionListResponse(sessions=sessions, total=3)
        assert len(resp.sessions) == 3
        assert resp.total == 3


class TestChatRequest:
    """Tests for ChatRequest model."""

    def test_required_fields(self):
        request = ChatRequest(
            session_id="abc",
            message="Hello",
        )
        assert request.session_id == "abc"
        assert request.message == "Hello"
        assert request.stream is True  # Default
        assert request.document_ids is None

    def test_non_streaming(self):
        request = ChatRequest(
            session_id="abc",
            message="Hello",
            stream=False,
        )
        assert request.stream is False

    def test_with_document_ids(self):
        request = ChatRequest(
            session_id="abc",
            message="What's in this doc?",
            document_ids=["doc1"],
        )
        assert request.document_ids == ["doc1"]


class TestSourceInfo:
    """Tests for SourceInfo model."""

    def test_required_fields(self):
        source = SourceInfo(
            document_id="doc1",
            filename="test.pdf",
            chunk="Some relevant text...",
            score=0.85,
        )
        assert source.document_id == "doc1"
        assert source.filename == "test.pdf"
        assert source.score == 0.85
        assert source.page is None

    def test_with_page(self):
        source = SourceInfo(
            document_id="doc1",
            filename="test.pdf",
            chunk="text",
            score=0.9,
            page=12,
        )
        assert source.page == 12


class TestChatResponse:
    """Tests for ChatResponse model."""

    def test_required_fields(self):
        resp = ChatResponse(
            message_id=1,
            content="Hello there!",
        )
        assert resp.message_id == 1
        assert resp.content == "Hello there!"
        assert resp.sources == []
        assert resp.tokens is None

    def test_with_sources_and_tokens(self):
        sources = [
            SourceInfo(
                document_id="doc1",
                filename="test.pdf",
                chunk="text",
                score=0.8,
            )
        ]
        resp = ChatResponse(
            message_id=1,
            content="Response",
            sources=sources,
            tokens={"prompt": 100, "completion": 50},
        )
        assert len(resp.sources) == 1
        assert resp.tokens["prompt"] == 100


class TestMessageResponse:
    """Tests for MessageResponse model."""

    def test_required_fields(self):
        msg = MessageResponse(
            id=1,
            session_id="abc",
            role="user",
            content="Hello",
            created_at="2025-01-01T00:00:00Z",
        )
        assert msg.id == 1
        assert msg.role == "user"
        assert msg.rag_sources is None

    def test_with_rag_sources(self):
        sources = [
            SourceInfo(
                document_id="doc1",
                filename="test.pdf",
                chunk="text",
                score=0.9,
            )
        ]
        msg = MessageResponse(
            id=1,
            session_id="abc",
            role="assistant",
            content="Response",
            created_at="now",
            rag_sources=sources,
        )
        assert len(msg.rag_sources) == 1


class TestMessageListResponse:
    """Tests for MessageListResponse model."""

    def test_empty(self):
        resp = MessageListResponse(messages=[], total=0)
        assert resp.messages == []
        assert resp.total == 0


class TestDocumentResponse:
    """Tests for DocumentResponse model."""

    def test_required_fields(self):
        doc = DocumentResponse(
            id="doc1",
            filename="test.pdf",
            filepath="/path/test.pdf",
            file_size=1024,
            chunk_count=5,
            indexed_at="2025-01-01T00:00:00Z",
        )
        assert doc.id == "doc1"
        assert doc.filename == "test.pdf"
        assert doc.file_size == 1024
        assert doc.last_accessed_at is None
        assert doc.sessions_using == 0


class TestDocumentListResponse:
    """Tests for DocumentListResponse model."""

    def test_empty(self):
        resp = DocumentListResponse(
            documents=[],
            total=0,
            total_size_bytes=0,
            total_chunks=0,
        )
        assert resp.documents == []
        assert resp.total_size_bytes == 0

    def test_with_documents(self):
        docs = [
            DocumentResponse(
                id=f"doc{i}",
                filename=f"file{i}.pdf",
                filepath=f"/path/{i}",
                file_size=1000,
                chunk_count=5,
                indexed_at="now",
            )
            for i in range(3)
        ]
        resp = DocumentListResponse(
            documents=docs,
            total=3,
            total_size_bytes=3000,
            total_chunks=15,
        )
        assert resp.total == 3
        assert resp.total_size_bytes == 3000
        assert resp.total_chunks == 15


class TestDocumentUploadRequest:
    """Tests for DocumentUploadRequest model."""

    def test_filepath_required(self):
        request = DocumentUploadRequest(filepath="/path/to/doc.pdf")
        assert request.filepath == "/path/to/doc.pdf"


class TestAttachDocumentRequest:
    """Tests for AttachDocumentRequest model."""

    def test_document_id_required(self):
        request = AttachDocumentRequest(document_id="doc123")
        assert request.document_id == "doc123"
