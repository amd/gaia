# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``CustodyStore`` — the daemon's single-writer SQLite backing for the custody
API (design §0.9, §0.29; breakdown V2-12).

Every table carries an ``agent_id`` column and every method takes ``agent_id``
and filters on it, so the store enforces per-agent scoping at the data layer
(the route layer enforces it again at the boundary — defense in depth). The
store is the daemon's own single writer: opened WAL + ``busy_timeout`` (§0.29)
so concurrent readers never block the writer and a contended write waits rather
than failing with ``database is locked``.

v1 scope (per the issue):
- **memory** — agent-scoped items, optional ``user`` shared scope; substring query.
- **sessions / session_messages** — host-minted session id is the authorization
  key (§0.30): ``get_session`` verifies ownership before returning a transcript.
- **audit** — plain append-only log with a per-store monotonic ``seq`` (§0.35.5;
  hash-chain deferred). ``action_id`` is the per-agent idempotency key.
- **rag_chunks** — agent-scoped corpus with a naive substring match (RAG feature
  changes are out of scope — this is the scoped round-trip, not a new retriever).

Threading: one connection with ``check_same_thread=False`` guarded by a lock, so
the daemon's threadpool routes share the single writer safely.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from gaia.daemon.custody.constants import (
    MEMORY_SCOPE_AGENT,
    VALID_MEMORY_SCOPES,
)
from gaia.daemon.custody.errors import (
    AuditConflictError,
    InvalidScopeError,
    ScopeDeniedError,
    SessionNotFoundError,
    StoreUnavailableError,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

# Wait up to this long for a contended write rather than failing loud with
# "database is locked" — the single-writer design means contention is brief.
_BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    scope       TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_agent_scope ON memory(agent_id, scope);

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id);

CREATE TABLE IF NOT EXISTS session_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_msgs_session ON session_messages(session_id, seq);

