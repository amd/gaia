# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for GAIA Agent UI utility helpers.

Tests data-conversion functions and filesystem helpers in gaia.ui.utils:
- format_size: human-readable byte formatting
- session_to_response: session dict -> SessionResponse
- message_to_response: message dict -> MessageResponse (with JSON parsing)
- doc_to_response: document dict -> DocumentResponse
- ensure_within_home: home directory security guard
"""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from gaia.ui.utils import (
    doc_to_response,
    ensure_within_home,
    format_size,
    message_to_response,
    session_to_response,
)

# ── format_size ───────────────────────────────────────────────────────────


class TestFormatSize:
    """Tests for format_size()."""

    def test_zero_bytes(self):
        assert format_size(0) == "0 B"

    def test_negative_bytes(self):
        assert format_size(-1) == "0 B"

    def test_one_byte(self):
        assert format_size(1) == "1.0 B"

    def test_bytes_range(self):
        assert format_size(512) == "512.0 B"

    def test_one_kb(self):
        assert format_size(1024) == "1.0 KB"

    def test_kilobytes(self):
        result = format_size(1536)  # 1.5 KB
        assert result == "1.5 KB"

    def test_one_mb(self):
        assert format_size(1024 * 1024) == "1.0 MB"

    def test_megabytes(self):
        result = format_size(5 * 1024 * 1024)  # 5 MB
        assert result == "5.0 MB"

    def test_one_gb(self):
        assert format_size(1024**3) == "1.0 GB"

    def test_one_tb(self):
        assert format_size(1024**4) == "1.0 TB"

    def test_large_tb_does_not_overflow(self):
        """Values beyond TB stay in TB units (the largest unit)."""
        result = format_size(1024**5)  # 1 PB = 1024 TB
        assert "TB" in result

    def test_typical_document_size(self):
        result = format_size(2_500_000)  # ~2.4 MB
        assert "MB" in result

    def test_exact_boundary_1024(self):
        """Exactly 1024 bytes should round to 1.0 KB."""
        assert format_size(1024) == "1.0 KB"

    def test_just_under_1024(self):
        result = format_size(1023)
        assert "B" in result
        assert "KB" not in result


# ── session_to_response ───────────────────────────────────────────────────


class TestSessionToResponse:
    """Tests for session_to_response()."""

    @pytest.fixture
    def full_session(self):
        return {
            "id": "sess-123",
            "title": "Test Chat",
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:01",
            "model": "Qwen3.5-35B",
            "system_prompt": "You are helpful.",
            "message_count": 5,
            "document_ids": ["doc1", "doc2"],
        }

    def test_full_session(self, full_session):
        resp = session_to_response(full_session)
        assert resp.id == "sess-123"
        assert resp.title == "Test Chat"
        assert resp.model == "Qwen3.5-35B"
        assert resp.system_prompt == "You are helpful."
        assert resp.message_count == 5
        assert resp.document_ids == ["doc1", "doc2"]

    def test_missing_optional_fields_use_defaults(self):
        """system_prompt, message_count, document_ids default gracefully."""
        session = {
            "id": "s1",
            "title": "Minimal",
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
            "model": "default-model",
        }
        resp = session_to_response(session)
        assert resp.system_prompt is None
        assert resp.message_count == 0
        assert resp.document_ids == []

    def test_returns_session_response_type(self, full_session):
        from gaia.ui.models import SessionResponse

        resp = session_to_response(full_session)
        assert isinstance(resp, SessionResponse)


# ── message_to_response ──────────────────────────────────────────────────


class TestMessageToResponse:
    """Tests for message_to_response()."""

    @pytest.fixture
    def base_message(self):
        return {
            "id": 1,
            "session_id": "sess-1",
            "role": "assistant",
            "content": "Hello!",
            "created_at": "2025-01-01T00:00:00",
        }

    def test_basic_message(self, base_message):
        resp = message_to_response(base_message)
        assert resp.id == 1
        assert resp.session_id == "sess-1"
        assert resp.role == "assistant"
        assert resp.content == "Hello!"
        assert resp.rag_sources is None
        assert resp.agent_steps is None

    def test_rag_sources_from_json_string(self, base_message):
        sources = [
            {
                "document_id": "d1",
                "filename": "test.pdf",
                "chunk": "some text",
                "score": 0.95,
            }
        ]
        base_message["rag_sources"] = json.dumps(sources)
        resp = message_to_response(base_message)
        assert resp.rag_sources is not None
        assert len(resp.rag_sources) == 1
        assert resp.rag_sources[0].filename == "test.pdf"
        assert resp.rag_sources[0].score == 0.95

    def test_rag_sources_from_list(self, base_message):
        """rag_sources can also be an already-parsed list."""
        base_message["rag_sources"] = [
            {
                "document_id": "d1",
                "filename": "test.pdf",
                "chunk": "text",
                "score": 0.8,
            }
        ]
        resp = message_to_response(base_message)
        assert resp.rag_sources is not None
        assert len(resp.rag_sources) == 1

    def test_rag_sources_invalid_json_returns_none(self, base_message):
        """Malformed JSON in rag_sources is swallowed, returns None."""
        base_message["rag_sources"] = "not valid json {"
        resp = message_to_response(base_message)
        assert resp.rag_sources is None

    def test_rag_sources_empty_string_is_falsy(self, base_message):
        """Empty string is falsy, so rag_sources stays None."""
        base_message["rag_sources"] = ""
        resp = message_to_response(base_message)
        assert resp.rag_sources is None

    def test_agent_steps_from_json_string(self, base_message):
        steps = [
            {
                "id": 1,
                "type": "thinking",
                "label": "Thinking",
                "timestamp": 1000,
            }
        ]
        base_message["agent_steps"] = json.dumps(steps)
        resp = message_to_response(base_message)
        assert resp.agent_steps is not None
        assert len(resp.agent_steps) == 1
        assert resp.agent_steps[0].type == "thinking"
        assert resp.agent_steps[0].label == "Thinking"

    def test_agent_steps_from_list(self, base_message):
        base_message["agent_steps"] = [
            {"id": 1, "type": "tool", "label": "search", "timestamp": 0}
        ]
        resp = message_to_response(base_message)
        assert resp.agent_steps is not None
        assert resp.agent_steps[0].type == "tool"

    def test_agent_steps_invalid_json_returns_none(self, base_message):
        base_message["agent_steps"] = "{broken"
        resp = message_to_response(base_message)
        assert resp.agent_steps is None

    def test_agent_steps_missing_required_field_returns_none(self, base_message):
        """AgentStepResponse requires 'id', 'type', 'label' - missing one = None."""
        base_message["agent_steps"] = json.dumps([{"type": "tool"}])
        resp = message_to_response(base_message)
        assert resp.agent_steps is None

    def test_both_sources_and_steps(self, base_message):
        base_message["rag_sources"] = json.dumps(
            [
                {
                    "document_id": "d1",
                    "filename": "f.pdf",
                    "chunk": "c",
                    "score": 0.9,
                }
            ]
        )
        base_message["agent_steps"] = json.dumps(
            [{"id": 1, "type": "thinking", "label": "Thinking", "timestamp": 0}]
        )
        resp = message_to_response(base_message)
        assert resp.rag_sources is not None
        assert resp.agent_steps is not None

    def test_returns_message_response_type(self, base_message):
        from gaia.ui.models import MessageResponse

        assert isinstance(message_to_response(base_message), MessageResponse)


# ── doc_to_response ──────────────────────────────────────────────────────


class TestDocToResponse:
    """Tests for doc_to_response()."""

    @pytest.fixture
    def full_doc(self):
        return {
            "id": "doc-1",
            "filename": "report.pdf",
            "filepath": "/docs/report.pdf",
            "file_size": 1024000,
            "chunk_count": 42,
            "indexed_at": "2025-01-01T00:00:00",
            "last_accessed_at": "2025-01-02T00:00:00",
            "sessions_using": 3,
            "indexing_status": "complete",
        }

    def test_full_document(self, full_doc):
        resp = doc_to_response(full_doc)
        assert resp.id == "doc-1"
        assert resp.filename == "report.pdf"
        assert resp.file_size == 1024000
        assert resp.chunk_count == 42
        assert resp.sessions_using == 3
        assert resp.indexing_status == "complete"

    def test_missing_optional_fields_use_defaults(self):
        doc = {
            "id": "d1",
            "filename": "test.txt",
            "filepath": "/test.txt",
            "indexed_at": "2025-01-01T00:00:00",
        }
        resp = doc_to_response(doc)
        assert resp.file_size == 0
        assert resp.chunk_count == 0
        assert resp.last_accessed_at is None
        assert resp.sessions_using == 0
        assert resp.indexing_status == "complete"

    def test_indexing_status_values(self, full_doc):
        for status in ["pending", "indexing", "complete", "failed", "cancelled"]:
            full_doc["indexing_status"] = status
            resp = doc_to_response(full_doc)
            assert resp.indexing_status == status

    def test_returns_document_response_type(self, full_doc):
        from gaia.ui.models import DocumentResponse

        assert isinstance(doc_to_response(full_doc), DocumentResponse)


# ── ensure_within_home ────────────────────────────────────────────────────


class TestEnsureWithinHome:
    """Tests for ensure_within_home()."""

    def test_home_directory_passes(self):
        """Path.home() itself should pass."""
        ensure_within_home(Path.home())

    def test_subdirectory_of_home_passes(self):
        ensure_within_home(Path.home() / "Documents" / "test.pdf")

    def test_path_outside_home_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            ensure_within_home(Path("/"))
        assert exc_info.value.status_code == 403
        assert "home directory" in exc_info.value.detail

    def test_system_path_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            ensure_within_home(
                Path("C:/Windows/System32") if Path("C:/").exists() else Path("/etc")
            )
        assert exc_info.value.status_code == 403

    def test_deeply_nested_home_subdirectory_passes(self):
        deep_path = Path.home() / "a" / "b" / "c" / "d" / "e"
        ensure_within_home(deep_path)
