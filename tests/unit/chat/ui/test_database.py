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
        assert session["model"] == "Gemma-4-E4B-it-GGUF"

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

    def test_update_session_model(self, db):
        # Multi-device (#1220): a device switch rewrites the session model so
        # the rebuilt agent loads the device's model (release blocker B1).
        session = db.create_session(model="Gemma-4-E4B-it-GGUF")
        updated = db.update_session(
            session["id"], device="npu", model="gemma4-it-e2b-FLM"
        )
        assert updated["device"] == "npu"
        assert updated["model"] == "gemma4-it-e2b-FLM"

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

    def test_create_session_default_mail_provider_is_null(self, db):
        # #1596: mail_provider is a FILTER — no pick stays NULL ("every
        # connected mailbox"), never silently coerced to google.
        session = db.create_session()
        assert session["mail_provider"] is None

    def test_create_session_with_mail_provider(self, db):
        session = db.create_session(mail_provider="microsoft")
        assert session["mail_provider"] == "microsoft"
        # Round-trips through a fresh read.
        assert db.get_session(session["id"])["mail_provider"] == "microsoft"

    def test_update_session_mail_provider(self, db):
        session = db.create_session()  # no pick (NULL = all connected)
        updated = db.update_session(session["id"], mail_provider="microsoft")
        assert updated["mail_provider"] == "microsoft"


class TestMailProviderMigration:
    """The mail_provider column must be added to pre-existing DBs that were
    created before the email-provider selector landed (additive migration,
    mirroring the device/agent_type precedent)."""

    def test_migration_adds_mail_provider_column(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        # Hand-roll a sessions table from before the mail_provider column.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, "
            "created_at TEXT, updated_at TEXT, model TEXT)"
        )
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, model) "
            "VALUES ('s1', 'Old', '2025-01-01', '2025-01-01', 'm')"
        )
        conn.commit()
        conn.close()

        # Opening through ChatDatabase runs the additive migration.
        database = ChatDatabase(db_path)
        try:
            cols = [
                r[1]
                for r in database._conn.execute(
                    "PRAGMA table_info(sessions)"
                ).fetchall()
            ]
            assert "mail_provider" in cols
            # The pre-existing row defaults to google, never NULL.
            assert database.get_session("s1")["mail_provider"] == "google"
        finally:
            database.close()


class TestTitleIsCustom:
    """Sessions must record whether their title was explicitly set (#2165)
    so the auto-retitler never overwrites a user/API-chosen title."""

    def test_explicit_title_marks_custom(self, db):
        session = db.create_session(title="My Research Project")
        assert session["title_is_custom"] == 1

    @pytest.mark.parametrize("title", [None, "New Chat", "New Task", "Untitled"])
    def test_placeholder_or_default_title_not_custom(self, db, title):
        session = db.create_session(title=title)
        assert session["title_is_custom"] == 0

    def test_rename_pins_title(self, db):
        session = db.create_session()
        updated = db.update_session(
            session["id"], title="Pinned Name", title_is_custom=True
        )
        assert updated["title"] == "Pinned Name"
        assert updated["title_is_custom"] == 1

    def test_auto_title_update_leaves_flag_alone(self, db):
        """The backend auto-titler updates title WITHOUT the flag — the
        session must stay non-custom so later auto-titles still apply."""
        session = db.create_session()
        updated = db.update_session(session["id"], title="LLM Generated Title")
        assert updated["title_is_custom"] == 0

    def test_unrelated_update_preserves_pin(self, db):
        session = db.create_session(title="Pinned")
        updated = db.update_session(session["id"], device="npu")
        assert updated["title_is_custom"] == 1


class TestTitleIsCustomMigration:
    """Pre-#2165 DBs get the title_is_custom column; existing rows with a
    non-placeholder title are backfilled as pinned (can't tell user-set from
    auto-generated, so err on never renaming)."""

    def test_migration_adds_and_backfills_column(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, "
            "created_at TEXT, updated_at TEXT, model TEXT)"
        )
        conn.executemany(
            "INSERT INTO sessions (id, title, created_at, updated_at, model) "
            "VALUES (?, ?, '2025-01-01', '2025-01-01', 'm')",
            [
                ("s1", "Quarterly Report Analysis"),
                ("s2", "New Chat"),
                ("s3", "New Task"),
            ],
        )
        conn.commit()
        conn.close()

        database = ChatDatabase(db_path)
        try:
            cols = [
                r[1]
                for r in database._conn.execute(
                    "PRAGMA table_info(sessions)"
                ).fetchall()
            ]
            assert "title_is_custom" in cols
            assert database.get_session("s1")["title_is_custom"] == 1
            assert database.get_session("s2")["title_is_custom"] == 0
            assert database.get_session("s3")["title_is_custom"] == 0
        finally:
            database.close()


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

    def test_delete_message(self, db):
        session = db.create_session()
        msg_id = db.add_message(session["id"], "user", "Hello")
        assert db.count_messages(session["id"]) == 1
        assert db.delete_message(session["id"], msg_id) is True
        assert db.count_messages(session["id"]) == 0

    def test_delete_message_not_found(self, db):
        session = db.create_session()
        assert db.delete_message(session["id"], 99999) is False

    def test_delete_message_wrong_session(self, db):
        """Deleting a message with wrong session_id should fail."""
        s1 = db.create_session()
        s2 = db.create_session()
        msg_id = db.add_message(s1["id"], "user", "Hello")
        # Should not delete when session_id doesn't match
        assert db.delete_message(s2["id"], msg_id) is False
        # Original message still exists
        assert db.count_messages(s1["id"]) == 1

    def test_delete_messages_from(self, db):
        session = db.create_session()
        id1 = db.add_message(session["id"], "user", "First")
        id2 = db.add_message(session["id"], "assistant", "Reply 1")
        id3 = db.add_message(session["id"], "user", "Second")
        id4 = db.add_message(session["id"], "assistant", "Reply 2")

        # Delete from message 3 onward (user "Second" + assistant "Reply 2")
        count = db.delete_messages_from(session["id"], id3)
        assert count == 2
        remaining = db.get_messages(session["id"])
        assert len(remaining) == 2
        assert remaining[0]["content"] == "First"
        assert remaining[1]["content"] == "Reply 1"

    def test_delete_messages_from_all(self, db):
        """Deleting from the first message removes everything."""
        session = db.create_session()
        id1 = db.add_message(session["id"], "user", "First")
        db.add_message(session["id"], "assistant", "Reply")
        count = db.delete_messages_from(session["id"], id1)
        assert count == 2
        assert db.count_messages(session["id"]) == 0

    def test_delete_messages_from_not_found(self, db):
        session = db.create_session()
        count = db.delete_messages_from(session["id"], 99999)
        assert count == 0

    def test_cascade_delete(self, db):
        session = db.create_session()
        db.add_message(session["id"], "user", "Hello")
        db.delete_session(session["id"])
        assert db.count_messages(session["id"]) == 0

    def test_upsert_message_insert_when_no_id(self, db):
        """upsert_message with msg_id=None behaves like a plain insert."""
        session = db.create_session()
        msg_id = db.upsert_message(session["id"], None, "assistant", "Hello")
        assert msg_id > 0
        messages = db.get_messages(session["id"])
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello"

    def test_upsert_message_replaces_existing(self, db):
        """upsert_message deletes the old row and inserts the replacement."""
        session = db.create_session()
        old_id = db.add_message(session["id"], "assistant", "Blocked: tool")
        new_id = db.upsert_message(session["id"], old_id, "assistant", "Full response")
        assert new_id != old_id
        messages = db.get_messages(session["id"])
        assert len(messages) == 1
        assert messages[0]["content"] == "Full response"
        assert messages[0]["id"] == new_id

    def test_upsert_message_wrong_session_keeps_old_row(self, db):
        """A session_id mismatch must not delete another session's message."""
        s1 = db.create_session()
        s2 = db.create_session()
        old_id = db.add_message(s1["id"], "assistant", "Original")
        # Upsert against s2 should not touch s1's row; it inserts into s2.
        db.upsert_message(s2["id"], old_id, "assistant", "Replacement")
        assert db.count_messages(s1["id"]) == 1
        assert db.get_messages(s1["id"])[0]["content"] == "Original"

    def test_upsert_message_atomic_on_insert_failure(self, db, monkeypatch):
        """If the INSERT fails mid-replace, the original row survives.

        Simulates a crash between DELETE and INSERT: the transaction must roll
        back so the session is never left without the message (issue #987).
        """
        session = db.create_session()
        old_id = db.add_message(session["id"], "assistant", "Original")

        real_conn = db._conn

        class _FlakyConn:
            """Forwards to the real connection but blows up on the INSERT."""

            def execute(self, sql, *args, **kwargs):
                if sql.lstrip().upper().startswith("INSERT INTO MESSAGES"):
                    raise sqlite3.OperationalError("simulated crash before commit")
                return real_conn.execute(sql, *args, **kwargs)

            def commit(self):
                return real_conn.commit()

            def rollback(self):
                return real_conn.rollback()

        monkeypatch.setattr(db, "_conn", _FlakyConn())
        with pytest.raises(sqlite3.OperationalError):
            db.upsert_message(session["id"], old_id, "assistant", "Replacement")

        monkeypatch.undo()
        # The DELETE was rolled back — the original row is still present.
        messages = db.get_messages(session["id"])
        assert len(messages) == 1
        assert messages[0]["content"] == "Original"
        assert messages[0]["id"] == old_id

    def test_upsert_message_persists_metadata(self, db):
        """agent_steps and inference_stats round-trip through upsert."""
        session = db.create_session()
        msg_id = db.upsert_message(
            session["id"],
            None,
            "assistant",
            "Answer",
            agent_steps=[{"type": "policy_alert", "decision": "BLOCK"}],
            inference_stats={"tokens_per_second": 42},
        )
        msg = db.get_messages(session["id"])[0]
        assert msg["id"] == msg_id
        assert msg["agent_steps"] == [{"type": "policy_alert", "decision": "BLOCK"}]
        assert msg["inference_stats"] == {"tokens_per_second": 42}


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