CREATE TABLE IF NOT EXISTS audit (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    action_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    summary     TEXT NOT NULL,
    ts          REAL NOT NULL,
    UNIQUE (agent_id, action_id)
);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit(agent_id);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rag_agent ON rag_chunks(agent_id);
"""


class CustodyStore:
    """SQLite-backed per-agent custody store (single writer, WAL)."""

    def __init__(self, db_path: "str | Path"):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        except sqlite3.Error as e:
            raise StoreUnavailableError(
                f"custody store at {self.db_path} could not be opened: {e}. "
                "Check the daemon has write access to its host dir "
                "(~/.gaia/host or $GAIA_DAEMON_HOME) and that the disk is not "
                "full, then restart the daemon."
            ) from e

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # -- memory --------------------------------------------------------------

    @staticmethod
    def _check_scope(scope: str) -> str:
        if scope not in VALID_MEMORY_SCOPES:
            raise InvalidScopeError(
                f"unknown memory scope '{scope}'; expected one of "
                f"{VALID_MEMORY_SCOPES}. Omit 'scope' for the agent-private "
                "default."
            )
        return scope

    def add_memory(
        self, agent_id: str, content: str, scope: str = MEMORY_SCOPE_AGENT
    ) -> int:
        """Persist a memory item for *agent_id*; return its row id."""
        self._check_scope(scope)
        now = time.time()
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO memory (agent_id, scope, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (agent_id, scope, content, now),
                )
                self._conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to write memory for agent '{agent_id}': {e}"
                ) from e

    def get_memory(
        self,
        agent_id: str,
        scope: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 100,
    ) -> "list[dict]":
        """Return *agent_id*'s memory items, optionally filtered by scope/substring.

        Scoping is enforced here: only rows tagged to *agent_id* are ever
        returned — a caller can never widen the read to another agent's memory.
        """
        sql = "SELECT id, scope, content, created_at FROM memory WHERE agent_id = ?"
        params: list = [agent_id]
        if scope is not None:
            self._check_scope(scope)
            sql += " AND scope = ?"
            params.append(scope)
        if query:
            sql += " AND content LIKE ?"
            params.append(f"%{query}%")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to read memory for agent '{agent_id}': {e}"
                ) from e
        return [
            {
                "id": r["id"],
                "scope": r["scope"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # -- sessions ------------------------------------------------------------

    def create_session(self, agent_id: str, session_id: str) -> str:
        """Register a host-minted *session_id* as owned by *agent_id*."""
        now = time.time()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO sessions (session_id, agent_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (session_id, agent_id, now),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                raise ScopeDeniedError(
                    f"session '{session_id}' already exists; a session id is "
                    "minted once and cannot be re-registered."
                ) from e
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to create session for '{agent_id}': {e}"
                ) from e
        return session_id

    def append_session_message(
        self, agent_id: str, session_id: str, role: str, content: str
    ) -> int:
        """Append a transcript message; verifies *agent_id* owns *session_id*."""
        with self._lock:
            owner = self._session_owner(session_id)
            if owner is None:
                raise SessionNotFoundError(
                    f"session '{session_id}' does not exist. Create it "
                    "(POST /host/v1/sessions) before appending messages."
                )
            if owner != agent_id:
                raise ScopeDeniedError(
                    f"session '{session_id}' belongs to another agent; agent "
                    f"'{agent_id}' may not write to it."
                )
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM "
                    "session_messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                seq = int(row["next"])
                self._conn.execute(
                    "INSERT INTO session_messages "
                    "(session_id, seq, role, content, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, seq, role, content, time.time()),
                )
                self._conn.commit()
                return seq
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to append to session " f"'{session_id}': {e}"
                ) from e

    def get_session(self, agent_id: str, session_id: str) -> "list[dict]":
        """Return *session_id*'s transcript slice IFF *agent_id* owns it.

        The §0.30 authorization key: the daemon verifies the session belongs to
        the caller. A session owned by another agent raises
        :class:`ScopeDeniedError` (403), a nonexistent one
        :class:`SessionNotFoundError` (404) — never a silent empty transcript.
        """
        with self._lock:
            owner = self._session_owner(session_id)
            if owner is None:
                raise SessionNotFoundError(f"session '{session_id}' does not exist.")
            if owner != agent_id:
                raise ScopeDeniedError(
                    f"session '{session_id}' belongs to another agent; agent "
                    f"'{agent_id}' may not read it."
                )
            try:
                rows = self._conn.execute(
                    "SELECT seq, role, content, created_at FROM session_messages "
                    "WHERE session_id = ? ORDER BY seq ASC",
                    (session_id,),
                ).fetchall()
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to read session '{session_id}': {e}"
                ) from e
        return [
            {
                "seq": r["seq"],
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def _session_owner(self, session_id: str) -> Optional[str]:
        """agent_id that owns *session_id*, or None. Caller holds ``self._lock``."""
        row = self._conn.execute(
            "SELECT agent_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row["agent_id"] if row is not None else None

    # -- audit ---------------------------------------------------------------

    def append_audit(
        self, agent_id: str, action_id: str, action: str, summary: str, ts: float
    ) -> int:
        """Append an audit row for *agent_id*; return the store-monotonic ``seq``.

        Append-only (§0.35.5). A duplicate ``(agent_id, action_id)`` raises
        :class:`AuditConflictError` (409) so a retried write is idempotent-safe
        rather than silently double-logged.
        """
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO audit (agent_id, action_id, action, summary, ts) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (agent_id, action_id, action, summary, ts),
                )
                self._conn.commit()
                return int(cur.lastrowid)
            except sqlite3.IntegrityError as e:
                raise AuditConflictError(
                    f"audit action_id '{action_id}' already recorded for agent "
                    f"'{agent_id}'. action_id is the idempotency key — use a "
                    "fresh id per distinct action."
                ) from e
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to append audit for '{agent_id}': {e}"
                ) from e

    def get_audit(self, agent_id: str, limit: int = 100) -> "list[dict]":
        """Return *agent_id*'s audit rows (host-internal read; agents are
        write-only to audit per §0.24 — this backs the dashboard/tests)."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT seq, action_id, action, summary, ts FROM audit "
                    "WHERE agent_id = ? ORDER BY seq ASC LIMIT ?",
                    (agent_id, int(limit)),
                ).fetchall()
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to read audit for '{agent_id}': {e}"
                ) from e
        return [
            {
                "seq": r["seq"],
                "action_id": r["action_id"],
                "action": r["action"],
                "summary": r["summary"],
                "ts": r["ts"],
            }
            for r in rows
        ]

    # -- rag -----------------------------------------------------------------

    def add_rag_chunk(
        self, agent_id: str, content: str, source: Optional[str] = None
    ) -> int:
        """Add one chunk to *agent_id*'s corpus; return its row id."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO rag_chunks (agent_id, content, source, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (agent_id, content, source, time.time()),
                )
                self._conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to write a RAG chunk for "
                    f"'{agent_id}': {e}"
                ) from e

    def query_rag(self, agent_id: str, query: str, k: int = 4) -> "list[dict]":
        """Return up to *k* of *agent_id*'s chunks matching *query* (substring).

        v1 is a naive scoped match — the embedder-backed retriever is a later
        issue. The invariant that matters now is scoping: only *agent_id*'s
        corpus is ever searched.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT id, content, source, created_at FROM rag_chunks "
                    "WHERE agent_id = ? AND content LIKE ? "
                    "ORDER BY id DESC LIMIT ?",
                    (agent_id, f"%{query}%", int(k)),
                ).fetchall()
            except sqlite3.Error as e:
                raise StoreUnavailableError(
                    f"custody store failed to query RAG for '{agent_id}': {e}"
                ) from e
        return [
            {
                "id": r["id"],
                "content": r["content"],
                "source": r["source"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
