# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MemoryStore: Unified data layer for agent memory.

Agent-agnostic. Pure SQLite + FTS5. Zero imports from gaia.agents.

Single database (~/.gaia/memory.db) with three tables:
- conversations: Every conversation turn, persistent across sessions
- knowledge: Persistent facts, preferences, learnings — the "second brain"
- tool_history: Every tool call the agent makes, auto-logged

Features:
- FTS5 search with AND default, OR fallback, BM25 ranking
- Knowledge deduplication (>80% word overlap in same category+context)
- Confidence scoring with decay (0.9x after 30 days of no access)
- Context scoping (work, personal, global, project-specific)
- Sensitivity classification (excluded from default search)
- Entity linking (person:X, app:Y, service:Z)
- Temporal awareness (due_at, reminded_at, get_upcoming)
- Thread-safe via threading.Lock
- Dashboard aggregate queries
"""

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


# ============================================================================
# FTS5 Query Sanitization
# ============================================================================


def _sanitize_fts5_query(query: str, use_and: bool = True) -> Optional[str]:
    """Sanitize a query string for FTS5 MATCH.

    FTS5 treats characters like . : - @ as special syntax.
    Replace them with spaces so the query works as plain word search.

    Args:
        query: Raw search string.
        use_and: If True (default), join words with AND for tighter matching.
                 If False, join with OR for broader matching.

    Returns:
        Sanitized FTS5 query string, or None if query is empty/invalid.
    """
    if not query or not query.strip():
        return None

    # Cap length to bound regex work — FTS5 queries beyond 500 chars are
    # pathologically long and signal bad input rather than a real search.
    query = query[:500]

    # Replace FTS5 special chars with spaces, keep alphanumeric and underscores
    sanitized = re.sub(r"[^\w\s]", " ", query)
    # Collapse multiple spaces
    sanitized = re.sub(r"\s+", " ", sanitized).strip()

    if not sanitized:
        return None

    words = sanitized.split()
    if len(words) > 1:
        operator = " AND " if use_and else " OR "
        return operator.join(words)

    return sanitized


def _word_overlap(text1: str, text2: str) -> float:
    """Calculate word overlap ratio using Szymkiewicz-Simpson coefficient.

    |intersection| / min(|A|, |B|)

    A subset of a longer text still counts as a match — appropriate for dedup.
    """
    words1 = set(re.sub(r"[^\w\s]", " ", text1.lower()).split())
    words2 = set(re.sub(r"[^\w\s]", " ", text2.lower()).split())

    if not words1 or not words2:
        return 0.0

    intersection = words1 & words2
    min_size = min(len(words1), len(words2))

    return len(intersection) / min_size if min_size > 0 else 0.0


# ============================================================================
# Timestamp helper
# ============================================================================


def _now_iso() -> str:
    """Return current local time in ISO 8601 with timezone offset."""
    return datetime.now().astimezone().isoformat()


def _safe_json_loads(value) -> object:
    """Deserialize a JSON string, returning None on failure.

    Guards against corrupt data in metadata/args columns crashing entire
    list queries — a single bad row should not prevent the rest from loading.
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[MemoryStore] corrupt JSON column value ignored: %.80r", value)
        return None


# ============================================================================
# Schema SQL
# ============================================================================

_SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    migrated_at TEXT NOT NULL
);

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    context     TEXT DEFAULT 'global',
    timestamp   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_conv_context ON conversations(context);

-- Conversations FTS5 (external content, porter stemmer for morphological matching)
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
    content,
    content=conversations,
    content_rowid=id,
    tokenize='porter unicode61'
);