class TestUpdateDocumentStatus:
    """Tests for update_document_status() method."""

    def _make_doc(self, db):
        return db.add_document("test.pdf", "/test.pdf", "hash1", 1024, 10)

    def test_update_status_to_indexing(self, db):
        doc = self._make_doc(db)
        result = db.update_document_status(doc["id"], "indexing")
        assert result is True
        fetched = db.get_document(doc["id"])
        assert fetched["indexing_status"] == "indexing"

    def test_update_status_to_complete(self, db):
        doc = self._make_doc(db)
        db.update_document_status(doc["id"], "indexing")
        result = db.update_document_status(doc["id"], "complete")
        assert result is True
        fetched = db.get_document(doc["id"])
        assert fetched["indexing_status"] == "complete"

    def test_update_status_to_failed(self, db):
        doc = self._make_doc(db)
        db.update_document_status(doc["id"], "indexing")
        db.update_document_status(doc["id"], "failed")
        fetched = db.get_document(doc["id"])
        assert fetched["indexing_status"] == "failed"

    def test_update_status_to_cancelled(self, db):
        doc = self._make_doc(db)
        db.update_document_status(doc["id"], "indexing")
        db.update_document_status(doc["id"], "cancelled")
        fetched = db.get_document(doc["id"])
        assert fetched["indexing_status"] == "cancelled"

    def test_update_status_with_chunk_count(self, db):
        doc = self._make_doc(db)
        db.update_document_status(doc["id"], "complete", chunk_count=99)
        fetched = db.get_document(doc["id"])
        assert fetched["indexing_status"] == "complete"
        assert fetched["chunk_count"] == 99

    def test_update_status_without_chunk_count_preserves_original(self, db):
        doc = self._make_doc(db)
        original_chunks = doc["chunk_count"]
        db.update_document_status(doc["id"], "indexing")
        fetched = db.get_document(doc["id"])
        assert fetched["chunk_count"] == original_chunks

    def test_update_status_updates_last_accessed_at(self, db):
        doc = self._make_doc(db)
        original = db.get_document(doc["id"])["last_accessed_at"]
        time.sleep(0.01)
        db.update_document_status(doc["id"], "indexing")
        fetched = db.get_document(doc["id"])
        assert fetched["last_accessed_at"] is not None
        assert fetched["last_accessed_at"] >= original

    def test_update_nonexistent_document_returns_false(self, db):
        result = db.update_document_status("nonexistent-id", "complete")
        assert result is False

    def test_full_status_lifecycle(self, db):
        """Test the full indexing lifecycle: pending -> indexing -> complete."""
        doc = self._make_doc(db)
        doc_id = doc["id"]

        db.update_document_status(doc_id, "indexing")
        assert db.get_document(doc_id)["indexing_status"] == "indexing"

        db.update_document_status(doc_id, "complete", chunk_count=50)
        fetched = db.get_document(doc_id)
        assert fetched["indexing_status"] == "complete"
        assert fetched["chunk_count"] == 50

    def test_status_transition_indexing_to_failed_preserves_zero_chunks(self, db):
        doc = self._make_doc(db)
        db.update_document_status(doc["id"], "indexing")
        db.update_document_status(doc["id"], "failed", chunk_count=0)
        fetched = db.get_document(doc["id"])
        assert fetched["indexing_status"] == "failed"
        assert fetched["chunk_count"] == 0


