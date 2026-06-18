# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for document last_error persistence and reindex DB helpers.

Tests both the cold-schema path (fresh DB) and the migration path (ALTER TABLE
on an existing DB that lacks last_error).
"""

import sqlite3
import threading

import pytest

from gaia.ui.database import ChatDatabase

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Fresh in-memory DB — cold schema must include last_error."""
    database = ChatDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture
def legacy_db():
    """Simulate an existing DB created before last_error was added.

    Builds the documents table *without* last_error or indexing_status to
    mimic a pre-1079 on-disk database, then wires up a ChatDatabase so
    _migrate() runs and must add the column via ALTER TABLE.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            file_hash TEXT UNIQUE NOT NULL,
            file_size INTEGER DEFAULT 0,
            chunk_count INTEGER DEFAULT 0,
            indexed_at TEXT DEFAULT (datetime('now')),
            last_accessed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'New Chat',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            model TEXT NOT NULL DEFAULT 'Gemma-4-E4B-it-GGUF',
            system_prompt TEXT,
            device TEXT DEFAULT 'gpu',
            mail_provider TEXT
        );
        CREATE TABLE IF NOT EXISTS session_documents (
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
            attached_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (session_id, document_id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT CHECK(role IN ('user', 'assistant', 'system')) NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            rag_sources TEXT,
            agent_steps TEXT,
            tokens_prompt INTEGER,
            tokens_completion INTEGER
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id               TEXT PRIMARY KEY,
            name             TEXT UNIQUE NOT NULL,
            interval_seconds INTEGER NOT NULL,
            prompt           TEXT NOT NULL,
            status           TEXT DEFAULT 'active',
            created_at       TEXT,
            last_run_at      TEXT,
            next_run_at      TEXT,
            last_result      TEXT,
            run_count        INTEGER DEFAULT 0,
            error_count      INTEGER DEFAULT 0,
            session_id       TEXT,
            schedule_config  TEXT
        );
        CREATE TABLE IF NOT EXISTS schedule_results (
            id          TEXT PRIMARY KEY,
            task_id     TEXT NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
            executed_at TEXT NOT NULL,
            result      TEXT,
            error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);
        CREATE INDEX IF NOT EXISTS idx_session_docs ON session_documents(session_id);
        CREATE INDEX IF NOT EXISTS idx_schedule_results_task
            ON schedule_results(task_id, executed_at DESC);
        """)
    conn.commit()

    # Construct ChatDatabase without calling __init__ (which would create a new
    # connection), then inject the pre-seeded connection and run _migrate().
    database = ChatDatabase.__new__(ChatDatabase)
    database._db_path = ":memory:"
    database._lock = threading.RLock()
    database._conn = conn
    database._migrate()
    yield database
    database.close()


def _insert_doc(db: ChatDatabase, status: str = "complete", suffix: str = "") -> str:
    """Insert a minimal document row and return its id."""
    doc = db.add_document(
        filename=f"test{suffix}.txt",
        filepath=f"/tmp/test{suffix}.txt",
        file_hash=f"abc123{status}{suffix}",
        file_size=100,
        chunk_count=5,
    )
    db.update_document_status(doc["id"], status)
    return doc["id"]


# ── Cold-schema tests ────────────────────────────────────────────────────────


class TestColdSchemaLastError:
    def test_documents_table_has_last_error_column(self, db):
        """Cold DB must have last_error in the documents table."""
        cols = [
            row[1]
            for row in db._conn.execute("PRAGMA table_info(documents)").fetchall()
        ]
        assert "last_error" in cols, f"last_error not found in columns: {cols}"

    def test_update_document_status_persists_last_error(self, db):
        """update_document_status with last_error kwarg writes the message."""
        doc_id = _insert_doc(db, "indexing", "a")
        db.update_document_status(doc_id, "failed", last_error="RAG exploded")
        row = db._conn.execute(
            "SELECT last_error FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        assert row is not None
        assert row["last_error"] == "RAG exploded"

    def test_update_document_status_truncates_long_last_error(self, db):
        """A very long last_error is capped so it can't bloat the UI tooltip."""
        doc_id = _insert_doc(db, "indexing", "trunc")
        db.update_document_status(doc_id, "failed", last_error="x" * 5000)
        row = db._conn.execute(
            "SELECT last_error FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        assert len(row["last_error"]) <= 500
        assert row["last_error"].endswith("…")

    def test_update_document_status_clears_last_error_on_success(self, db):
        """Transitioning to 'complete' must clear any prior last_error."""
        doc_id = _insert_doc(db, "indexing", "b")
        db.update_document_status(doc_id, "failed", last_error="some error")
        db.update_document_status(doc_id, "complete")
        row = db._conn.execute(
            "SELECT last_error FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        assert row["last_error"] is None

    def test_update_document_status_without_last_error_leaves_it_unchanged(self, db):
        """Calling update_document_status without last_error preserves existing value."""
        doc_id = _insert_doc(db, "indexing", "c")
        db.update_document_status(doc_id, "failed", last_error="kept error")
        # Call again without last_error kwarg
        db.update_document_status(doc_id, "failed")
        row = db._conn.execute(
            "SELECT last_error FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        assert row["last_error"] == "kept error"

    def test_get_document_exposes_last_error(self, db):
        """get_document must return last_error in the result dict."""
        doc_id = _insert_doc(db, "indexing", "d")
        db.update_document_status(doc_id, "failed", last_error="chunk error")
        doc = db.get_document(doc_id)
        assert doc is not None
        assert doc.get("last_error") == "chunk error"

    def test_list_documents_exposes_last_error(self, db):
        """list_documents must include last_error for each row."""
        doc_id = _insert_doc(db, "indexing", "e")
        db.update_document_status(doc_id, "failed", last_error="list error")
        docs = db.list_documents()
        target = next((d for d in docs if d["id"] == doc_id), None)
        assert target is not None
        assert target.get("last_error") == "list error"


# ── Migration path tests ─────────────────────────────────────────────────────


class TestMigrationLastError:
    def test_legacy_db_gains_last_error_column_after_migrate(self, legacy_db):
        """An old DB without last_error must gain the column after _migrate()."""
        cols = [
            row[1]
            for row in legacy_db._conn.execute(
                "PRAGMA table_info(documents)"
            ).fetchall()
        ]
        assert "last_error" in cols, f"last_error not found after migration: {cols}"

    def test_legacy_db_write_and_read_last_error(self, legacy_db):
        """After migration, last_error must be writable and readable."""
        doc = legacy_db.add_document(
            filename="migrated.txt",
            filepath="/tmp/migrated.txt",
            file_hash="migrated_hash_abc",
            file_size=50,
            chunk_count=0,
        )
        legacy_db.update_document_status(
            doc["id"], "failed", last_error="migration test error"
        )
        fetched = legacy_db.get_document(doc["id"])
        assert fetched is not None
        assert fetched.get("last_error") == "migration test error"
