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
from typing import Any, Callable, Dict, List, Optional

from gaia.config import GaiaConfig, gaia_home
from gaia.utils.process_owner import current_owner, owner_is_gone

logger = logging.getLogger(__name__)


def default_db_path() -> Path:
    """Return the default chat database path under the resolved GAIA home."""
    return gaia_home() / "chat" / "gaia_chat.db"


DEFAULT_DB_PATH = default_db_path

# Hard-coded floor for new sessions — kept in sync with the SQL schema DEFAULT
# and any code that reads session["model"] and falls back when NULL. Not what a
# new session necessarily gets: resolved_default_model() checks the user's
# configured default_model first.
SESSION_DEFAULT_MODEL = "Gemma-4-E4B-it-GGUF"


def resolved_default_model() -> str:
    """The model a new session gets when the caller doesn't specify one.

    ~/.gaia/config.json's default_model wins when set, otherwise
    SESSION_DEFAULT_MODEL. Also used by routers/sessions.py's device-switch
    rewrite guard, which treats a configured default the same as the floor.
    """
    return GaiaConfig.load().resolve_model(None, SESSION_DEFAULT_MODEL)


# Auto-generated placeholder titles the auto-retitler may replace (#2165).
# Anything else is treated as explicitly chosen and pinned via title_is_custom.
SESSION_KINDS = ("chat", "eval", "scheduled")


def session_kind_for_title(title: str | None) -> str:
    """Eval and scheduler chats are named by their runner; a person's never are."""
    t = (title or "").lstrip()
    if t.startswith("Eval:"):
        return "eval"
    if t.startswith("Schedule:"):
        return "scheduled"
    return "chat"


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