class TestReindexDocument:
    """Tests for reindex_document() method."""

    def test_reindex_updates_all_fields(self, db):
        doc = db.add_document("test.pdf", "/test.pdf", "old_hash", 1024, 10)
        result = db.reindex_document(
            doc["id"],
            file_hash="new_hash",
            file_mtime=2000.0,
            chunk_count=25,
            file_size=2048,
        )
        assert result is True
        fetched = db.get_document(doc["id"])
        assert fetched["file_hash"] == "new_hash"
        assert fetched["chunk_count"] == 25
        assert fetched["file_size"] == 2048
        assert fetched["indexing_status"] == "complete"

    def test_reindex_nonexistent_returns_false(self, db):
        result = db.reindex_document("nonexistent", file_hash="h", file_mtime=1.0)
        assert result is False

    def test_reindex_resets_status_to_complete(self, db):
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        db.update_document_status(doc["id"], "failed")
        db.reindex_document(
            doc["id"], file_hash="hash2", file_mtime=2000.0, chunk_count=8
        )
        fetched = db.get_document(doc["id"])
        assert fetched["indexing_status"] == "complete"
        assert fetched["chunk_count"] == 8


class TestUpdateDocumentMtime:
    """Tests for update_document_mtime() method."""

    def test_updates_mtime(self, db):
        doc = db.add_document(
            "test.pdf", "/test.pdf", "hash1", 100, 5, file_mtime=1000.0
        )
        result = db.update_document_mtime(doc["id"], 2000.0)
        assert result is True

    def test_nonexistent_returns_false(self, db):
        result = db.update_document_mtime("nonexistent", 2000.0)
        assert result is False

    def test_does_not_change_other_fields(self, db):
        doc = db.add_document("test.pdf", "/test.pdf", "hash1", 100, 5)
        db.update_document_mtime(doc["id"], 9999.0)
        fetched = db.get_document(doc["id"])
        assert fetched["file_hash"] == "hash1"
        assert fetched["chunk_count"] == 5
        assert fetched["file_size"] == 100