-- Knowledge
CREATE TABLE IF NOT EXISTS knowledge (
    id          TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    domain      TEXT,
    source      TEXT NOT NULL DEFAULT 'tool',
    confidence  REAL DEFAULT 0.5,
    metadata    TEXT,
    use_count   INTEGER DEFAULT 0,
    context     TEXT DEFAULT 'global',
    sensitive   INTEGER DEFAULT 0,
    entity      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_used   TEXT,
    due_at      TEXT,
    reminded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_due ON knowledge(due_at)
    WHERE due_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_knowledge_context ON knowledge(context);
CREATE INDEX IF NOT EXISTS idx_knowledge_entity ON knowledge(entity)
    WHERE entity IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_knowledge_sensitive ON knowledge(sensitive)
    WHERE sensitive = 1;

-- Knowledge FTS5 (standalone, manually synced, porter stemmer for morphological matching)
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(content, domain, category, tokenize='porter unicode61');

-- Tool history
CREATE TABLE IF NOT EXISTS tool_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    args        TEXT,
    result_summary TEXT,
    success     INTEGER NOT NULL,
    error       TEXT,
    duration_ms INTEGER,
    timestamp   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_name ON tool_history(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_session ON tool_history(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_success ON tool_history(success);
CREATE INDEX IF NOT EXISTS idx_tool_ts ON tool_history(timestamp DESC);
"""

# Sync triggers for conversations_fts (external-content FTS5 table).
# Only INSERT and DELETE triggers are defined because conversation turns are
# append-only — store_turn() only ever INSERTs, never UPDATEs existing rows.
# If UPDATE support is added in the future, a corresponding AFTER UPDATE
# trigger must be added here to keep the FTS index in sync.
# Triggers are created separately (can't use IF NOT EXISTS on triggers
# in all SQLite versions, so we catch the error).
_TRIGGER_SQL = [
    """
    CREATE TRIGGER conv_fts_ai AFTER INSERT ON conversations BEGIN
        INSERT INTO conversations_fts(rowid, content) VALUES (new.id, new.content);
    END
    """,
    """
    CREATE TRIGGER conv_fts_ad AFTER DELETE ON conversations BEGIN
        INSERT INTO conversations_fts(conversations_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
    END
    """,
]


# ============================================================================
# MemoryStore
# ============================================================================


class MemoryStore:
    """Pure SQLite storage for agent memory. No agent dependencies."""

    def __init__(self, db_path: Path = None):
        """Open/create DB at db_path. Default: ~/.gaia/memory.db

        Uses WAL mode. Thread-safe via threading.Lock.
        """
        if db_path is None:
            gaia_dir = Path.home() / ".gaia"
            gaia_dir.mkdir(parents=True, exist_ok=True)
            db_path = gaia_dir / "memory.db"
        else:
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()

        self._init_schema()
        logger.debug("[MemoryStore] initialized at %s", db_path)

    # ------------------------------------------------------------------
    # Schema initialization
    # ------------------------------------------------------------------

    def _init_schema(self):
        """Create tables, indexes, triggers, and set WAL mode."""
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA_SQL)

            # Create triggers (ignore if already exist)
            for trigger_sql in _TRIGGER_SQL:
                try:
                    self._conn.execute(trigger_sql)
                except sqlite3.OperationalError:
                    pass  # Trigger already exists

            # Initialize schema_version if empty
            cursor = self._conn.execute("SELECT COUNT(*) FROM schema_version")
            if cursor.fetchone()[0] == 0:
                self._conn.execute(
                    "INSERT INTO schema_version VALUES (?, ?)",
                    (1, _now_iso()),
                )

            self._conn.commit()

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute SQL with lock. Commits automatically."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def _row_to_knowledge_dict(self, row) -> Dict:
        """Convert a knowledge row tuple to a dict."""
        return {
            "id": row[0],
            "category": row[1],
            "content": row[2],
            "domain": row[3],
            "source": row[4],
            "confidence": row[5],
            "metadata": _safe_json_loads(row[6]),
            "use_count": row[7],
            "context": row[8],
            "sensitive": bool(row[9]),
            "entity": row[10],
            "created_at": row[11],
            "updated_at": row[12],
            "last_used": row[13],
            "due_at": row[14],
            "reminded_at": row[15],
        }

    _KNOWLEDGE_COLS = (
        "id, category, content, domain, source, confidence, metadata, "
        "use_count, context, sensitive, entity, created_at, updated_at, "
        "last_used, due_at, reminded_at"
    )

    # ==================================================================
    # Conversations
    # ==================================================================

    def store_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        context: str = "global",
    ) -> None:
        """Store one conversation turn.

        Truncates content to 4000 chars. Code-generation agents can produce
        very long responses; storing the full text would bloat the FTS index
        and slow down conversation queries without adding search value.
        Empty or whitespace-only turns are silently skipped — they add no
        signal to conversation history and pollute FTS5 with empty entries.
        """
        if not content or not content.strip():
            return  # Skip empty turns
        if len(content) > 4000:
            content = content[:4000]
        now = _now_iso()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO conversations (session_id, role, content, context, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, role, content, context, now),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_history(
        self,
        session_id: str = None,
        context: str = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Retrieve recent conversation turns, ordered oldest-first."""
        conditions = []
        params: list = []

        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        if context is not None:
            conditions.append("context = ?")
            params.append(context)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        sql = f"""
            SELECT id, session_id, role, content, context, timestamp
            FROM (
                SELECT id, session_id, role, content, context, timestamp
                FROM conversations
                {where}
                ORDER BY id DESC
                LIMIT ?
            ) sub ORDER BY id ASC
        """

        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            rows = cursor.fetchall()

        return [
            {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "context": r[4],
                "timestamp": r[5],
            }
            for r in rows
        ]

    def search_conversations(
        self,
        query: str,
        context: str = None,
        limit: int = 10,
    ) -> List[Dict]:
        """FTS5 keyword search across conversation content.

        Filterable by context. AND semantics with OR fallback.
        """
        safe_query = _sanitize_fts5_query(query, use_and=True)
        if not safe_query:
            return []

        with self._lock:
            results = self._fts5_search_conversations_locked(safe_query, context, limit)
            if not results:
                safe_query_or = _sanitize_fts5_query(query, use_and=False)
                if safe_query_or and safe_query_or != safe_query:
                    results = self._fts5_search_conversations_locked(
                        safe_query_or, context, limit
                    )

        return results

    def _fts5_search_conversations_locked(
        self, fts_query: str, context: Optional[str], limit: int
    ) -> List[Dict]:
        """Execute FTS5 search on conversations. Must hold self._lock."""
        try:
            if context is not None:
                cursor = self._conn.execute(
                    """
                    SELECT c.id, c.session_id, c.role, c.content, c.context, c.timestamp
                    FROM conversations c
                    JOIN conversations_fts f ON c.id = f.rowid
                    WHERE conversations_fts MATCH ? AND c.context = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, context, limit),
                )
            else:
                cursor = self._conn.execute(
                    """
                    SELECT c.id, c.session_id, c.role, c.content, c.context, c.timestamp
                    FROM conversations c
                    JOIN conversations_fts f ON c.id = f.rowid
                    WHERE conversations_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                )
            return [
                {
                    "id": r[0],
                    "session_id": r[1],
                    "role": r[2],
                    "content": r[3],
                    "context": r[4],
                    "timestamp": r[5],
                }
                for r in cursor.fetchall()
            ]
        except sqlite3.OperationalError as e:
            logger.debug("[MemoryStore] FTS5 conversation search error: %s", e)
            return []

    def get_recent_conversations(
        self,
        days: int = 7,
        context: str = None,
        limit: int = 50,
    ) -> List[Dict]:
        """Get conversations from the last N days (timestamp-based).

        Returns turns ordered oldest-first.
        """
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat()

        conditions = ["timestamp >= ?"]
        params: list = [cutoff]

        if context is not None:
            conditions.append("context = ?")
            params.append(context)

        where = "WHERE " + " AND ".join(conditions)
        params.append(limit)

        sql = f"""
            SELECT id, session_id, role, content, context, timestamp
            FROM conversations
            {where}
            ORDER BY id ASC
            LIMIT ?
        """

        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            rows = cursor.fetchall()

        return [
            {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "context": r[4],
                "timestamp": r[5],
            }
            for r in rows
        ]

    # ==================================================================
    # Knowledge — Store (with dedup)
    # ==================================================================

    def store(
        self,
        category: str,
        content: str,
        domain: str = None,
        metadata: dict = None,
        confidence: float = 0.5,
        due_at: str = None,
        source: str = "tool",
        context: str = "global",
        sensitive: bool = False,
        entity: str = None,
    ) -> str:
        """Store a knowledge entry with deduplication.

        >80% word overlap in same category+context → replaces with newer content.
        Validates due_at is a valid ISO 8601 string if provided.

        Returns the knowledge ID (existing if deduped, new UUID if created).

        Raises:
            ValueError: If content is empty or due_at is not valid ISO 8601.
        """
        # Reject empty content early — FTS5 indexes empty strings, wasting space
        # and polluting search results with no-op entries.
        if not content or not content.strip():
            raise ValueError("MemoryStore.store(): content must be non-empty")

        # Truncate very long content to prevent context overflow when injected
        # into the system prompt. 2000 chars ≈ 500 tokens — enough for any fact.
        if len(content) > 2000:
            content = content[:2000]

        # Clamp confidence to [0.0, 1.0].  Programmatic callers (e.g. scripts
        # running eval or migration) can accidentally pass values outside this
        # range; unclamped values corrupt avg_confidence stats.
        confidence = max(0.0, min(1.0, float(confidence)))

        # Normalize empty strings to None for optional text fields.
        # Empty strings break dedup and indexing:
        # - _find_similar_locked() uses `k.entity IS NULL` for entity=None and
        #   `k.entity = ?` for entity="", so "" and NULL are treated as different
        #   scopes, allowing duplicates across the two.
        # - The `WHERE entity IS NOT NULL` index also counts "" as an entity.
        domain = domain or None
        entity = entity or None

        # Validate and normalize due_at to timezone-aware ISO 8601.
        # If the caller provides a naive datetime (no offset), localize it to
        # the local timezone so that SQL string comparisons remain consistent.
        if due_at is not None:
            dt = datetime.fromisoformat(due_at)
            if dt.tzinfo is None:
                dt = dt.astimezone()  # attach local tz
            due_at = dt.isoformat()

        metadata_json = json.dumps(metadata) if metadata else None
        now = _now_iso()

        with self._lock:
            # Check for dedup match (scoped to category + context + entity)
            existing_id = self._find_similar_locked(content, category, context, entity)

            if existing_id:
                # Update existing: replace content with newer, take max confidence
                try:
                    self._conn.execute(
                        """
                        UPDATE knowledge SET
                            content = ?,
                            confidence = MAX(confidence, ?),
                            domain = COALESCE(?, domain),
                            metadata = COALESCE(?, metadata),
                            source = COALESCE(?, source),
                            entity = COALESCE(?, entity),
                            sensitive = MAX(sensitive, ?),
                            due_at = COALESCE(?, due_at),
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            content,
                            confidence,
                            domain,
                            metadata_json,
                            source,
                            entity,
                            int(sensitive),
                            due_at,
                            now,
                            existing_id,
                        ),
                    )
                    # Update FTS5 index
                    self._update_knowledge_fts_locked(existing_id)
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                logger.info(
                    "[MemoryStore] knowledge deduped id=%s category=%s",
                    existing_id,
                    category,
                )
                return existing_id

            # No dedup — create new entry
            knowledge_id = str(uuid4())
            try:
                self._conn.execute(
                    f"""
                    INSERT INTO knowledge ({self._KNOWLEDGE_COLS})
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        knowledge_id,
                        category,
                        content,
                        domain,
                        source,
                        confidence,
                        metadata_json,
                        0,  # use_count
                        context,
                        int(sensitive),
                        entity,
                        now,  # created_at
                        now,  # updated_at
                        now,  # last_used
                        due_at,
                        None,  # reminded_at
                    ),
                )
                self._insert_knowledge_fts_locked(knowledge_id)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        logger.info(
            "[MemoryStore] knowledge stored id=%s category=%s context=%s",
            knowledge_id,
            category,
            context,
        )
        return knowledge_id

    def _find_similar_locked(
        self, content: str, category: str, context: str, entity: str = None
    ) -> Optional[str]:
        """Find existing knowledge with >80% word overlap in same category+context+entity.

        Must be called with self._lock held.

        Cross-process note: self._lock is a threading.Lock — it protects against
        concurrent inserts within the *same process* only. Two separate processes
        may both see no duplicate and both insert, resulting in brief redundancy.
        This is intentionally accepted: the next store() call from either process
        will detect the overlap and deduplicate. SQLite WAL mode ensures each
        INSERT is atomic so no data is corrupted.
        """
        safe_query = _sanitize_fts5_query(content, use_and=False)
        if not safe_query:
            return None

        try:
            # Scope dedup by category + context + entity
            if entity is not None:
                cursor = self._conn.execute(
                    """
                    SELECT k.id, k.content
                    FROM knowledge k
                    JOIN knowledge_fts f ON k.rowid = f.rowid
                    WHERE knowledge_fts MATCH ? AND k.category = ? AND k.context = ?
                          AND k.entity = ?
                    ORDER BY rank
                    LIMIT 10
                    """,
                    (safe_query, category, context, entity),
                )
            else:
                cursor = self._conn.execute(
                    """
                    SELECT k.id, k.content
                    FROM knowledge k
                    JOIN knowledge_fts f ON k.rowid = f.rowid
                    WHERE knowledge_fts MATCH ? AND k.category = ? AND k.context = ?
                          AND k.entity IS NULL
                    ORDER BY rank
                    LIMIT 10
                    """,
                    (safe_query, category, context),
                )
            for row in cursor.fetchall():
                existing_id, existing_content = row[0], row[1]
                overlap = _word_overlap(content, existing_content)
                if overlap >= 0.8:
                    logger.debug(
                        "[MemoryStore] dedup match: overlap=%.2f id=%s",
                        overlap,
                        existing_id,
                    )
                    return existing_id
        except sqlite3.OperationalError as e:
            logger.debug("[MemoryStore] FTS5 dedup search error: %s", e)

        return None

    def _insert_knowledge_fts_locked(self, knowledge_id: str):
        """Insert a knowledge entry into the FTS5 index. Must hold self._lock.

        knowledge_fts is a STANDALONE FTS5 table (no content= clause).
        We manually sync rowids: knowledge_fts.rowid MUST equal knowledge.rowid
        so that JOIN knowledge_fts f ON k.rowid = f.rowid works correctly.
        NEVER let SQLite auto-assign the rowid here — always supply it explicitly.
        """
        cursor = self._conn.execute(
            "SELECT rowid, content, domain, category FROM knowledge WHERE id = ?",
            (knowledge_id,),
        )
        row = cursor.fetchone()
        if row:
            self._conn.execute(
                "INSERT INTO knowledge_fts (rowid, content, domain, category) "
                "VALUES (?, ?, ?, ?)",
                (row[0], row[1], row[2] or "", row[3]),
            )

    def _update_knowledge_fts_locked(self, knowledge_id: str):
        """Re-sync a knowledge entry in the FTS5 index. Must hold self._lock."""
        # Delete old FTS entry
        self._conn.execute(
            "DELETE FROM knowledge_fts WHERE rowid = "
            "(SELECT rowid FROM knowledge WHERE id = ?)",
            (knowledge_id,),
        )
        # Re-insert
        self._insert_knowledge_fts_locked(knowledge_id)

    # ==================================================================
    # Knowledge — Search (FTS5)
    # ==================================================================

    def search(
        self,
        query: str,
        category: str = None,
        context: str = None,
        entity: str = None,
        include_sensitive: bool = False,
        top_k: int = 5,
    ) -> List[Dict]:
        """FTS5 search. AND semantics, OR fallback. BM25 ranking.

        Bumps confidence +0.02 on each recalled item.
        Filters by context/entity if provided. Excludes sensitive by default.
        """
        safe_query = _sanitize_fts5_query(query, use_and=True)
        if not safe_query:
            return []

        with self._lock:
            results = self._fts5_search_knowledge_locked(
                safe_query, category, context, entity, include_sensitive, top_k
            )
            if not results:
                safe_query_or = _sanitize_fts5_query(query, use_and=False)
                if safe_query_or and safe_query_or != safe_query:
                    results = self._fts5_search_knowledge_locked(
                        safe_query_or,
                        category,
                        context,
                        entity,
                        include_sensitive,
                        top_k,
                    )

            # Bump confidence +0.02 and update last_used for each result.
            # Use SQL-side arithmetic (MIN(confidence + 0.02, 1.0)) so the
            # UPDATE is atomic: even in multi-process deployments where two
            # processes both query the same item, each bump is applied to the
            # CURRENT DB value rather than a stale Python-side snapshot read
            # before the UPDATE.  The Python dict is updated to the expected
            # post-bump value for the return object.
            # Wrap in try/except so that a failure on any UPDATE rolls back
            # the whole batch — partial confidence bumps committed by a later
            # unrelated commit() would produce inconsistent use_count/confidence.
            if results:
                now = _now_iso()
                try:
                    for r in results:
                        self._conn.execute(
                            "UPDATE knowledge SET "
                            "confidence = MIN(confidence + 0.02, 1.0), "
                            "last_used = ?, "
                            "use_count = use_count + 1 "
                            "WHERE id = ?",
                            (now, r["id"]),
                        )
                        r["confidence"] = min(r["confidence"] + 0.02, 1.0)
                        r["last_used"] = now
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise

        return results

    def _fts5_search_knowledge_locked(
        self,
        fts_query: str,
        category: Optional[str],
        context: Optional[str],
        entity: Optional[str],
        include_sensitive: bool,
        top_k: int,
    ) -> List[Dict]:
        """Execute FTS5 search on knowledge. Must hold self._lock."""
        conditions = ["knowledge_fts MATCH ?"]
        params: list = [fts_query]

        if category is not None:
            conditions.append("k.category = ?")
            params.append(category)
        if context is not None:
            conditions.append("k.context = ?")
            params.append(context)
        if entity is not None:
            conditions.append("k.entity = ?")
            params.append(entity)
        if not include_sensitive:
            conditions.append("k.sensitive = 0")

        where = " AND ".join(conditions)
        params.append(top_k)

        select_cols = ", ".join(
            f"k.{c.strip()}" for c in self._KNOWLEDGE_COLS.split(",")
        )
        sql = f"""
            SELECT {select_cols}
            FROM knowledge k
            JOIN knowledge_fts f ON k.rowid = f.rowid
            WHERE {where}
            ORDER BY bm25(knowledge_fts, 10.0, 1.0, 1.0), k.confidence DESC
            LIMIT ?
        """

        try:
            cursor = self._conn.execute(sql, tuple(params))
            return [self._row_to_knowledge_dict(r) for r in cursor.fetchall()]
        except sqlite3.OperationalError as e:
            logger.debug("[MemoryStore] FTS5 knowledge search error: %s", e)
            return []

    # ==================================================================
    # Knowledge — Category / Entity / Upcoming lookups
    # ==================================================================

    def get_by_category(
        self, category: str, context: str = None, limit: int = 10
    ) -> List[Dict]:
        """Get knowledge entries by category, optionally filtered by context."""
        conditions = ["category = ?"]
        params: list = [category]

        if context is not None:
            conditions.append("context = ?")
            params.append(context)

        where = "WHERE " + " AND ".join(conditions)
        params.append(limit)

        sql = f"""
            SELECT {self._KNOWLEDGE_COLS} FROM knowledge
            {where}
            ORDER BY confidence DESC, updated_at DESC
            LIMIT ?
        """

        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            return [self._row_to_knowledge_dict(r) for r in cursor.fetchall()]

    def get_by_entity(self, entity: str, limit: int = 20) -> List[Dict]:
        """Get all knowledge about a specific entity.

        Example: get_by_entity('person:sarah_chen') → all facts about Sarah.
        """
        sql = f"""
            SELECT {self._KNOWLEDGE_COLS} FROM knowledge
            WHERE entity = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """
        with self._lock:
            cursor = self._conn.execute(sql, (entity, limit))
            return [self._row_to_knowledge_dict(r) for r in cursor.fetchall()]

    def get_upcoming(
        self,
        within_days: int = 7,
        include_overdue: bool = True,
        context: str = None,
        limit: int = 10,
    ) -> List[Dict]:
        """Get time-sensitive items due within N days (or overdue).

        Returns items where:
        - due_at is within the window (or overdue if include_overdue=True)
        - Either never reminded, or reminded before the due date (needs follow-up)
        """
        now_iso = _now_iso()
        future_iso = (
            datetime.now().astimezone() + timedelta(days=within_days)
        ).isoformat()

        conditions = ["due_at IS NOT NULL"]
        params: list = []

        if include_overdue:
            # Due within window OR overdue
            conditions.append("due_at <= ?")
            params.append(future_iso)
        else:
            # Due within window only (not overdue)
            conditions.append("due_at > ?")
            params.append(now_iso)
            conditions.append("due_at <= ?")
            params.append(future_iso)

        # Not reminded since it became due
        conditions.append("(reminded_at IS NULL OR reminded_at < due_at)")

        if context is not None:
            conditions.append("context = ?")
            params.append(context)

        where = "WHERE " + " AND ".join(conditions)

        params.append(limit)
        sql = f"""
            SELECT {self._KNOWLEDGE_COLS} FROM knowledge
            {where}
            ORDER BY due_at ASC
            LIMIT ?
        """

        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            return [self._row_to_knowledge_dict(r) for r in cursor.fetchall()]

    # ==================================================================
    # Knowledge — Update / Delete
    # ==================================================================

    def update(
        self,
        knowledge_id: str,
        content: str = None,
        category: str = None,
        domain: str = None,
        metadata: dict = None,
        context: str = None,
        sensitive: bool = None,
        entity: str = None,
        due_at: str = None,
        reminded_at: str = None,
    ) -> bool:
        """Update an existing knowledge entry. Only provided fields are changed.

        Sets updated_at to now. Returns False if ID not found.
        """
        # Normalize empty strings to None — same semantics as store().
        # An empty-string entity or domain would differ from NULL in SQL and
        # break entity-scoped dedup, index filtering, and stats queries.
        if entity == "":
            entity = None
        if domain == "":
            domain = None

        # Validate and normalize due_at — same rule as store().
        # Naive datetimes are attached to local timezone so SQL string
        # comparisons against tz-aware timestamps remain consistent.
        if due_at is not None:
            _dt = datetime.fromisoformat(due_at)
            if _dt.tzinfo is None:
                _dt = _dt.astimezone()
            due_at = _dt.isoformat()

        # Validate and normalize reminded_at.
        # Non-ISO strings silently break get_upcoming() SQL string comparisons
        # (reminded_at < due_at), so we validate here as defense-in-depth.
        if reminded_at is not None:
            _rdt = datetime.fromisoformat(reminded_at)
            if _rdt.tzinfo is None:
                _rdt = _rdt.astimezone()
            reminded_at = _rdt.isoformat()

        sets = ["updated_at = ?"]
        params: list = [_now_iso()]

        if content is not None:
            if not content.strip():
                raise ValueError("update(): content must be non-empty")
            if len(content) > 2000:
                content = content[:2000]
            sets.append("content = ?")
            params.append(content)
        if category is not None:
            sets.append("category = ?")
            params.append(category)
        if domain is not None:
            sets.append("domain = ?")
            params.append(domain)
        if metadata is not None:
            sets.append("metadata = ?")
            params.append(json.dumps(metadata))
        if context is not None:
            sets.append("context = ?")
            params.append(context)
        if sensitive is not None:
            sets.append("sensitive = ?")
            params.append(int(sensitive))
        if entity is not None:
            sets.append("entity = ?")
            params.append(entity)
        if due_at is not None:
            sets.append("due_at = ?")
            params.append(due_at)
        if reminded_at is not None:
            sets.append("reminded_at = ?")
            params.append(reminded_at)

        params.append(knowledge_id)
        sql = f"UPDATE knowledge SET {', '.join(sets)} WHERE id = ?"

        with self._lock:
            try:
                rowcount = self._conn.execute(sql, tuple(params)).rowcount
                if rowcount > 0:
                    # Re-sync FTS if content/category/domain changed
                    if (
                        content is not None
                        or category is not None
                        or domain is not None
                    ):
                        self._update_knowledge_fts_locked(knowledge_id)
                    self._conn.commit()
                return rowcount > 0
            except Exception:
                self._conn.rollback()
                raise

    def update_confidence(self, knowledge_id: str, delta: float) -> None:
        """Adjust confidence by delta, clamped to [0.0, 1.0]."""
        with self._lock:
            try:
                self._conn.execute(
                    """
                    UPDATE knowledge SET
                        confidence = MIN(MAX(confidence + ?, 0.0), 1.0),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (delta, _now_iso(), knowledge_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def delete(self, knowledge_id: str) -> bool:
        """Delete a knowledge entry by ID. Returns False if not found."""
        with self._lock:
            try:
                # Delete from FTS first
                self._conn.execute(
                    "DELETE FROM knowledge_fts WHERE rowid = "
                    "(SELECT rowid FROM knowledge WHERE id = ?)",
                    (knowledge_id,),
                )
                rowcount = self._conn.execute(
                    "DELETE FROM knowledge WHERE id = ?", (knowledge_id,)
                ).rowcount
                self._conn.commit()
                return rowcount > 0
            except Exception:
                self._conn.rollback()
                raise

    # ==================================================================
    # Tool History
    # ==================================================================

    def log_tool_call(
        self,
        session_id: str,
        tool_name: str,
        args: dict,
        result_summary: str,
        success: bool,
        error: str = None,
        duration_ms: int = None,
    ) -> None:
        """Log a tool call to tool_history."""
        now = _now_iso()
        args_json = json.dumps(args, default=str) if args else None
        # Truncate all text columns to 500 chars.  Tool args, results, and
        # error messages can all be arbitrarily large (e.g. write_file called
        # with 100 KB content).  Storing the full payload bloats the database
        # without adding search or observability value.
        if args_json and len(args_json) > 500:
            args_json = args_json[:500]
        if result_summary and len(result_summary) > 500:
            result_summary = result_summary[:500]
        if error and len(error) > 500:
            error = error[:500]

        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO tool_history
                        (session_id, tool_name, args, result_summary, success, error, duration_ms, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        tool_name,
                        args_json,
                        result_summary,
                        int(success),
                        error,
                        duration_ms,
                        now,
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_tool_errors(self, tool_name: str = None, limit: int = 10) -> List[Dict]:
        """Get recent failed tool calls, newest first."""
        if tool_name is not None:
            sql = """
                SELECT id, session_id, tool_name, args, result_summary,
                       success, error, duration_ms, timestamp
                FROM tool_history
                WHERE success = 0 AND tool_name = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (tool_name, limit)
        else:
            sql = """
                SELECT id, session_id, tool_name, args, result_summary,
                       success, error, duration_ms, timestamp
                FROM tool_history
                WHERE success = 0
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (limit,)

        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [self._row_to_tool_dict(r) for r in cursor.fetchall()]

    def get_tool_stats(self, tool_name: str) -> Dict:
        """Returns: {total_calls, success_rate, avg_duration_ms, last_error}"""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                       AVG(duration_ms) as avg_ms
                FROM tool_history
                WHERE tool_name = ?
                """,
                (tool_name,),
            )
            row = cursor.fetchone()
            total = row[0] or 0
            successes = row[1] or 0
            avg_ms = row[2]

            # Get last error
            cursor = self._conn.execute(
                """
                SELECT error FROM tool_history
                WHERE tool_name = ? AND success = 0 AND error IS NOT NULL
                ORDER BY timestamp DESC LIMIT 1
                """,
                (tool_name,),
            )
            err_row = cursor.fetchone()
            last_error = err_row[0] if err_row else None

        return {
            "total_calls": total,
            "success_rate": (successes / total) if total > 0 else 0.0,
            "avg_duration_ms": round(avg_ms) if avg_ms is not None else None,
            "last_error": last_error,
        }

    def _row_to_tool_dict(self, row) -> Dict:
        """Convert a tool_history row to dict."""
        return {
            "id": row[0],
            "session_id": row[1],
            "tool_name": row[2],
            "args": _safe_json_loads(row[3]),
            "result_summary": row[4],
            "success": row[5],
            "error": row[6],
            "duration_ms": row[7],
            "timestamp": row[8],
        }

    # ==================================================================
    # Dashboard / Observability
    # ==================================================================

    def get_stats(self) -> Dict:
        """Aggregate statistics across all tables."""
        with self._lock:
            # Knowledge stats
            k_total = self._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]

            k_by_cat = {}
            for row in self._conn.execute(
                "SELECT category, COUNT(*) FROM knowledge GROUP BY category"
            ).fetchall():
                k_by_cat[row[0]] = row[1]

            k_by_ctx = {}
            for row in self._conn.execute(
                "SELECT context, COUNT(*) FROM knowledge GROUP BY context"
            ).fetchall():
                k_by_ctx[row[0]] = row[1]

            k_sensitive = self._conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE sensitive = 1"
            ).fetchone()[0]

            k_entities = self._conn.execute(
                "SELECT COUNT(DISTINCT entity) FROM knowledge WHERE entity IS NOT NULL"
            ).fetchone()[0]

            k_avg_conf_row = self._conn.execute(
                "SELECT AVG(confidence) FROM knowledge"
            ).fetchone()
            k_avg_conf = k_avg_conf_row[0] if k_avg_conf_row[0] is not None else 0.0

            k_oldest_row = self._conn.execute(
                "SELECT MIN(created_at) FROM knowledge"
            ).fetchone()
            k_oldest = k_oldest_row[0] if k_oldest_row else None

            k_newest_row = self._conn.execute(
                "SELECT MAX(created_at) FROM knowledge"
            ).fetchone()
            k_newest = k_newest_row[0] if k_newest_row else None

            # Conversation stats
            c_total = self._conn.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0]

            c_sessions = self._conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM conversations"
            ).fetchone()[0]

            c_first_row = self._conn.execute(
                "SELECT MIN(timestamp) FROM conversations"
            ).fetchone()
            c_first = c_first_row[0] if c_first_row else None

            c_last_row = self._conn.execute(
                "SELECT MAX(timestamp) FROM conversations"
            ).fetchone()
            c_last = c_last_row[0] if c_last_row else None

            # Tool stats
            t_total = self._conn.execute(
                "SELECT COUNT(*) FROM tool_history"
            ).fetchone()[0]

            t_unique = self._conn.execute(
                "SELECT COUNT(DISTINCT tool_name) FROM tool_history"
            ).fetchone()[0]

            t_successes = self._conn.execute(
                "SELECT COUNT(*) FROM tool_history WHERE success = 1"
            ).fetchone()[0]

            t_errors = self._conn.execute(
                "SELECT COUNT(*) FROM tool_history WHERE success = 0"
            ).fetchone()[0]

            # Temporal stats
            now_iso = _now_iso()
            future_7d = (datetime.now().astimezone() + timedelta(days=7)).isoformat()

            upcoming_count = self._conn.execute(
                """
                SELECT COUNT(*) FROM knowledge
                WHERE due_at IS NOT NULL AND due_at <= ? AND due_at > ?
                AND (reminded_at IS NULL OR reminded_at < due_at)
                """,
                (future_7d, now_iso),
            ).fetchone()[0]

            overdue_count = self._conn.execute(
                """
                SELECT COUNT(*) FROM knowledge
                WHERE due_at IS NOT NULL AND due_at <= ?
                AND (reminded_at IS NULL OR reminded_at < due_at)
                """,
                (now_iso,),
            ).fetchone()[0]

        # DB size
        try:
            db_size = os.path.getsize(str(self._db_path))
        except OSError:
            db_size = 0

        return {
            "knowledge": {
                "total": k_total,
                "by_category": k_by_cat,
                "by_context": k_by_ctx,
                "sensitive_count": k_sensitive,
                "entity_count": k_entities,
                "avg_confidence": round(k_avg_conf, 4),
                "oldest": k_oldest,
                "newest": k_newest,
            },
            "conversations": {
                "total_turns": c_total,
                "total_sessions": c_sessions,
                "first_session": c_first,
                "last_session": c_last,
            },
            "tools": {
                "total_calls": t_total,
                "unique_tools": t_unique,
                "overall_success_rate": (
                    round(t_successes / t_total, 4) if t_total > 0 else 0.0
                ),
                "total_errors": t_errors,
            },
            "temporal": {
                "upcoming_count": upcoming_count,
                "overdue_count": overdue_count,
            },
            "db_size_bytes": db_size,
        }

    def get_all_knowledge(
        self,
        category: str = None,
        context: str = None,
        entity: str = None,
        sensitive: bool = None,
        search: str = None,
        sort_by: str = "updated_at",
        order: str = "desc",
        offset: int = 0,
        limit: int = 50,
    ) -> Dict:
        """Paginated knowledge browser with full filtering.

        Returns: {"items": [...], "total": N, "offset": N, "limit": N}
        """
        # Whitelist sort columns
        allowed_sort = {
            "updated_at",
            "created_at",
            "confidence",
            "category",
            "context",
            "content",
            "use_count",
        }
        if sort_by not in allowed_sort:
            sort_by = "updated_at"
        order_dir = "DESC" if order.lower() == "desc" else "ASC"

        conditions: list = []
        params: list = []

        if category is not None:
            conditions.append("k.category = ?")
            params.append(category)
        if context is not None:
            conditions.append("k.context = ?")
            params.append(context)
        if entity is not None:
            conditions.append("k.entity = ?")
            params.append(entity)
        if sensitive is not None:
            conditions.append("k.sensitive = ?")
            params.append(int(sensitive))

        # FTS5 search filter
        fts_join = ""
        if search:
            safe_q = _sanitize_fts5_query(search, use_and=True)
            if safe_q:
                fts_join = "JOIN knowledge_fts f ON k.rowid = f.rowid"
                conditions.append("knowledge_fts MATCH ?")
                params.append(safe_q)
            else:
                # Search was provided but consists entirely of special chars
                # that sanitize to nothing (e.g. "@@@", "---").  Returning all
                # items would be surprising — return empty instead.
                return {"items": [], "total": 0, "offset": offset, "limit": limit}

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        with self._lock:
            # Total count
            count_sql = f"SELECT COUNT(*) FROM knowledge k {fts_join} {where}"
            total = self._conn.execute(count_sql, tuple(params)).fetchone()[0]

            # Paginated results
            select_cols = ", ".join(
                f"k.{c.strip()}" for c in self._KNOWLEDGE_COLS.split(",")
            )
            data_sql = f"""
                SELECT {select_cols} FROM knowledge k {fts_join}
                {where}
                ORDER BY k.{sort_by} {order_dir}
                LIMIT ? OFFSET ?
            """
            data_params = tuple(params) + (limit, offset)
            cursor = self._conn.execute(data_sql, data_params)
            items = [self._row_to_knowledge_dict(r) for r in cursor.fetchall()]

        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def get_tool_summary(self, limit: int = 200) -> List[Dict]:
        """Per-tool stats for the tool activity table.

        Uses a single LEFT JOIN to retrieve the last error per tool —
        no N+1 queries. Capped at `limit` rows (default 200) to prevent
        unbounded payloads when many distinct tool names are recorded.
        """
        sql = """
            SELECT t.tool_name,
                   t.total,
                   t.successes,
                   t.failures,
                   t.avg_ms,
                   t.last_used,
                   e.error AS last_error
            FROM (
                SELECT tool_name,
                       COUNT(*) AS total,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures,
                       AVG(duration_ms) AS avg_ms,
                       MAX(timestamp) AS last_used
                FROM tool_history
                GROUP BY tool_name
                ORDER BY total DESC
                LIMIT ?
            ) t
            LEFT JOIN (
                SELECT tool_name, error,
                       ROW_NUMBER() OVER (
                           PARTITION BY tool_name ORDER BY timestamp DESC
                       ) AS rn
                FROM tool_history
                WHERE success = 0 AND error IS NOT NULL
            ) e ON t.tool_name = e.tool_name AND e.rn = 1
        """
        with self._lock:
            cursor = self._conn.execute(sql, (limit,))
            rows = cursor.fetchall()

        return [
            {
                "tool_name": r[0],
                "total_calls": r[1],
                "success_count": r[2],
                "failure_count": r[3],
                "success_rate": round(r[2] / r[1], 4) if r[1] > 0 else 0.0,
                "avg_duration_ms": round(r[4]) if r[4] is not None else None,
                "last_used": r[5],
                "last_error": r[6],
            }
            for r in rows
        ]

    def get_activity_timeline(self, days: int = 30) -> List[Dict]:
        """Daily activity counts for the activity chart."""
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat()

        with self._lock:
            # Conversation turns per day
            conv_rows = self._conn.execute(
                """
                SELECT SUBSTR(timestamp, 1, 10) as day, COUNT(*)
                FROM conversations
                WHERE timestamp >= ?
                GROUP BY day
                """,
                (cutoff,),
            ).fetchall()
            conv_map = {r[0]: r[1] for r in conv_rows}

            # Tool calls per day
            tool_rows = self._conn.execute(
                """
                SELECT SUBSTR(timestamp, 1, 10) as day, COUNT(*)
                FROM tool_history
                WHERE timestamp >= ?
                GROUP BY day
                """,
                (cutoff,),
            ).fetchall()
            tool_map = {r[0]: r[1] for r in tool_rows}

            # Knowledge added per day
            knowledge_rows = self._conn.execute(
                """
                SELECT SUBSTR(created_at, 1, 10) as day, COUNT(*)
                FROM knowledge
                WHERE created_at >= ?
                GROUP BY day
                """,
                (cutoff,),
            ).fetchall()
            knowledge_map = {r[0]: r[1] for r in knowledge_rows}

            # Errors per day
            error_rows = self._conn.execute(
                """
                SELECT SUBSTR(timestamp, 1, 10) as day, COUNT(*)
                FROM tool_history
                WHERE timestamp >= ? AND success = 0
                GROUP BY day
                """,
                (cutoff,),
            ).fetchall()
            error_map = {r[0]: r[1] for r in error_rows}

        # Build timeline for each day in range
        all_days = set()
        all_days.update(conv_map.keys())
        all_days.update(tool_map.keys())
        all_days.update(knowledge_map.keys())
        all_days.update(error_map.keys())

        # Also include days with no activity in the range
        now = datetime.now().astimezone()
        for i in range(days + 1):
            day_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            all_days.add(day_str)

        timeline = []
        for day in sorted(all_days):
            if day < cutoff[:10]:
                continue
            timeline.append(
                {
                    "date": day,
                    "conversations": conv_map.get(day, 0),
                    "tool_calls": tool_map.get(day, 0),
                    "knowledge_added": knowledge_map.get(day, 0),
                    "errors": error_map.get(day, 0),
                }
            )

        return timeline

    def get_recent_errors(self, limit: int = 20) -> List[Dict]:
        """Recent tool errors for the error log view.

        Returns tool_history rows where success=0, newest first.
        """
        sql = """
            SELECT id, session_id, tool_name, args, result_summary,
                   success, error, duration_ms, timestamp
            FROM tool_history
            WHERE success = 0
            ORDER BY timestamp DESC
            LIMIT ?
        """
        with self._lock:
            cursor = self._conn.execute(sql, (limit,))
            return [self._row_to_tool_dict(r) for r in cursor.fetchall()]

    # ==================================================================
    # Housekeeping
    # ==================================================================

    def get_source_counts(self) -> Dict[str, int]:
        """Return knowledge entry counts grouped by source (tool, user, discovery, …)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, COUNT(*) FROM knowledge GROUP BY source"
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def delete_by_source(self, source: str) -> int:
        """Delete all knowledge entries with the given source. Returns deleted count.

        Atomically cleans up the FTS5 index and knowledge table in a single
        transaction — avoids the knowledge/FTS divergence that manual per-ID
        deletion without a wrapping transaction would risk.
        """
        with self._lock:
            try:
                # FTS cleanup: delete all FTS entries for matching knowledge rows
                self._conn.execute(
                    """
                    DELETE FROM knowledge_fts
                    WHERE rowid IN (SELECT rowid FROM knowledge WHERE source = ?)
                    """,
                    (source,),
                )
                deleted = self._conn.execute(
                    "DELETE FROM knowledge WHERE source = ?", (source,)
                ).rowcount
                self._conn.commit()
                return deleted
            except Exception:
                self._conn.rollback()
                raise

    def get_entities(self, limit: int = 100) -> List[Dict]:
        """List unique entities with knowledge counts and last_updated.

        Capped at `limit` rows (default 100) to prevent unbounded payloads.
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT entity, COUNT(*) as count, MAX(updated_at) as last_updated
                FROM knowledge
                WHERE entity IS NOT NULL
                GROUP BY entity
                ORDER BY count DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                {"entity": row[0], "count": row[1], "last_updated": row[2]}
                for row in cursor.fetchall()
            ]

    def get_contexts(self, limit: int = 100) -> List[Dict]:
        """List contexts with their knowledge counts.

        Capped at `limit` rows (default 100) to prevent unbounded payloads.
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT context, COUNT(*) as count
                FROM knowledge
                GROUP BY context
                ORDER BY count DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [{"context": row[0], "count": row[1]} for row in cursor.fetchall()]

    def get_tool_history(self, tool_name: str, limit: int = 50) -> List[Dict]:
        """Recent call history for a specific tool."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT tool_name, args, result_summary, success, error, duration_ms, timestamp
                FROM tool_history
                WHERE tool_name = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (tool_name, limit),
            )
            return [
                {
                    "tool_name": row[0],
                    "args": _safe_json_loads(row[1]),
                    "result_summary": row[2],
                    "success": bool(row[3]),
                    "error": row[4],
                    "duration_ms": row[5],
                    "timestamp": row[6],
                }
                for row in cursor.fetchall()
            ]

    def get_sessions(self, limit: int = 20) -> List[Dict]:
        """List conversation sessions with turn counts and first message preview."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT session_id,
                       COUNT(*) as turn_count,
                       MIN(timestamp) as started_at,
                       MAX(timestamp) as last_activity,
                       (SELECT content FROM conversations c2
                        WHERE c2.session_id = conversations.session_id
                          AND c2.role = 'user'
                        ORDER BY c2.id ASC
                        LIMIT 1) as first_message
                FROM conversations
                GROUP BY session_id
                ORDER BY last_activity DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                {
                    "session_id": row[0],
                    "turn_count": row[1],
                    "started_at": row[2],
                    "last_activity": row[3],
                    "first_message": (row[4] or "")[:100],
                }
                for row in cursor.fetchall()
            ]

    def apply_confidence_decay(
        self, days_threshold: int = 30, decay_factor: float = 0.9
    ) -> int:
        """Decay confidence for items not used in N days.

        Returns the number of items decayed.
        """
        cutoff = (
            datetime.now().astimezone() - timedelta(days=days_threshold)
        ).isoformat()
        now = _now_iso()

        with self._lock:
            try:
                cursor = self._conn.execute(
                    """
                    UPDATE knowledge SET
                        confidence = confidence * ?,
                        updated_at = ?
                    WHERE last_used IS NOT NULL AND last_used < ?
                          AND updated_at < ?
                    """,
                    (decay_factor, now, cutoff, cutoff),
                )
                rowcount = cursor.rowcount
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        logger.info(
            "[MemoryStore] confidence decay: %d items decayed (threshold=%d days, factor=%.2f)",
            rowcount,
            days_threshold,
            decay_factor,
        )
        return rowcount

    def prune(self, days: int = 90) -> Dict:
        """Prune old tool_history and conversation entries.

        Returns counts of deleted rows.
        """
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat()

        with self._lock:
            try:
                # Prune tool_history
                tool_deleted = self._conn.execute(
                    "DELETE FROM tool_history WHERE timestamp < ?", (cutoff,)
                ).rowcount

                # Prune conversations (delete FTS entries via trigger)
                conv_deleted = self._conn.execute(
                    "DELETE FROM conversations WHERE timestamp < ?", (cutoff,)
                ).rowcount

                # Prune low-confidence knowledge
                knowledge_deleted = self._conn.execute(
                    """
                    DELETE FROM knowledge
                    WHERE confidence < 0.1 AND last_used IS NOT NULL AND last_used < ?
                    """,
                    (cutoff,),
                ).rowcount

                # Clean up FTS for pruned knowledge
                # Re-sync by rebuilding if any knowledge was pruned
                if knowledge_deleted > 0:
                    self._rebuild_knowledge_fts_locked()

                self._conn.commit()

                # WAL checkpoint inside the lock so it doesn't race with
                # concurrent writes on self._conn. TRUNCATE mode may fail
                # with SQLITE_BUSY if a reader holds a snapshot — best-effort.
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
            except Exception:
                # Roll back the whole prune transaction so that a failure in
                # _rebuild_knowledge_fts_locked() (e.g. disk full) does not
                # leave an uncommitted DELETE that the next unrelated commit()
                # could pick up — which would wipe the FTS index.
                self._conn.rollback()
                raise

        logger.info(
            "[MemoryStore] prune: tool_history=%d conversations=%d knowledge=%d",
            tool_deleted,
            conv_deleted,
            knowledge_deleted,
        )
        return {
            "tool_history_deleted": tool_deleted,
            "conversations_deleted": conv_deleted,
            "knowledge_deleted": knowledge_deleted,
        }

    def rebuild_fts(self) -> None:
        """Rebuild all FTS5 indexes from source tables.

        Call from dashboard if search results seem wrong.

        Atomic: if the rebuild fails (e.g. disk full), rolls back so the
        pending DELETE is not committed by the next unrelated operation.
        """
        with self._lock:
            try:
                self._rebuild_knowledge_fts_locked()
                self._rebuild_conversations_fts_locked()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        logger.info("[MemoryStore] FTS5 indexes rebuilt")

    def _rebuild_knowledge_fts_locked(self):
        """Rebuild knowledge FTS5 index. Must hold self._lock."""
        self._conn.execute("DELETE FROM knowledge_fts")
        self._conn.execute("""
            INSERT INTO knowledge_fts (rowid, content, domain, category)
            SELECT rowid, content, COALESCE(domain, ''), category
            FROM knowledge
            """)

    def _rebuild_conversations_fts_locked(self):
        """Rebuild conversations FTS5 index. Must hold self._lock."""
        # For external-content FTS5, use the rebuild command
        self._conn.execute(
            "INSERT INTO conversations_fts(conversations_fts) VALUES('rebuild')"
        )

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass
