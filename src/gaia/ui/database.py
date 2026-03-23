# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Database manager for GAIA Agent UI.

Manages sessions, messages, documents, and their relationships using SQLite.
"""

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".gaia" / "chat" / "gaia_chat.db"

SCHEMA_SQL = """
-- Global document library
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

-- Sessions (conversations)
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Chat',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    model TEXT NOT NULL DEFAULT 'Qwen3.5-35B-A3B-GGUF',
    system_prompt TEXT
);

-- Many-to-many: which docs are attached to which session
CREATE TABLE IF NOT EXISTS session_documents (
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    attached_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, document_id)
);

-- Messages
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_session_docs ON session_documents(session_id);
"""


class ChatDatabase:
    """SQLite database for Agent UI sessions, messages, and documents."""

    def __init__(self, db_path: str = None):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to ~/.gaia/chat/gaia_chat.db.
                     Use ":memory:" for in-memory database (testing).
        """
        if db_path is None:
            db_path = str(DEFAULT_DB_PATH)

        self._db_path = db_path
        self._lock = threading.RLock()

        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()
        logger.info("Chat database initialized: %s", db_path)

    def _init_schema(self):
        """Create tables if they don't exist and run migrations."""
        self._conn.executescript(SCHEMA_SQL)
        self._migrate()

    def _ensure_settings_table(self):
        """Create the settings key-value table if it doesn't exist."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
        self._conn.commit()

    def _migrate(self):
        """Apply incremental schema migrations for existing databases."""
        # Ensure settings table exists
        self._ensure_settings_table()
        # Add agent_steps column if it doesn't exist (added for observability persistence)
        try:
            cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
            ]
            if "agent_steps" not in cols:
                self._conn.execute("ALTER TABLE messages ADD COLUMN agent_steps TEXT")
                self._conn.commit()
                logger.info("Migrated messages table: added agent_steps column")
        except Exception as e:
            logger.debug("Migration check for agent_steps: %s", e)

        # Add indexing_status column for background indexing progress
        try:
            doc_cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(documents)").fetchall()
            ]
            if "indexing_status" not in doc_cols:
                self._conn.execute(
                    "ALTER TABLE documents ADD COLUMN indexing_status TEXT DEFAULT 'complete'"
                )
                self._conn.commit()
                logger.info("Migrated documents table: added indexing_status column")
        except Exception as e:
            logger.debug("Migration check for indexing_status: %s", e)

        # Add file_mtime column for tracking file modification times
        try:
            doc_cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(documents)").fetchall()
            ]
            if "file_mtime" not in doc_cols:
                self._conn.execute("ALTER TABLE documents ADD COLUMN file_mtime REAL")
                self._conn.commit()
                logger.info("Migrated documents table: added file_mtime column")
        except Exception as e:
            logger.debug("Migration check for file_mtime: %s", e)

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _transaction(self):
        """Execute operations atomically with thread safety."""
        if self._conn is None:
            raise RuntimeError("Database connection is closed")
        with self._lock:
            try:
                yield
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _now(self) -> str:
        """Current UTC timestamp as ISO string."""
        return datetime.now(timezone.utc).isoformat()

    # ── Sessions ────────────────────────────────────────────────────────

    def create_session(
        self,
        title: str = None,
        model: str = None,
        system_prompt: str = None,
        document_ids: List[str] = None,
    ) -> Dict[str, Any]:
        """Create a new chat session."""
        session_id = str(uuid.uuid4())
        now = self._now()
        model = model or "Qwen3.5-35B-A3B-GGUF"
        title = title or "New Chat"

        with self._transaction():
            self._conn.execute(
                """INSERT INTO sessions (id, title, created_at, updated_at, model, system_prompt)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, title, now, now, model, system_prompt),
            )

            # Attach documents if provided
            if document_ids:
                for doc_id in document_ids:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO session_documents
                           (session_id, document_id, attached_at)
                           VALUES (?, ?, ?)""",
                        (session_id, doc_id, now),
                    )

        return self.get_session(session_id)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID with message count and document IDs."""
        with self._lock:
            row = self._conn.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM messages WHERE session_id = s.id) as message_count
                   FROM sessions s WHERE s.id = ?""",
                (session_id,),
            ).fetchone()

            if not row:
                return None

            session = dict(row)

            # Get attached document IDs
            doc_rows = self._conn.execute(
                "SELECT document_id FROM session_documents WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            session["document_ids"] = [r["document_id"] for r in doc_rows]

            return session

    def list_sessions(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """List sessions ordered by most recently updated."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM messages WHERE session_id = s.id) as message_count
                   FROM sessions s
                   ORDER BY s.updated_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()

            sessions = []
            for row in rows:
                session = dict(row)
                doc_rows = self._conn.execute(
                    "SELECT document_id FROM session_documents WHERE session_id = ?",
                    (session["id"],),
                ).fetchall()
                session["document_ids"] = [r["document_id"] for r in doc_rows]
                sessions.append(session)

            return sessions

    def count_sessions(self) -> int:
        """Count total sessions."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) as cnt FROM sessions").fetchone()
            return row["cnt"]

    def update_session(
        self,
        session_id: str,
        title: str = None,
        system_prompt: str = None,
        document_ids: list = None,
    ) -> Optional[Dict[str, Any]]:
        """Update session title, system prompt, and/or document_ids."""
        updates = []
        params = []

        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if system_prompt is not None:
            updates.append("system_prompt = ?")
            params.append(system_prompt)

        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(session_id)

        with self._transaction():
            self._conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            # Update session-document attachments via the join table.
            # Replace the full set: delete all existing links then re-insert
            # so the final state exactly matches the supplied list.
            if document_ids is not None:
                self._conn.execute(
                    "DELETE FROM session_documents WHERE session_id = ?",
                    (session_id,),
                )
                now = self._now()
                for doc_id in document_ids:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO session_documents
                           (session_id, document_id, attached_at)
                           VALUES (?, ?, ?)""",
                        (session_id, doc_id, now),
                    )

        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and its messages."""
        with self._transaction():
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            deleted = cursor.rowcount > 0
        return deleted

    def touch_session(self, session_id: str):
        """Update the session's updated_at timestamp."""
        with self._transaction():
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (self._now(), session_id),
            )

    # ── Messages ────────────────────────────────────────────────────────

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        rag_sources: List[Dict] = None,
        agent_steps: List[Dict] = None,
        tokens_prompt: int = None,
        tokens_completion: int = None,
    ) -> int:
        """Add a message to a session. Returns message ID."""
        sources_json = json.dumps(rag_sources) if rag_sources else None
        steps_json = json.dumps(agent_steps) if agent_steps else None

        with self._transaction():
            cursor = self._conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, created_at, rag_sources,
                    agent_steps, tokens_prompt, tokens_completion)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    content,
                    self._now(),
                    sources_json,
                    steps_json,
                    tokens_prompt,
                    tokens_completion,
                ),
            )

            # Update session timestamp
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (self._now(), session_id),
            )
            msg_id = cursor.lastrowid

        return msg_id

    def get_messages(
        self, session_id: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get messages for a session, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM messages
                   WHERE session_id = ?
                   ORDER BY created_at ASC
                   LIMIT ? OFFSET ?""",
                (session_id, limit, offset),
            ).fetchall()

        messages = []
        for row in rows:
            msg = dict(row)
            if msg.get("rag_sources"):
                try:
                    msg["rag_sources"] = json.loads(msg["rag_sources"])
                except (json.JSONDecodeError, TypeError):
                    msg["rag_sources"] = None
            if msg.get("agent_steps"):
                try:
                    msg["agent_steps"] = json.loads(msg["agent_steps"])
                except (json.JSONDecodeError, TypeError):
                    msg["agent_steps"] = None
            messages.append(msg)

        return messages

    def delete_message(self, session_id: str, message_id: int) -> bool:
        """Delete a single message by ID.

        Args:
            session_id: Session the message belongs to (for safety).
            message_id: ID of the message to delete.

        Returns:
            True if a message was deleted, False if not found.
        """
        with self._transaction():
            cursor = self._conn.execute(
                "DELETE FROM messages WHERE id = ? AND session_id = ?",
                (message_id, session_id),
            )
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info("Deleted message %d from session %s", message_id, session_id)

        return deleted

    def delete_messages_from(self, session_id: str, message_id: int) -> int:
        """Delete a message and all subsequent messages in the session.

        Used for the "resend" flow: removes the target message and everything
        after it so the user can re-submit from that point.

        Args:
            session_id: Session the messages belong to.
            message_id: ID of the first message to delete. All messages with
                        id >= this value in the same session are removed.

        Returns:
            Number of messages deleted.
        """
        with self._transaction():
            cursor = self._conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id >= ?",
                (session_id, message_id),
            )
            count = cursor.rowcount

        if count:
            logger.info(
                "Deleted %d message(s) from session %s starting at id %d",
                count,
                session_id,
                message_id,
            )

        return count

    def count_messages(self, session_id: str) -> int:
        """Count messages in a session."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row["cnt"]

    # ── Documents ───────────────────────────────────────────────────────

    def add_document(
        self,
        filename: str,
        filepath: str,
        file_hash: str,
        file_size: int = 0,
        chunk_count: int = 0,
        file_mtime: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add a document to the library. Returns existing doc if hash matches.

        Uses a single lock acquisition for the check-then-insert pattern
        to prevent race conditions with concurrent uploads of the same file.
        """
        doc_id = str(uuid.uuid4())
        now = self._now()

        with self._lock:
            # Check if document with same hash already exists
            existing = self._conn.execute(
                "SELECT * FROM documents WHERE file_hash = ?", (file_hash,)
            ).fetchone()

            if existing:
                doc = dict(existing)
                # Update last_accessed_at and chunk_count (if newly indexed
                # count is higher, e.g. fixing a previous 0-chunk bug)
                new_chunk_count = max(chunk_count, doc.get("chunk_count", 0))
                self._conn.execute(
                    "UPDATE documents SET last_accessed_at = ?, chunk_count = ?, file_mtime = ? WHERE id = ?",
                    (now, new_chunk_count, file_mtime, doc["id"]),
                )
                self._conn.commit()
                doc["chunk_count"] = new_chunk_count
                return self._enrich_document(doc)

            # Insert new document (still under lock)
            try:
                self._conn.execute(
                    """INSERT INTO documents
                       (id, filename, filepath, file_hash, file_size, chunk_count,
                        indexed_at, last_accessed_at, file_mtime)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        doc_id,
                        filename,
                        filepath,
                        file_hash,
                        file_size,
                        chunk_count,
                        now,
                        now,
                        file_mtime,
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        return self.get_document(doc_id)

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get document by ID."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()

            if not row:
                return None

            return self._enrich_document(dict(row))

    def _enrich_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Add sessions_using count to document dict.

        NOTE: Caller must hold self._lock.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM session_documents WHERE document_id = ?",
            (doc["id"],),
        ).fetchone()
        doc["sessions_using"] = row["cnt"]
        return doc

    def list_documents(self) -> List[Dict[str, Any]]:
        """List all documents in the library."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM documents ORDER BY indexed_at DESC"
            ).fetchall()

            docs = []
            for row in rows:
                docs.append(self._enrich_document(dict(row)))
            return docs

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document from the library."""
        with self._transaction():
            cursor = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            deleted = cursor.rowcount > 0
        return deleted

    # ── Session-Document Attachments ────────────────────────────────────

    def attach_document(self, session_id: str, document_id: str) -> bool:
        """Attach a document to a session."""
        try:
            with self._transaction():
                self._conn.execute(
                    """INSERT OR IGNORE INTO session_documents
                       (session_id, document_id, attached_at)
                       VALUES (?, ?, ?)""",
                    (session_id, document_id, self._now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def detach_document(self, session_id: str, document_id: str) -> bool:
        """Detach a document from a session."""
        with self._transaction():
            cursor = self._conn.execute(
                """DELETE FROM session_documents
                   WHERE session_id = ? AND document_id = ?""",
                (session_id, document_id),
            )
            detached = cursor.rowcount > 0
        return detached

    def get_session_documents(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all documents attached to a session."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT d.* FROM documents d
                   INNER JOIN session_documents sd ON d.id = sd.document_id
                   WHERE sd.session_id = ?
                   ORDER BY sd.attached_at DESC""",
                (session_id,),
            ).fetchall()
            return [self._enrich_document(dict(row)) for row in rows]

    # ── Document Status ────────────────────────────────────────────

    def update_document_status(
        self, doc_id: str, status: str, chunk_count: int = None
    ) -> bool:
        """Update a document's indexing status and optionally its chunk count.

        Args:
            doc_id: Document ID.
            status: New status ('pending', 'indexing', 'complete', 'failed', 'cancelled').
            chunk_count: If provided, also update the chunk count.

        Returns:
            True if the document was found and updated.
        """
        with self._transaction():
            parts = ["indexing_status = ?"]
            params: list = [status]
            if chunk_count is not None:
                parts.append("chunk_count = ?")
                params.append(chunk_count)
            parts.append("last_accessed_at = ?")
            params.append(self._now())
            params.append(doc_id)
            cursor = self._conn.execute(
                f"UPDATE documents SET {', '.join(parts)} WHERE id = ?",
                params,
            )
            return cursor.rowcount > 0

    def reindex_document(
        self,
        doc_id: str,
        file_hash: str,
        file_mtime: float,
        chunk_count: int = 0,
        file_size: int = 0,
    ) -> bool:
        """Update a document after re-indexing due to file change.

        Updates the hash, mtime, chunk count, file size, and resets
        indexed_at to the current time.

        Args:
            doc_id: Document ID.
            file_hash: New SHA-256 hash of the file contents.
            file_mtime: New file modification time (Unix epoch float).
            chunk_count: New chunk count from re-indexing.
            file_size: New file size in bytes.

        Returns:
            True if the document was found and updated.
        """
        with self._transaction():
            cursor = self._conn.execute(
                """UPDATE documents
                   SET file_hash = ?, file_mtime = ?, chunk_count = ?,
                       file_size = ?, indexed_at = ?, indexing_status = 'complete',
                       last_accessed_at = ?
                   WHERE id = ?""",
                (
                    file_hash,
                    file_mtime,
                    chunk_count,
                    file_size,
                    self._now(),
                    self._now(),
                    doc_id,
                ),
            )
            return cursor.rowcount > 0

    def update_document_mtime(self, doc_id: str, file_mtime: float) -> bool:
        """Update only the stored file mtime (when content unchanged).

        Used when the file's mtime changed but the hash is identical
        (e.g., the file was touched without content modification).

        Args:
            doc_id: Document ID.
            file_mtime: New file modification time (Unix epoch float).

        Returns:
            True if the document was found and updated.
        """
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE documents SET file_mtime = ? WHERE id = ?",
                (file_mtime, doc_id),
            )
            return cursor.rowcount > 0

    # ── Settings ──────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = None) -> Optional[str]:
        """Get a setting value by key."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: Optional[str]) -> None:
        """Set a setting value. Pass None to delete the key."""
        with self._transaction():
            if value is None:
                self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )

    def get_all_settings(self) -> Dict[str, str]:
        """Get all settings as a dict."""
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: row["value"] for row in rows}

    # ── Stats ───────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get overall database statistics."""
        with self._lock:
            sessions = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM sessions"
            ).fetchone()["cnt"]
            messages = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM messages"
            ).fetchone()["cnt"]
            documents = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM documents"
            ).fetchone()["cnt"]
            total_chunks = self._conn.execute(
                "SELECT COALESCE(SUM(chunk_count), 0) as total FROM documents"
            ).fetchone()["total"]
            total_size = self._conn.execute(
                "SELECT COALESCE(SUM(file_size), 0) as total FROM documents"
            ).fetchone()["total"]

            return {
                "sessions": sessions,
                "messages": messages,
                "documents": documents,
                "total_chunks": total_chunks,
                "total_size_bytes": total_size,
            }

    # ── Settings ──────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = None) -> Optional[str]:
        """Get a setting value by key."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: Optional[str]) -> None:
        """Set a setting value. Pass None to delete the key."""
        with self._transaction():
            if value is None:
                self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )

    def get_all_settings(self) -> dict:
        """Return all settings as a key→value dict."""
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: row["value"] for row in rows}