class TestAddMessageWithAgentSteps:
    """Tests for add_message() with agent_steps parameter."""

    def test_add_message_with_agent_steps(self, db):
        session = db.create_session()
        steps = [
            {"id": 1, "type": "thinking", "label": "Thinking", "active": False},
            {"id": 2, "type": "tool", "label": "search", "active": False},
        ]
        db.add_message(session["id"], "assistant", "Result", agent_steps=steps)
        messages = db.get_messages(session["id"])
        msg = messages[0]
        assert msg["agent_steps"] is not None
        # get_messages() parses agent_steps from JSON, so it's already a list
        parsed = msg["agent_steps"]
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["type"] == "thinking"
        assert parsed[1]["type"] == "tool"

    def test_add_message_without_agent_steps(self, db):
        session = db.create_session()
        db.add_message(session["id"], "user", "Hello")
        messages = db.get_messages(session["id"])
        assert messages[0].get("agent_steps") is None

    def test_add_message_with_empty_agent_steps(self, db):
        """Empty list is falsy in Python -- documents current behavior."""
        session = db.create_session()
        db.add_message(session["id"], "assistant", "Reply", agent_steps=[])
        messages = db.get_messages(session["id"])
        # Empty list is falsy, so the code path `if agent_steps:` skips it
        assert messages[0].get("agent_steps") is None


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


class TestScheduledTaskStorage:
    """Schedule persistence via public ChatDatabase API (PR #517 salvage)."""

    def _task_row(self, **overrides):
        row = {
            "id": "t-1",
            "name": "morning-brief",
            "interval_seconds": 3600,
            "prompt": "Summarize my inbox",
            "status": "active",
            "created_at": "2026-06-09T00:00:00+00:00",
            "next_run_at": None,
            "run_count": 0,
            "error_count": 0,
            "session_id": None,
            "schedule_config": None,
        }
        row.update(overrides)
        return row

    def test_create_and_list_scheduled_tasks(self, db):
        db.create_scheduled_task(self._task_row())
        rows = db.list_scheduled_tasks()
        assert len(rows) == 1
        assert rows[0]["name"] == "morning-brief"
        assert rows[0]["interval_seconds"] == 3600

    def test_duplicate_name_raises(self, db):
        db.create_scheduled_task(self._task_row())
        with pytest.raises(sqlite3.IntegrityError):
            db.create_scheduled_task(self._task_row(id="t-2"))

    def test_update_scheduled_task(self, db):
        db.create_scheduled_task(self._task_row())
        db.update_scheduled_task(
            "t-1",
            status="paused",
            run_count=3,
            last_run_at="2026-06-09T01:00:00+00:00",
            next_run_at=None,
            last_result="ok",
            error_count=0,
            session_id="s-9",
            schedule_config=None,
        )
        rows = db.list_scheduled_tasks()
        assert rows[0]["status"] == "paused"
        assert rows[0]["run_count"] == 3
        assert rows[0]["session_id"] == "s-9"

    def test_delete_scheduled_task_cascades_results(self, db):
        db.create_scheduled_task(self._task_row())
        db.store_schedule_result(
            "r-1", "t-1", "2026-06-09T01:00:00+00:00", result="done", error=None
        )
        db.delete_scheduled_task("t-1")
        assert db.list_scheduled_tasks() == []
        assert db.get_schedule_results("t-1") == []

    def test_store_and_get_results_ordered_desc(self, db):
        db.create_scheduled_task(self._task_row())
        db.store_schedule_result(
            "r-1", "t-1", "2026-06-09T01:00:00+00:00", "first", None
        )
        db.store_schedule_result(
            "r-2", "t-1", "2026-06-09T02:00:00+00:00", "second", None
        )
        results = db.get_schedule_results("t-1", limit=10)
        assert [r["result"] for r in results] == ["second", "first"]

    def test_get_results_respects_limit(self, db):
        db.create_scheduled_task(self._task_row())
        for i in range(5):
            db.store_schedule_result(
                f"r-{i}", "t-1", f"2026-06-09T0{i}:00:00+00:00", str(i), None
            )
        assert len(db.get_schedule_results("t-1", limit=2)) == 2
