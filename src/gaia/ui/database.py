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

# Default model for new sessions — kept in sync with the SQL schema DEFAULT and
# any code that reads session["model"] and falls back when the field is NULL.
SESSION_DEFAULT_MODEL = "Gemma-4-E4B-it-GGUF"

# Auto-generated placeholder titles the auto-retitler may replace (#2165).
# Anything else is treated as explicitly chosen and pinned via title_is_custom.
PLACEHOLDER_TITLES = {
    "",
    "new chat",
    "new task",
    "untitled",
    "untitled session",
    "chat",
}


def is_placeholder_title(title: str | None) -> bool:
    """True when the title is an auto-generated default, not a user choice."""
    return (title or "").strip().lower() in PLACEHOLDER_TITLES


# Sentinel used by update_document_status to distinguish "caller passed None to clear
# last_error" from "caller did not pass last_error at all" (leaving it unchanged).
_UNSET = object()

# Cap stored indexing error messages -- they are surfaced verbatim in a UI
# tooltip and a raw traceback/exception string can be very large (#1749 review).
_MAX_LAST_ERROR_LEN = 500

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
    last_accessed_at TEXT,
    last_error TEXT
);

-- Sessions (conversations)
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Chat',
    -- 1 = explicitly set by the user/API caller; auto-retitler must not touch (#2165)
    title_is_custom INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    model TEXT NOT NULL DEFAULT 'Gemma-4-E4B-it-GGUF',
    system_prompt TEXT,
    device TEXT DEFAULT 'gpu',
    -- Mailbox FILTER (#1596): NULL = every connected mailbox; the legacy
    -- ALTER migration below still backfills pre-Phase-2 rows to 'google'.
    mail_provider TEXT
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_session_docs ON session_documents(session_id);
CREATE INDEX IF NOT EXISTS idx_schedule_results_task
    ON schedule_results(task_id, executed_at DESC);
"""


class ChatDatabase:
    """SQLite database for Agent UI sessions, messages, and documents."""

    def __init__(self, db_path: str | None = None):
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

        # Add inference_stats column for persisting LLM performance metrics
        try:
            cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
            ]
            if "inference_stats" not in cols:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN inference_stats TEXT"
                )
                self._conn.commit()
                logger.info("Migrated messages table: added inference_stats column")
        except Exception as e:
            logger.debug("Migration check for inference_stats: %s", e)

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

        # Add last_error column for persisting indexing failure messages
        try:
            doc_cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(documents)").fetchall()
            ]
            if "last_error" not in doc_cols:
                self._conn.execute("ALTER TABLE documents ADD COLUMN last_error TEXT")
                self._conn.commit()
                logger.info("Migrated documents table: added last_error column")
        except Exception as e:
            logger.debug("Migration check for last_error: %s", e)

        # Add private column to sessions for per-session incognito mode,
        # and agent_type column for per-session agent selection.
        try:
            sess_cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
            ]
            if "private" not in sess_cols:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN private INTEGER NOT NULL DEFAULT 0"
                )
                self._conn.commit()
                logger.info("Migrated sessions table: added private column")
            if "agent_type" not in sess_cols:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN agent_type TEXT DEFAULT 'chat'"
                )
                # SQLite ALTER TABLE DEFAULT doesn't backfill existing rows
                self._conn.execute(
                    "UPDATE sessions SET agent_type = 'chat' WHERE agent_type IS NULL"
                )
                self._conn.commit()
                logger.info("Migrated sessions table: added agent_type column")
        except Exception as e:
            logger.debug("Migration check for sessions columns: %s", e)

        # Add device column for multi-device support (issue #1220)
        try:
            sess_cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
            ]
            if "device" not in sess_cols:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN device TEXT DEFAULT 'gpu'"
                )
                self._conn.execute(
                    "UPDATE sessions SET device = 'gpu' WHERE device IS NULL"
                )
                self._conn.commit()
                logger.info("Migrated sessions table: added device column")
        except Exception as e:
            logger.debug("Migration check for device column: %s", e)

        # Add mail_provider column for per-session email-backend selection
        # (Gmail vs Outlook). See EmailAgentConfig.mail_provider.
        try:
            sess_cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
            ]
            if "mail_provider" not in sess_cols:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN mail_provider TEXT DEFAULT 'google'"
                )
                self._conn.execute(
                    "UPDATE sessions SET mail_provider = 'google' WHERE mail_provider IS NULL"
                )
                self._conn.commit()
                logger.info("Migrated sessions table: added mail_provider column")
        except Exception as e:
            logger.debug("Migration check for mail_provider column: %s", e)

        # Add title_is_custom column so the auto-retitler can tell explicit
        # titles from its own (#2165). Backfill: existing non-placeholder
        # titles could be user-set or auto-generated — pin them all, since
        # wrongly renaming a user's title is worse than never renaming.
        try:
            sess_cols = [
                row[1]
                for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
            ]
            if "title_is_custom" not in sess_cols:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN title_is_custom INTEGER NOT NULL DEFAULT 0"
                )
                placeholders = ", ".join("?" for _ in PLACEHOLDER_TITLES)
                self._conn.execute(
                    f"UPDATE sessions SET title_is_custom = 1 "
                    f"WHERE LOWER(TRIM(COALESCE(title, ''))) NOT IN ({placeholders})",
                    tuple(PLACEHOLDER_TITLES),
                )
                self._conn.commit()
                logger.info("Migrated sessions table: added title_is_custom column")
        except Exception as e:
            logger.debug("Migration check for title_is_custom column: %s", e)

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
        title: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        document_ids: List[str] | None = None,
        private: bool = False,
        agent_type: str | None = None,
        device: str | None = None,
        mail_provider: str | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new chat session."""
        session_id = str(uuid.uuid4())
        now = self._now()
        model = model or SESSION_DEFAULT_MODEL
        # A non-placeholder title at creation is an explicit caller choice —
        # pin it so the auto-retitler never overwrites it (#2165).
        title_is_custom = 0 if is_placeholder_title(title) else 1
        title = title or "New Chat"
        agent_type = agent_type or "chat"
        device = device or "gpu"
        # mail_provider is a FILTER (#1596): no pick stays NULL ("every
        # connected mailbox") — never silently coerce to google.

        with self._transaction():
            self._conn.execute(
                """INSERT INTO sessions (id, title, title_is_custom, created_at, updated_at, model, system_prompt, private, agent_type, device, mail_provider)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    title,
                    title_is_custom,
                    now,
                    now,
                    model,
                    system_prompt,
                    1 if private else 0,
                    agent_type,
                    device,
                    mail_provider,
                ),
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
            return int(row["cnt"])

    def update_session(
        self,
        session_id: str,
        title: str | None = None,
        system_prompt: str | None = None,
        document_ids: list | None = None,
        private: bool | None = None,
        agent_type: str | None = None,
        device: str | None = None,
        model: str | None = None,
        mail_provider: str | None = None,
        title_is_custom: bool | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Update session title, system prompt, agent_type, device, model, mail_provider, private flag, and/or document_ids.

        ``title_is_custom`` pins/unpins the title against auto-retitling
        (#2165); omit it (None) to leave the pin unchanged — the backend
        auto-titler relies on that so its own updates never pin.
        """
        updates: list[str] = []
        params: list[Any] = []

        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if title_is_custom is not None:
            updates.append("title_is_custom = ?")
            params.append(1 if title_is_custom else 0)
        if model is not None:
            updates.append("model = ?")
            params.append(model)
        if system_prompt is not None:
            updates.append("system_prompt = ?")
            params.append(system_prompt)
        if private is not None:
            updates.append("private = ?")
            params.append(1 if private else 0)
        if agent_type is not None:
            updates.append("agent_type = ?")
            params.append(agent_type)
        if device is not None:
            updates.append("device = ?")
            params.append(device)
        if mail_provider is not None:
            updates.append("mail_provider = ?")
            params.append(mail_provider)

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
        rag_sources: List[Dict] | None = None,
        agent_steps: List[Dict] | None = None,
        tokens_prompt: int | None = None,
        tokens_completion: int | None = None,
        inference_stats: Dict | None = None,
    ) -> int:
        """Add a message to a session. Returns message ID."""
        sources_json = json.dumps(rag_sources) if rag_sources else None
        steps_json = json.dumps(agent_steps) if agent_steps else None
        stats_json = json.dumps(inference_stats) if inference_stats else None

        with self._transaction():
            cursor = self._conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, created_at, rag_sources,
                    agent_steps, tokens_prompt, tokens_completion, inference_stats)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    content,
                    self._now(),
                    sources_json,
                    steps_json,
                    tokens_prompt,
                    tokens_completion,
                    stats_json,
                ),
            )

            # Update session timestamp
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (self._now(), session_id),
            )
            msg_id = cursor.lastrowid

        return msg_id or 0

    def upsert_message(
        self,
        session_id: str,
        msg_id: int | None,
        role: str,
        content: str,
        rag_sources: List[Dict] | None = None,
        agent_steps: List[Dict] | None = None,
        tokens_prompt: int | None = None,
        tokens_completion: int | None = None,
        inference_stats: Dict | None = None,
    ) -> int:
        """Atomically replace a message with a fresh row. Returns the new ID.

        When ``msg_id`` is not None the old row is deleted and the new row is
        inserted in the SAME transaction, so a crash or exception can never leave
        the session without the message (the failure mode of a separate
        ``delete_message`` + ``add_message``). A new id is minted rather than
        reusing ``msg_id`` to keep the delete-then-insert id semantics.
        """
        sources_json = json.dumps(rag_sources) if rag_sources else None
        steps_json = json.dumps(agent_steps) if agent_steps else None
        stats_json = json.dumps(inference_stats) if inference_stats else None

        with self._transaction():
            if msg_id is not None:
                self._conn.execute(
                    "DELETE FROM messages WHERE id = ? AND session_id = ?",
                    (msg_id, session_id),
                )

            cursor = self._conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, created_at, rag_sources,
                    agent_steps, tokens_prompt, tokens_completion, inference_stats)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    content,
                    self._now(),
                    sources_json,
                    steps_json,
                    tokens_prompt,
                    tokens_completion,
                    stats_json,
                ),
            )

            # Update session timestamp
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (self._now(), session_id),
            )
            new_id = cursor.lastrowid

        return new_id or 0

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
            if msg.get("inference_stats"):
                try:
                    msg["inference_stats"] = json.loads(msg["inference_stats"])
                except (json.JSONDecodeError, TypeError):
                    msg["inference_stats"] = None
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
            return int(row["cnt"])

    # ── Documents ───────────────────────────────────────────────────────

    def add_document(
        self,
        filename: str,
        filepath: str,
        file_hash: str,
        file_size: int = 0,
        chunk_count: int = 0,
        file_mtime: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
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

    def get_document_by_hash(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """Get document by its SHA-256 content hash.

        Used by the blob upload endpoint to short-circuit re-indexing when
        the same file content has already been uploaded.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE file_hash = ?", (file_hash,)
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
        self,
        doc_id: str,
        status: str,
        chunk_count: int | None = None,
        last_error: str | None = _UNSET,
    ) -> bool:
        """Update a document's indexing status and optionally its chunk count.

        Args:
            doc_id: Document ID.
            status: New status ('pending', 'indexing', 'complete', 'failed', 'cancelled').
            chunk_count: If provided, also update the chunk count.
            last_error: Controls the stored error message.
                        Omit (default) to leave the existing value unchanged.
                        Pass None to clear the column (SQL NULL).
                        Pass a string to store that error message.

        Returns:
            True if the document was found and updated.
        """
        with self._transaction():
            parts = ["indexing_status = ?"]
            params: list = [status]
            if chunk_count is not None:
                parts.append("chunk_count = ?")
                params.append(chunk_count)
            # Successful indexing always clears any prior error message.
            # For other statuses, only write last_error when explicitly passed.
            if status == "complete":
                parts.append("last_error = ?")
                params.append(None)
            elif last_error is not _UNSET:
                parts.append("last_error = ?")
                if (
                    isinstance(last_error, str)
                    and len(last_error) > _MAX_LAST_ERROR_LEN
                ):
                    last_error = last_error[: _MAX_LAST_ERROR_LEN - 1] + "…"
                params.append(last_error)
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

    def get_setting(self, key: str, default: str | None = None) -> Optional[str]:
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

    # ── Scheduled tasks ─────────────────────────────────────────────────

    def create_scheduled_task(self, row: Dict[str, Any]) -> None:
        """Insert a scheduled task row.

        Args:
            row: Mapping with keys id, name, interval_seconds, prompt, status,
                created_at, next_run_at, run_count, error_count, session_id,
                schedule_config.

        Raises:
            sqlite3.IntegrityError: if a task with the same name exists.
        """
        with self._transaction():
            self._conn.execute(
                """INSERT INTO scheduled_tasks
                   (id, name, interval_seconds, prompt, status, created_at,
                    next_run_at, run_count, error_count, session_id, schedule_config)
                   VALUES (:id, :name, :interval_seconds, :prompt, :status,
                           :created_at, :next_run_at, :run_count, :error_count,
                           :session_id, :schedule_config)""",
                row,
            )

    def update_scheduled_task(
        self,
        task_id: str,
        *,
        status: str,
        last_run_at: str | None = None,
        next_run_at: str | None = None,
        last_result: str | None = None,
        run_count: int = 0,
        error_count: int = 0,
        session_id: str | None = None,
        schedule_config: str | None = None,
    ) -> None:
        """Update the mutable fields of a scheduled task row."""
        with self._transaction():
            self._conn.execute(
                """UPDATE scheduled_tasks
                   SET status = ?, last_run_at = ?, next_run_at = ?, last_result = ?,
                       run_count = ?, error_count = ?, session_id = ?, schedule_config = ?
                   WHERE id = ?""",
                (
                    status,
                    last_run_at,
                    next_run_at,
                    last_result,
                    run_count,
                    error_count,
                    session_id,
                    schedule_config,
                    task_id,
                ),
            )

    def delete_scheduled_task(self, task_id: str) -> None:
        """Delete a scheduled task and its results."""
        with self._transaction():
            self._conn.execute(
                "DELETE FROM schedule_results WHERE task_id = ?", (task_id,)
            )
            self._conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))

    def list_scheduled_tasks(self) -> List[Dict[str, Any]]:
        """Load all scheduled task rows."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM scheduled_tasks").fetchall()
            return [dict(r) for r in rows]

    def store_schedule_result(
        self,
        result_id: str,
        task_id: str,
        executed_at: str,
        result: str | None,
        error: str | None,
    ) -> None:
        """Store one schedule execution result."""
        with self._transaction():
            self._conn.execute(
                """INSERT INTO schedule_results (id, task_id, executed_at, result, error)
                   VALUES (?, ?, ?, ?, ?)""",
                (result_id, task_id, executed_at, result, error),
            )

    def get_schedule_results(
        self, task_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get past execution results for a task, newest first."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM schedule_results WHERE task_id = ?
                   ORDER BY executed_at DESC LIMIT ?""",
                (task_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

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
