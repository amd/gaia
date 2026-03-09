# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for GAIA Agent UI database layer."""

import sqlite3
import time

import pytest

from gaia.ui.database import ChatDatabase


@pytest.fixture
def db():
    """In-memory database for testing."""
    database = ChatDatabase(":memory:")
    yield database
    database.close()


class TestSessions:
    def test_create_session(self, db):
        session = db.create_session(title="Test Chat")
        assert session["id"]
        assert session["title"] == "Test Chat"
        assert session["message_count"] == 0

    def test_create_session_default_title(self, db):
        session = db.create_session()
        assert session["title"] == "New Chat"

    def test_create_session_with_model(self, db):
        session = db.create_session(model="Qwen3-0.6B-GGUF")
        assert session["model"] == "Qwen3-0.6B-GGUF"

    def test_create_session_default_model(self, db):
        session = db.create_session()
        assert session["model"] == "Qwen3-Coder-30B-A3B-Instruct-GGUF"

    def test_create_session_with_system_prompt(self, db):
        session = db.create_session(system_prompt="You are helpful.")
        assert session["system_prompt"] == "You are helpful."

    def test_get_session(self, db):
        created = db.create_session(title="Hello")
        fetched = db.get_session(created["id"])
        assert fetched is not None
        assert fetched["title"] == "Hello"

    def test_get_session_not_found(self, db):
        assert db.get_session("nonexistent") is None

    def test_list_sessions(self, db):
        db.create_session(title="A")
        db.create_session(title="B")
        sessions = db.list_sessions()
        assert len(sessions) == 2

    def test_list_sessions_pagination(self, db):
        for i in range(5):
            db.create_session(title=f"Session {i}")
        page1 = db.list_sessions(limit=2, offset=0)
        page2 = db.list_sessions(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        # Pages should have different sessions
        ids1 = {s["id"] for s in page1}
        ids2 = {s["id"] for s in page2}
        assert ids1.isdisjoint(ids2)

    def test_update_session(self, db):
        session = db.create_session(title="Old")
        updated = db.update_session(session["id"], title="New")
        assert updated["title"] == "New"

    def test_update_session_system_prompt(self, db):
        session = db.create_session()
        updated = db.update_session(session["id"], system_prompt="Be concise.")
        assert updated["system_prompt"] == "Be concise."

    def test_update_session_no_changes(self, db):
        session = db.create_session(title="Keep")
        result = db.update_session(session["id"])
        assert result["title"] == "Keep"

    def test_update_session_not_found(self, db):
        result = db.update_session("nonexistent", title="Nope")
        assert result is None

    def test_delete_session(self, db):
        session = db.create_session(title="Delete Me")
        assert db.delete_session(session["id"]) is True
        assert db.get_session(session["id"]) is None

    def test_delete_session_not_found(self, db):
        assert db.delete_session("nonexistent") is False

    def test_count_sessions(self, db):
        assert db.count_sessions() == 0
        db.create_session()
        db.create_session()
        assert db.count_sessions() == 2

    def test_touch_session(self, db):
        session = db.create_session()
        original_updated = session["updated_at"]
        time.sleep(0.01)
        db.touch_session(session["id"])
        refreshed = db.get_session(session["id"])
        assert refreshed["updated_at"] >= original_updated


class TestMessages:
    def test_add_and_get_messages(self, db):
        session = db.create_session()
        db.add_message(session["id"], "user", "Hello")
        db.add_message(session["id"], "assistant", "Hi there!")
        messages = db.get_messages(session["id"])
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"

    def test_add_message_returns_id(self, db):
        session = db.create_session()
        msg_id = db.add_message(session["id"], "user", "Hello")
        assert isinstance(msg_id, int)
        assert msg_id > 0

    def test_add_message_with_tokens(self, db):
        session = db.create_session()
        db.add_message(
            session["id"],
            "assistant",
            "Response",
            tokens_prompt=100,
            tokens_completion=50,
        )
        messages = db.get_messages(session["id"])
        assert messages[0]["tokens_prompt"] == 100
        assert messages[0]["tokens_completion"] == 50

    def test_add_message_system_role(self, db):
        session = db.create_session()
        msg_id = db.add_message(session["id"], "system", "You are helpful.")
        assert isinstance(msg_id, int)
        messages = db.get_messages(session["id"])
        assert messages[0]["role"] == "system"

    def test_add_message_invalid_role_rejected(self, db):
        session = db.create_session()
        with pytest.raises(sqlite3.IntegrityError):
            db.add_message(session["id"], "invalid_role", "Bad")

    def test_add_message_updates_session_timestamp(self, db):
        session = db.create_session()
        original = session["updated_at"]
        time.sleep(0.01)
        db.add_message(session["id"], "user", "New message")
        refreshed = db.get_session(session["id"])
        assert refreshed["updated_at"] >= original

    def test_message_count(self, db):
        session = db.create_session()
        assert db.count_messages(session["id"]) == 0
        db.add_message(session["id"], "user", "Test")
        assert db.count_messages(session["id"]) == 1

    def test_get_messages_pagination(self, db):
        session = db.create_session()
        for i in range(5):
            db.add_message(session["id"], "user", f"Message {i}")
        page = db.get_messages(session["id"], limit=2, offset=1)
        assert len(page) == 2
        assert page[0]["content"] == "Message 1"
        assert page[1]["content"] == "Message 2"

    def test_messages_with_rag_sources(self, db):
        session = db.create_session()
        sources = [{"document_id": "doc1", "chunk": "text", "score": 0.9}]
        db.add_message(session["id"], "assistant", "Answer", rag_sources=sources)
        messages = db.get_messages(session["id"])
        assert messages[0]["rag_sources"] is not None
        assert messages[0]["rag_sources"][0]["document_id"] == "doc1"

    def test_cascade_delete(self, db):
        session = db.create_session()
        db.add_message(session["id"], "user", "Hello")
        db.delete_session(session["id"])
        assert db.count_messages(session["id"]) == 0


class TestDocuments:
    def test_add_document(self, db):
        doc = db.add_document("test.pdf", "/path/test.pdf", "abc123", 1024, 10)
        assert doc["id"]
        assert doc["filename"] == "test.pdf"
        assert doc["chunk_count"] == 10

    def test_duplicate_hash_returns_existing(self, db):
        doc1 = db.add_document("test.pdf", "/path/test.pdf", "same_hash", 1024, 10)
        doc2 = db.add_document("test2.pdf", "/path/test2.pdf", "same_hash", 2048, 20)
        assert doc1["id"] == doc2["id"]

    def test_get_document(self, db):
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        fetched = db.get_document(doc["id"])
        assert fetched is not None
        assert fetched["filename"] == "test.pdf"
        assert fetched["file_size"] == 100

    def test_get_document_not_found(self, db):
        assert db.get_document("nonexistent") is None

    def test_list_documents(self, db):
        db.add_document("a.pdf", "/a.pdf", "hash1", 100, 5)
        db.add_document("b.pdf", "/b.pdf", "hash2", 200, 10)
        docs = db.list_documents()
        assert len(docs) == 2

    def test_delete_document(self, db):
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        assert db.delete_document(doc["id"]) is True
        assert db.get_document(doc["id"]) is None

    def test_delete_document_not_found(self, db):
        assert db.delete_document("nonexistent") is False

    def test_sessions_using_count(self, db):
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        s1 = db.create_session(title="A")
        s2 = db.create_session(title="B")
        db.attach_document(s1["id"], doc["id"])
        db.attach_document(s2["id"], doc["id"])
        enriched = db.get_document(doc["id"])
        assert enriched["sessions_using"] == 2


class TestSessionDocuments:
    def test_attach_and_get(self, db):
        session = db.create_session()
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        db.attach_document(session["id"], doc["id"])
        docs = db.get_session_documents(session["id"])
        assert len(docs) == 1
        assert docs[0]["id"] == doc["id"]

    def test_attach_duplicate_is_idempotent(self, db):
        session = db.create_session()
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        db.attach_document(session["id"], doc["id"])
        db.attach_document(session["id"], doc["id"])  # duplicate
        docs = db.get_session_documents(session["id"])
        assert len(docs) == 1

    def test_detach(self, db):
        session = db.create_session()
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        db.attach_document(session["id"], doc["id"])
        result = db.detach_document(session["id"], doc["id"])
        assert result is True
        docs = db.get_session_documents(session["id"])
        assert len(docs) == 0

    def test_detach_not_attached(self, db):
        session = db.create_session()
        result = db.detach_document(session["id"], "nonexistent-doc")
        assert result is False

    def test_create_session_with_document_ids(self, db):
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        session = db.create_session(document_ids=[doc["id"]])
        assert doc["id"] in session["document_ids"]

    def test_cascade_delete_session_detaches_documents(self, db):
        session = db.create_session()
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        db.attach_document(session["id"], doc["id"])
        db.delete_session(session["id"])
        # Document still exists but no longer attached
        assert db.get_document(doc["id"]) is not None
        enriched = db.get_document(doc["id"])
        assert enriched["sessions_using"] == 0


class TestStats:
    def test_get_stats(self, db):
        stats = db.get_stats()
        assert stats["sessions"] == 0

        session = db.create_session()
        db.add_message(session["id"], "user", "Test")
        db.add_document("test.pdf", "/test.pdf", "hash1", 1024, 10)

        stats = db.get_stats()
        assert stats["sessions"] == 1
        assert stats["messages"] == 1
        assert stats["documents"] == 1
        assert stats["total_chunks"] == 10
        assert stats["total_size_bytes"] == 1024