-- What one chat may reach, set from the session panel (#5276).
-- folders: added by the user; withheld: removed, refused even under a wider grant.
CREATE TABLE IF NOT EXISTS session_environment (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    folders TEXT NOT NULL DEFAULT '[]',
    withheld TEXT NOT NULL DEFAULT '[]',
    internet_off INTEGER NOT NULL DEFAULT 0
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
    role TEXT CHECK(role IN ('user', 'assistant', 'system', 'autonomous')) NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    rag_sources TEXT,
    agent_steps TEXT,
    tokens_prompt INTEGER,
    tokens_completion INTEGER
);

-- A chat turn that has started and not yet saved its reply, one row per
-- question. Saving the reply deletes the row in the same transaction, so a row
-- whose owner has exited belongs to a turn that process never finished.
CREATE TABLE IF NOT EXISTS unfinished_turns (
    question_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    -- The server process running the turn (process_owner.current_owner).
    owner TEXT NOT NULL,
    started_at TEXT NOT NULL,
    partial_content TEXT NOT NULL DEFAULT '',
    -- JSON list of the tools the turn had started, in order.
    tools_run TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_unfinished_turns_owner
    ON unfinished_turns(session_id, owner);

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

-- One row per cloud chat turn; usd is NULL when the model has no known price.
CREATE TABLE IF NOT EXISTS cloud_spend (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT DEFAULT (datetime('now')),
    month         TEXT NOT NULL,
    model         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    usd           REAL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_cloud_spend_month ON cloud_spend(month);
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
            db_path = str(default_db_path())

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
        self._migrate_autonomous_role()

    def _migrate_autonomous_role(self) -> None:
        """Allow background turns while preserving the existing message table."""
        schema = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0]
        old_roles = "'user', 'assistant', 'system'"
        if "'autonomous'" in schema:
            return
        if old_roles not in schema:
            raise RuntimeError(
                "Cannot migrate messages role constraint; inspect the chat database schema."
            )
        indexes = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name = 'messages' "
            "AND type IN ('index', 'trigger') AND sql IS NOT NULL"
        ).fetchall()
        sequence = self._conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'messages'"
        ).fetchone()
        migrated = schema.replace(old_roles, old_roles + ", 'autonomous'")
        migrated = migrated.replace(
            "CREATE TABLE messages", "CREATE TABLE messages_history_migration", 1
        )
        stale = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'messages_history_migration'"
        ).fetchone()
        if stale:
            # ``messages`` still has the old schema, so it holds every row.
            logger.warning(
                "Dropping leftover messages_history_migration table before "
                "re-running the messages role migration"
            )
        with self._transaction():
            self._conn.execute("BEGIN")
            self._conn.execute("DROP TABLE IF EXISTS messages_history_migration")
            self._conn.execute(migrated)
            self._conn.execute(
                "INSERT INTO messages_history_migration SELECT * FROM messages"
            )
            self._conn.execute("DROP TABLE messages")
            self._conn.execute(
                "ALTER TABLE messages_history_migration RENAME TO messages"
            )
            for index in indexes:
                self._conn.execute(index[0])
            if sequence is not None:
                self._conn.execute(
                    "UPDATE sqlite_sequence SET seq = ? WHERE name = 'messages'",
                    (sequence[0],),
                )
        logger.info("Migrated messages table: enabled autonomous history")

    def _ensure_settings_table(self):
        """Create the settings key-value table if it doesn't exist."""
        self._conn.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
        self._conn.commit()

    def _add_column(
        self,
        table: str,
        column: str,
        ddl: str,
        backfill: str | None = None,
        params: tuple = (),
    ) -> None:
        """Add *column* to *table* unless present; a failure stops startup."""
        cols = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column in cols:
            return
        try:
            with self._transaction():
                # Explicit BEGIN so the ALTER and its backfill commit together.
                self._conn.execute("BEGIN")
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                if backfill:
                    self._conn.execute(backfill, params)
        except sqlite3.Error as e:
            raise RuntimeError(
                f"Could not upgrade the chat database at {self._db_path}: adding "
                f"{table}.{column} failed ({e}). Close other GAIA windows using it "
                "and restart; if it keeps failing, move the file aside to start fresh."
            ) from e
        logger.info("Migrated %s table: added %s column", table, column)

    def _migrate(self):
        """Apply incremental schema migrations for existing databases."""
        self._ensure_settings_table()
        self._add_column("messages", "model_messages", "TEXT")
        self._add_column("messages", "agent_steps", "TEXT")
        self._add_column("messages", "inference_stats", "TEXT")
        self._add_column("messages", "turn_summary", "TEXT")
        self._add_column("documents", "indexing_status", "TEXT DEFAULT 'complete'")
        self._add_column("documents", "file_mtime", "REAL")
        self._add_column("documents", "last_error", "TEXT")
        # The server indexing it, so a restart can tell a dead index from a live one.
        self._add_column("documents", "indexing_owner", "TEXT")
        # Per-session incognito mode and agent selection.
        self._add_column("sessions", "private", "INTEGER NOT NULL DEFAULT 0")
        self._add_column(
            "sessions",
            "agent_type",
            "TEXT DEFAULT 'chat'",
            "UPDATE sessions SET agent_type = 'chat' WHERE agent_type IS NULL",
        )
        self._add_column(
            "sessions",
            "device",
            "TEXT DEFAULT 'gpu'",
            "UPDATE sessions SET device = 'gpu' WHERE device IS NULL",
        )
        # Legacy rows predate the "every mailbox" filter (#1596) and stay on Gmail.
        self._add_column(
            "sessions",
            "mail_provider",
            "TEXT DEFAULT 'google'",
            "UPDATE sessions SET mail_provider = 'google' WHERE mail_provider IS NULL",
        )
        # Existing titles may be user-set; never auto-rename them (#2165).
        placeholders = ", ".join("?" for _ in PLACEHOLDER_TITLES)
        self._add_column(
            "sessions",
            "title_is_custom",
            "INTEGER NOT NULL DEFAULT 0",
            "UPDATE sessions SET title_is_custom = 1 "
            f"WHERE LOWER(TRIM(COALESCE(title, ''))) NOT IN ({placeholders})",
            tuple(PLACEHOLDER_TITLES),
        )

        # The chat list as a queue (#5221): pin, unread, how the last turn
        # ended, and whether a person or the eval/scheduler started the chat.
        self._add_column("sessions", "pinned", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("sessions", "unread", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("sessions", "last_run_status", "TEXT")
        self._add_column(
            "sessions",
            "kind",
            "TEXT NOT NULL DEFAULT 'chat'",
            "UPDATE sessions SET kind = CASE "
            "WHEN title LIKE 'Eval:%' THEN 'eval' "
            "WHEN title LIKE 'Schedule:%' THEN 'scheduled' "
            "ELSE 'chat' END",
        )

    @property
    def db_path(self) -> str:
        """The SQLite file this database opened, or ``:memory:``."""
        return self._db_path

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
        kind: str | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new chat session.

        ``kind`` defaults from the title (``Eval:`` / ``Schedule:`` prefixes),
        since the eval driver creates chats through the plain API.
        """
        session_id = str(uuid.uuid4())
        now = self._now()
        # An explicit caller-supplied model wins; otherwise resolve against
        # the user's configured default_model.
        model = model or resolved_default_model()
        # A non-placeholder title at creation is an explicit caller choice —
        # pin it so the auto-retitler never overwrites it (#2165).
        title_is_custom = 0 if is_placeholder_title(title) else 1
        title = title or "New Chat"
        # The flagship, not `chat`: callers that don't pass an agent_type (API
        # clients, the scheduler) would otherwise land on a hidden agent the
        # picker no longer offers.
        agent_type = agent_type or "gaia"
        device = device or "gpu"
        kind = kind or session_kind_for_title(title)
        if kind not in SESSION_KINDS:
            raise ValueError(
                f"Unknown session kind {kind!r}; expected one of {SESSION_KINDS}"
            )
        # mail_provider is a FILTER (#1596): no pick stays NULL ("every
        # connected mailbox") — never silently coerce to google.

        with self._transaction():
            self._conn.execute(
                """INSERT INTO sessions (id, title, title_is_custom, created_at, updated_at, model, system_prompt, private, agent_type, device, mail_provider, kind)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    kind,
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
                          (SELECT COUNT(*) FROM messages WHERE session_id = s.id AND role != 'autonomous') as message_count
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
                          (SELECT COUNT(*) FROM messages WHERE session_id = s.id AND role != 'autonomous') as message_count
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

    def set_session_pinned(self, session_id: str, pinned: bool) -> bool:
        """Pin or unpin a chat. Leaves ``updated_at`` alone so it keeps its place."""
        with self._transaction():
            cur = self._conn.execute(
                "UPDATE sessions SET pinned = ? WHERE id = ?",
                (1 if pinned else 0, session_id),
            )
        return cur.rowcount > 0

    def mark_session_read(self, session_id: str) -> bool:
        """Clear a chat's unread flag (the user opened it)."""
        with self._transaction():
            cur = self._conn.execute(
                "UPDATE sessions SET unread = 0 WHERE id = ?", (session_id,)
            )
        return cur.rowcount > 0

    def finish_session_run(self, session_id: str, status: str) -> bool:
        """Record how a turn ended (``done``/``failed``/``stopped``) and mark it unread."""
        if status not in ("done", "failed", "stopped"):
            raise ValueError(f"Unknown run status {status!r}")
        with self._transaction():
            cur = self._conn.execute(
                "UPDATE sessions SET last_run_status = ?, unread = 1 WHERE id = ?",
                (status, session_id),
            )
        return cur.rowcount > 0

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
        model_messages: List[Dict] | None = None,
        turn_summary: Dict | None = None,
        ends_turn: bool = False,
    ) -> int:
        """Add a message to a session. Returns message ID.

        ``ends_turn`` marks it as the reply of this process's running turn in
        the session, whose unfinished-turn row is deleted with it.
        """
        sources_json = json.dumps(rag_sources) if rag_sources else None
        steps_json = json.dumps(agent_steps) if agent_steps else None
        stats_json = json.dumps(inference_stats) if inference_stats else None
        model_json = json.dumps(model_messages) if model_messages is not None else None
        summary_json = json.dumps(turn_summary) if turn_summary else None

        with self._transaction():
            cursor = self._conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, created_at, rag_sources,
                    agent_steps, tokens_prompt, tokens_completion, inference_stats, model_messages,
                    turn_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    model_json,
                    summary_json,
                ),
            )

            # Update session timestamp
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (self._now(), session_id),
            )
            msg_id = cursor.lastrowid
            if ends_turn:
                self._end_own_turn(session_id)

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
        model_messages: List[Dict] | None = None,
        turn_summary: Dict | None = None,
        ends_turn: bool = False,
    ) -> int:
        """Atomically replace a message with a fresh row. Returns the new ID.

        When ``msg_id`` is not None the old row is deleted and the new row is
        inserted in the SAME transaction, so a crash or exception can never leave
        the session without the message (the failure mode of a separate
        ``delete_message`` + ``add_message``). A new id is minted rather than
        reusing ``msg_id`` to keep the delete-then-insert id semantics.

        ``ends_turn`` marks it as the reply of this process's running turn,
        as in :meth:`add_message`.
        """
        sources_json = json.dumps(rag_sources) if rag_sources else None
        steps_json = json.dumps(agent_steps) if agent_steps else None
        stats_json = json.dumps(inference_stats) if inference_stats else None
        model_json = json.dumps(model_messages) if model_messages is not None else None
        summary_json = json.dumps(turn_summary) if turn_summary else None

        with self._transaction():
            if msg_id is not None:
                self._conn.execute(
                    "DELETE FROM messages WHERE id = ? AND session_id = ?",
                    (msg_id, session_id),
                )

            cursor = self._conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, created_at, rag_sources,
                    agent_steps, tokens_prompt, tokens_completion, inference_stats, model_messages,
                    turn_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    model_json,
                    summary_json,
                ),
            )

            # Update session timestamp
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (self._now(), session_id),
            )
            new_id = cursor.lastrowid
            if ends_turn:
                self._end_own_turn(session_id)

        return new_id or 0

    def get_messages(
        self, session_id: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get messages for a session, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM messages
                   WHERE session_id = ? AND role != 'autonomous'
                   ORDER BY created_at ASC, id ASC
                   LIMIT ? OFFSET ?""",
                (session_id, limit, offset),
            ).fetchall()

        return self._decode_messages(rows)

    def get_recent_messages(
        self, session_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get the newest messages in chronological order for agent context."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM (
                       SELECT * FROM messages WHERE session_id = ?
                       ORDER BY created_at DESC, id DESC LIMIT ?
                   ) ORDER BY created_at ASC, id ASC""",
                (session_id, limit),
            ).fetchall()
        return self._decode_messages(rows)

    @staticmethod
    def _decode_messages(rows) -> List[Dict[str, Any]]:
        messages = []
        for row in rows:
            msg = dict(row)
            msg.pop("model_messages", None)
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
            if msg.get("turn_summary"):
                try:
                    msg["turn_summary"] = json.loads(msg["turn_summary"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        "Message %s has an unreadable turn summary: %s",
                        msg.get("id"),
                        exc,
                    )
                    msg["turn_summary"] = None
            messages.append(msg)

        return messages

    def get_turn_summaries(self, session_id: str) -> List[Dict[str, Any]]:
        """Every answer's id, time and turn summary, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, turn_summary FROM messages "
                "WHERE session_id = ? AND role = 'assistant' "
                "AND turn_summary IS NOT NULL ORDER BY created_at ASC, id ASC",
                (session_id,),
            ).fetchall()
        out = []
        for row in rows:
            try:
                summary = json.loads(row["turn_summary"])
            except ValueError as e:
                raise RuntimeError(
                    f"Message {row['id']} in chat {session_id} has an unreadable "
                    f"turn summary ({e})."
                ) from e
            out.append(
                {"id": row["id"], "created_at": row["created_at"], "summary": summary}
            )
        return out

    def get_message_times(self, session_id: str) -> Dict[int, str]:
        """Message id -> when it was written, for every message in the chat."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        return {row["id"]: row["created_at"] for row in rows}

    def get_context_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Read model history separately from paginated UI activity metadata."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, model_messages FROM messages "
                "WHERE session_id = ? ORDER BY created_at ASC, id ASC",
                (session_id,),
            ).fetchall()
        messages = []
        for row in rows:
            message = dict(row)
            if message["model_messages"] is not None:
                try:
                    message["model_messages"] = json.loads(message["model_messages"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"Cannot restore model history for session {session_id}; "
                        "inspect the messages table in the chat database."
                    ) from exc
            messages.append(message)
        return messages

    def latest_message_id(
        self, session_id: str, role: Optional[str] = None
    ) -> Optional[int]:
        """The id of the newest message (of *role*, if given), or None when none."""
        role_clause = " AND role = ?" if role else ""
        params = (session_id, role) if role else (session_id,)
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM messages WHERE session_id = ?"
                + role_clause
                + " ORDER BY created_at DESC, id DESC LIMIT 1",
                params,
            ).fetchone()
        return row["id"] if row else None

    def get_message_record(
        self, session_id: str, message_id: int
    ) -> Optional[Dict[str, Any]]:
        """One message with its model messages and stats decoded."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, role, content, model_messages, inference_stats "
                "FROM messages WHERE session_id = ? AND id = ?",
                (session_id, message_id),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        for key in ("model_messages", "inference_stats"):
            if record[key] is not None:
                try:
                    record[key] = json.loads(record[key])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"Cannot read {key} of message {message_id} in session "
                        f"{session_id}; inspect the messages table in the chat "
                        "database."
                    ) from exc
        return record

    def turn_performance(self, session_id: str) -> List[Dict[str, Any]]:
        """Each answered turn's recorded performance, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, inference_stats FROM messages "
                "WHERE session_id = ? AND role = 'assistant' "
                "AND inference_stats IS NOT NULL ORDER BY created_at ASC, id ASC",
                (session_id,),
            ).fetchall()
        turns = []
        for row in rows:
            try:
                stats = json.loads(row["inference_stats"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Cannot read inference_stats of message {row['id']} in session "
                    f"{session_id}; inspect the messages table in the chat database."
                ) from exc
            performance = stats.get("performance") if isinstance(stats, dict) else None
            if isinstance(performance, dict):
                turns.append(
                    {
                        "message_id": row["id"],
                        "created_at": row["created_at"],
                        **performance,
                    }
                )
        return turns

    def replace_answer(
        self,
        session_id: str,
        message_id: int,
        content: str,
        model_messages: List[Dict] | None,
        inference_stats: Dict | None,
    ) -> bool:
        """Swap an assistant message's answer in place, keeping its id and steps."""
        model_json = json.dumps(model_messages) if model_messages is not None else None
        stats_json = json.dumps(inference_stats) if inference_stats else None
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE messages SET content = ?, model_messages = ?, "
                "inference_stats = ? "
                "WHERE session_id = ? AND id = ? AND role = 'assistant'",
                (content, model_json, stats_json, session_id, message_id),
            )
        return cursor.rowcount > 0

    def delete_message(self, session_id: str, message_id: int) -> bool:
        """Delete a single message by ID.

        Args:
            session_id: Session the message belongs to (for safety).
            message_id: ID of the message to delete.

        Returns:
            True if a message was deleted, False if not found.
        """
        with self._transaction():
            self._conn.execute(
                "UPDATE messages SET model_messages = NULL "
                "WHERE id = (SELECT id FROM messages WHERE session_id = ? AND id > ? "
                "AND role IN ('user', 'assistant') ORDER BY id LIMIT 1) "
                "AND role = 'assistant' AND EXISTS "
                "(SELECT 1 FROM messages WHERE id = ? AND session_id = ? AND role = 'user')",
                (session_id, message_id, message_id, session_id),
            )
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
                "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ? AND role != 'autonomous'",
                (session_id,),
            ).fetchone()
            return int(row["cnt"])

    # ── Unfinished turns ────────────────────────────────────────────────

    def begin_turn(self, session_id: str, question: str) -> int:
        """Save the user's *question* and mark its reply as started, together.

        The row is this process's: only this process saves into or closes it,
        and another server closes it only once this process has exited.
        Returns the question's message id.
        """
        now = self._now()
        with self._transaction():
            cursor = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) "
                "VALUES (?, 'user', ?, ?)",
                (session_id, question, now),
            )
            question_id = cursor.lastrowid
            self._conn.execute(
                """INSERT INTO unfinished_turns
                   (question_id, session_id, owner, started_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (question_id, session_id, current_owner(), now, now),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
        return question_id

    def _end_own_turn(self, session_id: str) -> None:
        # Inside the caller's transaction. A session runs one turn at a time
        # per process, so this is the turn the reply belongs to.
        self._conn.execute(
            "DELETE FROM unfinished_turns WHERE session_id = ? AND owner = ?",
            (session_id, current_owner()),
        )

    def own_turn(self, session_id: str) -> Optional[int]:
        """The question id of this process's unfinished turn in the session."""
        with self._lock:
            row = self._conn.execute(
                "SELECT question_id FROM unfinished_turns "
                "WHERE session_id = ? AND owner = ?",
                (session_id, current_owner()),
            ).fetchone()
        return row["question_id"] if row else None

    def save_turn_partial(
        self, question_id: int, content: str, tools_run: List[str]
    ) -> None:
        """Keep the reply streamed so far, and the tools it started."""
        with self._transaction():
            self._conn.execute(
                "UPDATE unfinished_turns SET partial_content = ?, tools_run = ?, "
                "updated_at = ? WHERE question_id = ? AND owner = ?",
                (
                    content,
                    json.dumps(tools_run),
                    self._now(),
                    question_id,
                    current_owner(),
                ),
            )

    def discard_turn(self, question_id: int) -> None:
        """Forget this process's turn, whose outcome the caller reported."""
        with self._transaction():
            self._conn.execute(
                "DELETE FROM unfinished_turns WHERE question_id = ? AND owner = ?",
                (question_id, current_owner()),
            )

    def turns_in_progress(self) -> List[Dict[str, Any]]:
        """Unfinished turns: ``question_id``, ``session_id`` and ``owner``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT question_id, session_id, owner FROM unfinished_turns"
            ).fetchall()
        return [dict(row) for row in rows]

    def close_turn(
        self,
        question_id: int,
        notice: str,
        owner: Optional[str] = None,
        tools_notice: Optional[Callable[[List[str]], str]] = None,
    ) -> Optional[int]:
        """Save *notice* as the reply of an unfinished turn, and end it.

        *owner* is the process the turn must belong to, this one by default,
        so one server never closes another's live turn. Whatever the turn had
        streamed is kept above the notice; an empty notice keeps it alone.
        *tools_notice*, given the tools the turn had started, adds a line
        saying they ran. Returns the id of the reply written, or None when
        there was no such turn (it saved its own reply, or its question was
        deleted).
        """
        owner = owner or current_owner()
        with self._transaction():
            row = self._conn.execute(
                "SELECT session_id, partial_content, tools_run FROM unfinished_turns "
                "WHERE question_id = ? AND owner = ?",
                (question_id, owner),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "DELETE FROM unfinished_turns WHERE question_id = ? AND owner = ?",
                (question_id, owner),
            )
            tools = json.loads(row["tools_run"] or "[]")
            parts = [
                (row["partial_content"] or "").strip(),
                tools_notice(tools) if tools and tools_notice else "",
                notice,
            ]
            content = "\n\n".join(part for part in parts if part)
            if not content:
                raise ValueError(
                    f"close_turn for question {question_id} has neither "
                    "streamed text nor a notice to save as its reply"
                )
            now = self._now()
            cursor = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) "
                "VALUES (?, 'assistant', ?, ?)",
                (row["session_id"], content, now),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, row["session_id"]),
            )
            return cursor.lastrowid

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

    def get_session_environment(self, session_id: str) -> Dict[str, Any]:
        """The chat's added and removed folders and its internet switch."""
        with self._lock:
            row = self._conn.execute(
                "SELECT folders, withheld, internet_off FROM session_environment "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return {"folders": [], "withheld": [], "internet_off": False}
        try:
            folders, withheld = json.loads(row["folders"]), json.loads(row["withheld"])
        except ValueError as e:
            raise RuntimeError(
                f"The saved folders for chat {session_id} are unreadable ({e}). "
                "Remove and re-add them in the session panel."
            ) from e
        return {
            "folders": folders,
            "withheld": withheld,
            "internet_off": bool(row["internet_off"]),
        }

    def set_session_environment(
        self,
        session_id: str,
        *,
        folders: List[str],
        withheld: List[str],
        internet_off: bool,
    ) -> None:
        """Replace the chat's environment settings."""
        with self._transaction():
            self._conn.execute(
                """INSERT INTO session_environment
                       (session_id, folders, withheld, internet_off)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       folders = excluded.folders,
                       withheld = excluded.withheld,
                       internet_off = excluded.internet_off""",
                (
                    session_id,
                    json.dumps(folders),
                    json.dumps(withheld),
                    1 if internet_off else 0,
                ),
            )

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
            parts = ["indexing_status = ?", "indexing_owner = ?"]
            params: list = [
                status,
                current_owner() if status in ("pending", "indexing") else None,
            ]
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

    def fail_unfinished_indexing(self, error: str) -> List[str]:
        """Mark documents left pending or indexing by a process that has ended
        as failed with *error*. Returns their ids."""
        with self._transaction():
            rows = self._conn.execute(
                "SELECT id, indexing_owner FROM documents "
                "WHERE indexing_status IN ('pending', 'indexing')"
            ).fetchall()
            ids = [row["id"] for row in rows if owner_is_gone(row["indexing_owner"])]
            self._conn.executemany(
                "UPDATE documents SET indexing_status = 'failed', last_error = ?, "
                "last_accessed_at = ? WHERE id = ?",
                [(error, self._now(), doc_id) for doc_id in ids],
            )
        return ids

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

    def recent_inference_stats(self, limit: int = 500) -> List[Dict]:
        """Inference stats of the newest assistant messages that recorded any."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT inference_stats FROM messages WHERE role = 'assistant' "
                "AND inference_stats IS NOT NULL ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for row in rows:
            try:
                stats = json.loads(row["inference_stats"])
            except ValueError:
                logger.warning("Skipping a message with unreadable inference_stats")
                continue
            if isinstance(stats, dict):
                out.append(stats)
        return out

    # ── Cloud spending ────────────────────────────────────────────────

    def add_cloud_spend(
        self,
        month: str,
        model: str,
        provider: str,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        usd: Optional[float],
    ) -> None:
        """Record one cloud chat turn; *usd* None when its price is unknown."""
        with self._transaction():
            self._conn.execute(
                "INSERT INTO cloud_spend (month, model, provider, input_tokens, "
                "output_tokens, usd) VALUES (?, ?, ?, ?, ?, ?)",
                (month, model, provider, input_tokens, output_tokens, usd),
            )

    def cloud_spend_for_month(self, month: str) -> List[Dict]:
        """Every cloud turn recorded in *month* (``YYYY-MM``)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT model, provider, input_tokens, output_tokens, usd "
                "FROM cloud_spend WHERE month = ? ORDER BY id",
                (month,),
            ).fetchall()
        return [dict(r) for r in rows]

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

    def claim_scheduled_run(
        self, task_id: str, expected_next_run_at: str | None, next_run_at: str
    ) -> bool:
        """Take the run due at *expected_next_run_at*, moving the task on to
        *next_run_at*. False when another server sharing this database
        already took it (the stored time no longer matches), or paused or
        cancelled the task."""
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE scheduled_tasks SET next_run_at = ? "
                "WHERE id = ? AND next_run_at IS ? AND status = 'active'",
                (next_run_at, task_id, expected_next_run_at),
            )
            return cursor.rowcount == 1

    def scheduled_task_timing(self, task_id: str) -> Dict[str, Any]:
        """The task's stored ``status`` and ``next_run_at``, as another server
        may have set them."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status, next_run_at FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"No scheduled task {task_id!r} in the chat database")
        return dict(row)

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
