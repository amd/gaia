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
from typing import Any, Callable, Dict, List, Optional, Union, cast
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

    # Cap length to bound regex work — FTS5 queries beyond MAX_FTS_QUERY_LENGTH
    # chars are pathologically long and signal bad input rather than a real search.
    query = query[:MAX_FTS_QUERY_LENGTH]

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


# ============================================================================
# Public constants — imported by memory.py and ui/routers/memory.py so that
# category validation and content limits stay in sync across all layers without
# any of them needing to hard-code the same values independently.
# ============================================================================

#: Valid knowledge categories.  The single source of truth — import this
#: rather than redefining the set in tool closures or REST validators.
#: Goals and tasks are NOT stored here — they live in GoalStore
#: (src/gaia/agents/base/goal_store.py) which provides proper hierarchy,
#: state machines, and relational integrity.
VALID_CATEGORIES: frozenset = frozenset(
    {
        "fact",
        "preference",
        "error",
        "skill",
        "note",
        "reminder",
        "system",
        "profile",
        # Permission grants for agent-inferred goals (autonomous mode).
        # Stored as natural-language descriptions and matched via semantic
        # search so users can express broad approvals ("always accept
        # maintenance tasks") without explicit rule lists.
        "permission",
    }
)

#: Privileged categories that only an explicit memory tool / the system may
#: write — never the LLM conversation extractor. A chat turn must not be able to
#: mint a permission grant, a system fact, or a profile entry by emitting that
#: category, so the extraction/consolidation paths validate against
#: EXTRACTABLE_CATEGORIES below, not VALID_CATEGORIES.
_PRIVILEGED_CATEGORIES: frozenset = frozenset({"system", "profile", "permission"})

#: Categories the LLM conversation extractor and consolidation pass may emit.
#: Subset of VALID_CATEGORIES; mirrors the set advertised in _EXTRACTION_PROMPT.
EXTRACTABLE_CATEGORIES: frozenset = VALID_CATEGORIES - _PRIVILEGED_CATEGORIES

#: Maximum stored content length (chars).  Longer content is truncated by
#: callers before reaching store() so the database stays compact.
MAX_CONTENT_LENGTH: int = 2000

#: Maximum conversation turn length (chars) stored / injected into prompts.
MAX_TURN_LENGTH: int = 4000

#: Maximum FTS5 query length (chars).  Longer queries are pathological input.
MAX_FTS_QUERY_LENGTH: int = 500

#: Minimum word-overlap ratio (Szymkiewicz-Simpson) to treat two entries as
#: duplicates in the same category+context+entity scope.
DEDUP_OVERLAP_THRESHOLD: float = 0.8

#: Confidence bump applied to a knowledge entry each time it is recalled.
CONFIDENCE_BUMP_PER_RECALL: float = 0.02

#: Entries below this confidence are eligible for pruning.
LOW_CONFIDENCE_PRUNE_THRESHOLD: float = 0.1


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
    timestamp   TEXT NOT NULL,
    consolidated_at TEXT
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
    reminded_at TEXT,
    embedding   BLOB,
    superseded_by TEXT
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

-- Procedures (v3 — procedural memory, #887)
-- A distilled, reusable SKILL.md-shaped procedure synthesized from clusters of
-- successful tool sequences.  Its own table (not knowledge): different origin,
-- unit, quality signal, removal semantics, and embedding corpus.  A brand-new
-- table, so it is created here via CREATE TABLE IF NOT EXISTS for both fresh
-- and migrating databases; the v2->v3 step only advances the version marker.
CREATE TABLE IF NOT EXISTS procedures (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,              -- kebab-case (-> SKILL.md name)
    when_to_use     TEXT NOT NULL,              -- trigger; embedded for recall (-> description)
    markdown_body   TEXT NOT NULL,              -- full procedure incl. edge cases inline
    tools_required  TEXT,                       -- JSON array — the tool-loader recipe contract
    tool_sequence   TEXT,                       -- JSON — the distilled step pattern
    success_count   INTEGER NOT NULL DEFAULT 0,
    attempt_count   INTEGER NOT NULL DEFAULT 0, -- success_count / attempt_count = success rate
    provenance      TEXT,                       -- JSON {source:'synthesized', from_sessions:[...]}
    version         TEXT NOT NULL DEFAULT '1.0.0',
    enabled         INTEGER NOT NULL DEFAULT 1, -- disable without delete (blocks recall)
    embedding       BLOB,                       -- over when_to_use; its OWN FAISS index
    superseded_by   TEXT,                       -- set on supersede (higher success rate)
    created_at      TEXT NOT NULL,
    last_used_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_proc_name ON procedures(name);
CREATE INDEX IF NOT EXISTS idx_proc_enabled ON procedures(enabled)
    WHERE enabled = 1;
CREATE INDEX IF NOT EXISTS idx_proc_superseded ON procedures(superseded_by)
    WHERE superseded_by IS NOT NULL;
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

# v2-specific indexes that reference columns added in the v1 → v2 migration.
# Created AFTER migration in _init_schema() so the columns are guaranteed to
# exist.  IF NOT EXISTS makes them idempotent.
_V2_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_knowledge_no_embedding "
    "ON knowledge(id) WHERE embedding IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_superseded "
    "ON knowledge(superseded_by) WHERE superseded_by IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_conv_not_consolidated "
    "ON conversations(session_id) WHERE consolidated_at IS NULL",
]


# ============================================================================
# MemoryStore
# ============================================================================


class MemoryStore:
    """Pure SQLite storage for agent memory. No agent dependencies."""

    def __init__(self, db_path: Path | None = None):
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
        """Create tables, indexes, triggers, and set WAL mode.

        Fresh installs get the full v3 schema.  Existing databases at v1 or v2
        are migrated automatically (v1→v2 via ALTER TABLE ADD COLUMN; v2→v3 via
        the procedures table's CREATE TABLE IF NOT EXISTS in ``_SCHEMA_SQL``).
        """
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            # Allow up to 5 s of retries before raising SQLITE_BUSY.  This
            # prevents spurious errors when the dashboard REST singleton and
            # the ChatAgent instance share the same WAL file concurrently.
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA_SQL)

            # Create triggers (ignore if already exist)
            for trigger_sql in _TRIGGER_SQL:
                try:
                    self._conn.execute(trigger_sql)
                except sqlite3.OperationalError:
                    pass  # Trigger already exists

            # Initialize schema_version if empty (fresh install → v3)
            cursor = self._conn.execute("SELECT COUNT(*) FROM schema_version")
            if cursor.fetchone()[0] == 0:
                self._conn.execute(
                    "INSERT INTO schema_version VALUES (?, ?)",
                    (3, _now_iso()),
                )
            else:
                # Run migrations for existing databases
                self._migrate_schema_locked()

            # Create v2-specific indexes AFTER migration ensures the columns
            # exist.  On fresh installs the columns are in the CREATE TABLE,
            # so these are also safe.  IF NOT EXISTS makes them idempotent.
            for idx_sql in _V2_INDEX_SQL:
                self._conn.execute(idx_sql)

            self._conn.commit()

    def _migrate_schema_locked(self):
        """Run schema migrations if needed. Must hold self._lock.

        Migrations are additive — ALTER TABLE ADD COLUMN (v1->v2) and a new
        CREATE TABLE (v2->v3), both of which SQLite applies without rewriting
        existing rows.  Each step is guarded so a partial prior migration
        re-runs cleanly, and the steps chain (a v1 database is taken to v3).
        """
        cursor = self._conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        )
        row = cursor.fetchone()
        current_version = row[0] if row else 1

        if current_version < 2:
            logger.info("[MemoryStore] migrating schema v%d -> v2", current_version)

            # v1 -> v2: add embedding, superseded_by to knowledge;
            #           add consolidated_at to conversations.
            #           Indexes are created by _init_schema() after this returns.
            _v2_alter_statements = [
                "ALTER TABLE knowledge ADD COLUMN embedding BLOB",
                "ALTER TABLE knowledge ADD COLUMN superseded_by TEXT",
                "ALTER TABLE conversations ADD COLUMN consolidated_at TEXT",
            ]
            for stmt in _v2_alter_statements:
                try:
                    self._conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    # "duplicate column name" — column already exists from
                    # a partial prior migration.  Safe to ignore.
                    if "duplicate column" in str(e).lower():
                        logger.debug("[MemoryStore] migration column exists: %s", e)
                    else:
                        raise

            self._conn.execute(
                "UPDATE schema_version SET version = 2, migrated_at = ?",
                (_now_iso(),),
            )
            logger.info("[MemoryStore] schema migration to v2 complete")
            current_version = 2

        if current_version < 3:
            logger.info("[MemoryStore] migrating schema v%d -> v3", current_version)

            # v2 -> v3: add the procedures table (procedural memory, #887).
            # The table and its indexes are created by _SCHEMA_SQL's
            # CREATE TABLE IF NOT EXISTS, which already ran in _init_schema(),
            # so this step only advances the version marker.  Additive only —
            # no knowledge / tool_history / conversations row is touched.
            self._conn.execute(
                "UPDATE schema_version SET version = 3, migrated_at = ?",
                (_now_iso(),),
            )
            logger.info("[MemoryStore] schema migration to v3 complete")
            current_version = 3

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
        """Convert a knowledge row tuple to a dict.

        Maps the standard 17-column SELECT (``_KNOWLEDGE_COLS``) to a dict.
        Does NOT include ``embedding`` — that BLOB is large and only fetched
        by dedicated methods (``get_items_with_embeddings``, etc.).
        """
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
            "superseded_by": row[16],
        }

    _KNOWLEDGE_COLS = (
        "id, category, content, domain, source, confidence, metadata, "
        "use_count, context, sensitive, entity, created_at, updated_at, "
        "last_used, due_at, reminded_at, superseded_by"
    )

    #: Extended column list that includes the embedding BLOB.  Only used by
    #: methods that need to return embeddings to the caller (e.g. for FAISS
    #: index building or reconciliation).
    _KNOWLEDGE_COLS_WITH_EMBEDDING = (
        "id, category, content, domain, source, confidence, metadata, "
        "use_count, context, sensitive, entity, created_at, updated_at, "
        "last_used, due_at, reminded_at, superseded_by, embedding"
    )

    #: Procedure column list (v3).  Excludes the ``embedding`` BLOB so routine
    #: queries stay light; ``_PROCEDURE_COLS_WITH_EMBEDDING`` appends it for the
    #: FAISS-index builder.  Order matches ``_row_to_procedure_dict``.
    _PROCEDURE_COLS = (
        "id, name, when_to_use, markdown_body, tools_required, tool_sequence, "
        "success_count, attempt_count, provenance, version, enabled, "
        "superseded_by, created_at, last_used_at"
    )

    _PROCEDURE_COLS_WITH_EMBEDDING = (
        "id, name, when_to_use, markdown_body, tools_required, tool_sequence, "
        "success_count, attempt_count, provenance, version, enabled, "
        "superseded_by, created_at, last_used_at, embedding"
    )

    def _row_to_knowledge_dict_with_embedding(self, row) -> Dict:
        """Convert an 18-column row (including embedding) to a dict."""
        d = self._row_to_knowledge_dict(row)
        d["embedding"] = row[17]  # bytes or None
        return d

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
        if len(content) > MAX_TURN_LENGTH:
            content = content[:MAX_TURN_LENGTH]
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
        session_id: str | None = None,
        context: str | None = None,
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
        context: str | None = None,
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
        context: str | None = None,
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
        domain: str | None = None,
        metadata: dict | None = None,
        confidence: float = 0.5,
        due_at: str | None = None,
        source: str = "tool",
        context: str = "global",
        sensitive: bool = False,
        entity: str | None = None,
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
        # into the system prompt. MAX_CONTENT_LENGTH chars ≈ 500 tokens — enough for any fact.
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH]

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
                # Update existing: replace content with newer, take max confidence.
                # Set embedding = NULL because the old embedding is now stale
                # (it was computed for the previous content).  This forces the
                # item into get_items_without_embeddings() for re-embedding.
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
                            updated_at = ?,
                            embedding = NULL
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
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        None,  # superseded_by
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
        self, content: str, category: str, context: str, entity: str | None = None
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
                          AND k.entity = ? AND k.superseded_by IS NULL
                    ORDER BY rank
                    LIMIT 25
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
                          AND k.entity IS NULL AND k.superseded_by IS NULL
                    ORDER BY rank
                    LIMIT 25
                    """,
                    (safe_query, category, context),
                )
            for row in cursor.fetchall():
                existing_id, existing_content = row[0], row[1]
                overlap = _word_overlap(content, existing_content)
                if overlap >= DEDUP_OVERLAP_THRESHOLD:
                    logger.debug(
                        "[MemoryStore] dedup match: overlap=%.2f id=%s",
                        overlap,
                        existing_id,
                    )
                    return cast(str, existing_id)
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
        category: str | None = None,
        context: str | None = None,
        entity: str | None = None,
        include_sensitive: bool = False,
        top_k: int = 5,
        time_from: str | None = None,
        time_to: str | None = None,
    ) -> List[Dict]:
        """FTS5 search. AND semantics, OR fallback. BM25 ranking.

        Bumps confidence +0.02 and increments use_count on each recalled item.
        Filters by context/entity if provided. Excludes sensitive by default.
        Only returns active items (superseded_by IS NULL).

        Args:
            time_from: ISO 8601 lower bound on created_at (inclusive).
            time_to: ISO 8601 upper bound on created_at (inclusive).
        """
        safe_query = _sanitize_fts5_query(query, use_and=True)
        if not safe_query:
            return []

        with self._lock:
            results = self._fts5_search_knowledge_locked(
                safe_query,
                category,
                context,
                entity,
                include_sensitive,
                top_k,
                time_from,
                time_to,
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
                        time_from,
                        time_to,
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
                            "confidence = MIN(confidence + ?, 1.0), "
                            "last_used = ?, "
                            "use_count = use_count + 1 "
                            "WHERE id = ?",
                            (CONFIDENCE_BUMP_PER_RECALL, now, r["id"]),
                        )
                        r["confidence"] = min(
                            r["confidence"] + CONFIDENCE_BUMP_PER_RECALL, 1.0
                        )
                        r["use_count"] = r["use_count"] + 1
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
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
    ) -> List[Dict]:
        """Execute FTS5 search on knowledge. Must hold self._lock.

        Always filters ``superseded_by IS NULL`` (only active items).
        Optional temporal filtering on ``created_at``.
        """
        conditions = ["knowledge_fts MATCH ?", "k.superseded_by IS NULL"]
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
        if time_from is not None:
            conditions.append("k.created_at >= ?")
            params.append(time_from)
        if time_to is not None:
            conditions.append("k.created_at <= ?")
            params.append(time_to)

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
        self,
        category: str,
        context: str | None = None,
        domain: str | None = None,
        limit: int = 10,
    ) -> List[Dict]:
        """Get active knowledge entries by category, optionally filtered by context and domain."""
        conditions = ["category = ?", "superseded_by IS NULL"]
        params: list = [category]

        if context is not None:
            conditions.append("context = ?")
            params.append(context)

        if domain is not None:
            conditions.append("domain = ?")
            params.append(domain)

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

    def get_by_category_contexts(
        self, category: str, context: str, limit: int = 10
    ) -> List[Dict]:
        """Get non-sensitive knowledge by category for a specific context AND global.

        Single query that replaces two sequential get_by_category() calls in
        _get_context_items() — avoids the 2-round-trips-per-category overhead
        during system prompt construction.
        """
        if context == "global":
            sql = f"""
                SELECT {self._KNOWLEDGE_COLS} FROM knowledge
                WHERE category = ? AND context = ? AND sensitive = 0
                      AND superseded_by IS NULL
                ORDER BY confidence DESC, updated_at DESC
                LIMIT ?
            """
            params = (category, "global", limit)
        else:
            sql = f"""
                SELECT {self._KNOWLEDGE_COLS} FROM knowledge
                WHERE category = ? AND context IN (?, 'global') AND sensitive = 0
                      AND superseded_by IS NULL
                ORDER BY confidence DESC, updated_at DESC
                LIMIT ?
            """
            params = (category, context, limit)

        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [self._row_to_knowledge_dict(r) for r in cursor.fetchall()]

    def get_by_entity(self, entity: str, limit: int = 20) -> List[Dict]:
        """Get all active knowledge about a specific entity.

        Example: get_by_entity('person:sarah_chen') → all facts about Sarah.
        """
        sql = f"""
            SELECT {self._KNOWLEDGE_COLS} FROM knowledge
            WHERE entity = ? AND superseded_by IS NULL
            ORDER BY updated_at DESC
            LIMIT ?
        """
        with self._lock:
            cursor = self._conn.execute(sql, (entity, limit))
            return [self._row_to_knowledge_dict(r) for r in cursor.fetchall()]

    def get_item(self, knowledge_id: str) -> Optional[Dict]:
        """Fetch a single knowledge item by ID, or None if not found.

        Unlike search-style methods, this returns superseded items too — eval
        and the dashboard need to be able to inspect a row's ``superseded_by``
        chain to verify supersession behavior.

        Returns the same shape as :meth:`_row_to_knowledge_dict` (17 fields,
        embedding excluded).
        """
        sql = f"SELECT {self._KNOWLEDGE_COLS} FROM knowledge WHERE id = ?"
        with self._lock:
            row = self._conn.execute(sql, (knowledge_id,)).fetchone()
        return self._row_to_knowledge_dict(row) if row else None

    def get_upcoming(
        self,
        within_days: int = 7,
        include_overdue: bool = True,
        context: str | None = None,
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

        conditions = ["due_at IS NOT NULL", "superseded_by IS NULL"]
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
        content: str | None = None,
        category: str | None = None,
        domain: str | None = None,
        metadata: dict | None = None,
        context: str | None = None,
        sensitive: bool | None = None,
        entity: str | None = None,
        due_at: str | None = None,
        reminded_at: str | None = None,
        superseded_by: str | None = None,
    ) -> bool:
        """Update an existing knowledge entry. Only provided fields are changed.

        Sets updated_at to now. Returns False if ID not found.

        Args:
            superseded_by: ID of the newer knowledge item that replaces this one.
                When set, this item is considered historical/inactive and will be
                excluded from active queries (search, get_by_*, system prompt).
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
            if len(content) > MAX_CONTENT_LENGTH:
                content = content[:MAX_CONTENT_LENGTH]
            sets.append("content = ?")
            params.append(content)
            # Clear embedding — it was computed for the old content and is now
            # stale.  Forces the item into get_items_without_embeddings() for
            # re-embedding by the caller.
            sets.append("embedding = NULL")
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
        if superseded_by is not None:
            sets.append("superseded_by = ?")
            params.append(superseded_by)

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
    # Knowledge — Embeddings (v2)
    # ==================================================================

    def store_embedding(self, knowledge_id: str, embedding: bytes) -> bool:
        """Store an embedding BLOB for a knowledge item.

        Args:
            knowledge_id: UUID of the knowledge item.
            embedding: Raw bytes (float32 vector, e.g. 768-dim × 4 bytes = 3072 bytes).

        Returns:
            True if the row was found and updated, False if knowledge_id not found.
        """
        with self._lock:
            try:
                rowcount = self._conn.execute(
                    "UPDATE knowledge SET embedding = ? WHERE id = ?",
                    (embedding, knowledge_id),
                ).rowcount
                self._conn.commit()
                return rowcount > 0
            except Exception:
                self._conn.rollback()
                raise

    def clear_all_embeddings(self) -> Dict:
        """NULL out every stored embedding (knowledge + procedures).

        Used when the embedding model changes: vectors from a different model
        aren't comparable, so they must be regenerated. The rows themselves are
        kept — ``backfill_embeddings`` / skill synthesis re-embed lazily with the
        new model. Returns per-table counts of cleared embeddings.
        """
        with self._lock:
            try:
                knowledge = self._conn.execute(
                    "UPDATE knowledge SET embedding = NULL WHERE embedding IS NOT NULL"
                ).rowcount
                procedures = self._conn.execute(
                    "UPDATE procedures SET embedding = NULL WHERE embedding IS NOT NULL"
                ).rowcount
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        logger.info(
            "[MemoryStore] cleared embeddings (model change): knowledge=%d procedures=%d",
            knowledge,
            procedures,
        )
        return {"knowledge": knowledge, "procedures": procedures}

    def get_items_with_embeddings(
        self,
        category: str | None = None,
        context: str | None = None,
        entity: str | None = None,
        include_sensitive: bool = False,
        top_k: int = 100,
        time_from: str | None = None,
        time_to: str | None = None,
    ) -> List[Dict]:
        """Return active knowledge items that have stored embeddings.

        Filters: superseded_by IS NULL, embedding IS NOT NULL, plus optional
        category, context, entity, sensitive, and time-range filters.

        Returns items with ALL fields INCLUDING the embedding BLOB.
        Used by MemoryMixin to build/query the FAISS index.
        """
        conditions = ["superseded_by IS NULL", "embedding IS NOT NULL"]
        params: list = []

        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if context is not None:
            conditions.append("context = ?")
            params.append(context)
        if entity is not None:
            conditions.append("entity = ?")
            params.append(entity)
        if not include_sensitive:
            conditions.append("sensitive = 0")
        if time_from is not None:
            conditions.append("created_at >= ?")
            params.append(time_from)
        if time_to is not None:
            conditions.append("created_at <= ?")
            params.append(time_to)

        where = "WHERE " + " AND ".join(conditions)
        params.append(top_k)

        sql = f"""
            SELECT {self._KNOWLEDGE_COLS_WITH_EMBEDDING} FROM knowledge
            {where}
            ORDER BY confidence DESC, updated_at DESC
            LIMIT ?
        """

        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            return [
                self._row_to_knowledge_dict_with_embedding(r) for r in cursor.fetchall()
            ]

    def get_items_without_embeddings(self, limit: int = 100) -> List[Dict]:
        """Return knowledge items where embedding IS NULL.

        Used for backfill on startup — items created before v2 migration
        or items whose embedding failed on initial store.

        Returns standard dicts (no embedding field — it's NULL by definition).
        """
        sql = f"""
            SELECT {self._KNOWLEDGE_COLS} FROM knowledge
            WHERE embedding IS NULL AND superseded_by IS NULL
            ORDER BY created_at ASC
            LIMIT ?
        """
        with self._lock:
            cursor = self._conn.execute(sql, (limit,))
            return [self._row_to_knowledge_dict(r) for r in cursor.fetchall()]

    def get_embedding_coverage(self) -> Dict:
        """Return embedding coverage stats for all active knowledge items.

        Returns:
            {total_items, with_embedding, without_embedding, coverage_pct}
        """
        with self._lock:
            row = self._conn.execute("""
                SELECT
                    COUNT(*) AS total_items,
                    SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS with_embedding,
                    SUM(CASE WHEN embedding IS NULL THEN 1 ELSE 0 END) AS without_embedding
                FROM knowledge
                WHERE superseded_by IS NULL
                """).fetchone()
        total = row[0] or 0
        with_emb = row[1] or 0
        without_emb = row[2] or 0
        coverage_pct = round(with_emb / total * 100, 1) if total > 0 else 0.0
        return {
            "total_items": total,
            "with_embedding": with_emb,
            "without_embedding": without_emb,
            "coverage_pct": coverage_pct,
        }

    def backfill_embeddings(
        self, embed_fn: Callable[[str], bytes], limit: int = 500
    ) -> Dict:
        """Embed items that are missing embeddings using the provided function.

        Args:
            embed_fn: Callable that takes a text string and returns embedding
                bytes (float32 vector as raw bytes).
            limit: Maximum number of items to process in one call.

        Returns:
            {backfilled: int, total_without: int}
        """
        items = self.get_items_without_embeddings(limit=limit)
        total_without = len(items)
        backfilled = 0
        for item in items:
            try:
                embedding_bytes = embed_fn(item["content"])
                self.store_embedding(item["id"], embedding_bytes)
                backfilled += 1
            except Exception as exc:
                logger.warning(
                    "[MemoryStore] backfill embedding failed for %s: %s",
                    item["id"],
                    exc,
                )
        return {"backfilled": backfilled, "total_without": total_without}

    def get_items_for_reconciliation(
        self, context: str | None = None, limit: int = 100
    ) -> List[Dict]:
        """Get active knowledge items with embeddings for pairwise comparison.

        Used by the reconciliation pipeline to find near-duplicates and
        contradictions via cosine similarity on stored embeddings.

        Filters: superseded_by IS NULL, embedding IS NOT NULL.
        Returns items with ALL fields including the embedding BLOB.
        """
        conditions = ["superseded_by IS NULL", "embedding IS NOT NULL"]
        params: list = []

        if context is not None:
            conditions.append("context = ?")
            params.append(context)

        where = "WHERE " + " AND ".join(conditions)
        params.append(limit)

        sql = f"""
            SELECT {self._KNOWLEDGE_COLS_WITH_EMBEDDING} FROM knowledge
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
        """

        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            return [
                self._row_to_knowledge_dict_with_embedding(r) for r in cursor.fetchall()
            ]

    # ==================================================================
    # Consolidation (v2)
    # ==================================================================

    def get_unconsolidated_sessions(
        self, older_than_days: int = 14, min_turns: int = 5, limit: int = 5
    ) -> List[str]:
        """Find session_ids eligible for consolidation.

        A session is eligible when:
        - ALL its turns are older than ``older_than_days``
        - It has at least ``min_turns`` turns
        - At least one turn has ``consolidated_at IS NULL``

        Returns a list of session_id strings (oldest first), up to ``limit``.
        """
        cutoff = (
            datetime.now().astimezone() - timedelta(days=older_than_days)
        ).isoformat()

        sql = """
            SELECT session_id
            FROM conversations
            GROUP BY session_id
            HAVING COUNT(*) >= ?
               AND MAX(timestamp) < ?
               AND SUM(CASE WHEN consolidated_at IS NULL THEN 1 ELSE 0 END) > 0
            ORDER BY MAX(timestamp) ASC
            LIMIT ?
        """

        with self._lock:
            cursor = self._conn.execute(sql, (min_turns, cutoff, limit))
            return [row[0] for row in cursor.fetchall()]

    def mark_turns_consolidated(self, turn_ids: List[int]) -> int:
        """Set ``consolidated_at`` to now on the specified conversation turn IDs.

        Returns the number of rows actually updated.
        """
        if not turn_ids:
            return 0

        now = _now_iso()
        placeholders = ", ".join("?" for _ in turn_ids)
        sql = f"""
            UPDATE conversations
            SET consolidated_at = ?
            WHERE id IN ({placeholders}) AND consolidated_at IS NULL
        """

        with self._lock:
            try:
                rowcount = self._conn.execute(sql, (now, *turn_ids)).rowcount
                self._conn.commit()
                return rowcount
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
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Log a tool call to tool_history."""
        now = _now_iso()
        args_json = json.dumps(args, default=str) if args else None
        # Truncate all text columns to MAX_FTS_QUERY_LENGTH chars.  Tool args,
        # results, and error messages can all be arbitrarily large (e.g.
        # write_file called with 100 KB content).  Storing the full payload
        # bloats the database without adding search or observability value.
        if args_json and len(args_json) > MAX_FTS_QUERY_LENGTH:
            args_json = args_json[:MAX_FTS_QUERY_LENGTH]
        if result_summary and len(result_summary) > MAX_FTS_QUERY_LENGTH:
            result_summary = result_summary[:MAX_FTS_QUERY_LENGTH]
        if error and len(error) > MAX_FTS_QUERY_LENGTH:
            error = error[:MAX_FTS_QUERY_LENGTH]

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

    def get_tool_errors(
        self, tool_name: str | None = None, limit: int = 10
    ) -> List[Dict]:
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
            params: tuple[Any, ...] = (tool_name, limit)
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

            k_total_retrievals = self._conn.execute(
                "SELECT COALESCE(SUM(use_count), 0) FROM knowledge"
            ).fetchone()[0]

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
                AND superseded_by IS NULL
                """,
                (future_7d, now_iso),
            ).fetchone()[0]

            overdue_count = self._conn.execute(
                """
                SELECT COUNT(*) FROM knowledge
                WHERE due_at IS NOT NULL AND due_at <= ?
                AND (reminded_at IS NULL OR reminded_at < due_at)
                AND superseded_by IS NULL
                """,
                (now_iso,),
            ).fetchone()[0]

            # Procedures (procedural memory / skills, #887). COUNT(*) instead of
            # fetching rows so a large procedure table never loads into memory.
            p_total = self._conn.execute("SELECT COUNT(*) FROM procedures").fetchone()[
                0
            ]
            p_active = self._conn.execute(
                "SELECT COUNT(*) FROM procedures "
                "WHERE enabled = 1 AND superseded_by IS NULL"
            ).fetchone()[0]
            p_last_recalled = self._conn.execute(
                "SELECT MAX(last_used_at) FROM procedures"
            ).fetchone()[0]

        # DB size
        try:
            db_size = os.path.getsize(str(self._db_path))
        except OSError:
            db_size = 0

        return {
            "knowledge": {
                "total": k_total,
                "total_retrievals": k_total_retrievals,
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
            "procedures": {
                "total": p_total,
                "active": p_active,
                "last_recalled": p_last_recalled,
            },
            "db_size_bytes": db_size,
        }

    def get_all_knowledge(
        self,
        category: Optional[Union[str, List[str]]] = None,
        context: str | None = None,
        entity: str | None = None,
        sensitive: bool | None = None,
        search: str | None = None,
        sort_by: str = "updated_at",
        order: str = "desc",
        offset: int = 0,
        limit: int = 50,
        include_superseded: bool = False,
    ) -> Dict:
        """Paginated knowledge browser with full filtering.

        By default excludes superseded items.  Set ``include_superseded=True``
        to see the full history including superseded entries (their
        ``superseded_by`` field will be non-None in the returned dicts).

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

        if not include_superseded:
            conditions.append("k.superseded_by IS NULL")

        if category is not None:
            if isinstance(category, list):
                if len(category) == 1:
                    conditions.append("k.category = ?")
                    params.append(category[0])
                elif len(category) > 1:
                    placeholders = ",".join("?" * len(category))
                    conditions.append(f"k.category IN ({placeholders})")
                    params.extend(category)
                # empty list → no category filter
            else:
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
        all_days: set[str] = set()
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

    def delete_by_category(self, category: str) -> int:
        """Delete all knowledge entries with the given category. Returns deleted count.

        Atomically cleans FTS5 index and knowledge table in one transaction.
        """
        with self._lock:
            try:
                self._conn.execute(
                    """
                    DELETE FROM knowledge_fts
                    WHERE rowid IN (SELECT rowid FROM knowledge WHERE category = ?)
                    """,
                    (category,),
                )
                deleted = self._conn.execute(
                    "DELETE FROM knowledge WHERE category = ?", (category,)
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
        """List conversation sessions with turn counts and first message preview.

        Uses a single-pass query with a LEFT JOIN to the minimum user-turn ID
        per session, avoiding an N+1 correlated subquery.
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT c.session_id,
                       COUNT(*) as turn_count,
                       MIN(c.timestamp) as started_at,
                       MAX(c.timestamp) as last_activity,
                       first_user.content as first_message
                FROM conversations c
                LEFT JOIN (
                    SELECT session_id, content
                    FROM conversations
                    WHERE id IN (
                        SELECT MIN(id) FROM conversations
                        WHERE role = 'user'
                        GROUP BY session_id
                    )
                ) first_user ON first_user.session_id = c.session_id
                GROUP BY c.session_id
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

    # ==================================================================
    # Procedures (v3 — procedural memory, #887)
    # ==================================================================

    def _row_to_procedure_dict(self, row) -> Dict:
        """Convert a procedures row (``_PROCEDURE_COLS`` order) to a dict.

        Does NOT include the ``embedding`` BLOB — only callers that pass
        ``with_embedding=True`` to ``search_skills`` get it (via
        ``_row_to_procedure_dict_with_embedding``).
        """
        return {
            "id": row[0],
            "name": row[1],
            "when_to_use": row[2],
            "markdown_body": row[3],
            "tools_required": _safe_json_loads(row[4]),
            "tool_sequence": _safe_json_loads(row[5]),
            "success_count": row[6],
            "attempt_count": row[7],
            "provenance": _safe_json_loads(row[8]),
            "version": row[9],
            "enabled": bool(row[10]),
            "superseded_by": row[11],
            "created_at": row[12],
            "last_used_at": row[13],
        }

    def _row_to_procedure_dict_with_embedding(self, row) -> Dict:
        """Convert a 15-column procedures row (trailing embedding BLOB) to a dict."""
        d = self._row_to_procedure_dict(row)
        d["embedding"] = row[14]  # bytes or None
        return d

    def put_skill(
        self,
        name: str,
        when_to_use: str,
        markdown_body: str,
        tools_required: list | None = None,
        tool_sequence: list | None = None,
        success_count: int = 0,
        attempt_count: int = 0,
        provenance: dict | None = None,
        version: str = "1.0.0",
        enabled: bool = True,
        embedding: bytes | None = None,
        skill_id: str | None = None,
    ) -> str:
        """Insert or update a procedure row (procedural memory).

        With ``skill_id`` None a new ``proc_<uuid>`` id is generated and a row
        is inserted (Mem0 ADD).  With ``skill_id`` referring to an existing row,
        that row is updated in place (Mem0 UPDATE).  A ``skill_id`` that does not
        match any row raises — callers update only ids they read back from
        ``search_skills`` and insert with ``skill_id=None``.  No row is ever
        deleted by this method; reconciliation only ADDs, UPDATEs, or supersedes.

        Args:
            name: kebab-case procedure name (→ SKILL.md frontmatter ``name``).
            when_to_use: trigger text; embedded for recall (→ ``description``).
            markdown_body: full procedure body, edge cases inline.
            tools_required: tool names the procedure uses (stored as JSON).
            tool_sequence: distilled ordered step pattern (stored as JSON).
            success_count: successful runs behind this procedure.
            attempt_count: total runs (success_count / attempt_count = rate).
            provenance: audit dict, e.g.
                ``{"source": "synthesized", "from_sessions": [...]}``.
            version: SemVer string; freshly synthesized procedures are "1.0.0".
            enabled: False disables recall without deleting the row.
            embedding: 768-float32 BLOB over ``when_to_use`` (its own FAISS index).
            skill_id: existing procedure id to update; None inserts a new row.

        Returns:
            The procedure id (existing on update, new ``proc_<uuid>`` on insert).

        Raises:
            ValueError: if ``name``, ``when_to_use``, or ``markdown_body`` is
                empty, or if ``skill_id`` is given but matches no existing row.
        """
        if not name or not name.strip():
            raise ValueError("MemoryStore.put_skill(): name must be non-empty")
        if not when_to_use or not when_to_use.strip():
            raise ValueError("MemoryStore.put_skill(): when_to_use must be non-empty")
        if not markdown_body or not markdown_body.strip():
            raise ValueError("MemoryStore.put_skill(): markdown_body must be non-empty")

        tools_json = json.dumps(tools_required) if tools_required is not None else None
        seq_json = json.dumps(tool_sequence) if tool_sequence is not None else None
        prov_json = json.dumps(provenance) if provenance is not None else None
        now = _now_iso()

        with self._lock:
            try:
                # existing_id is the str id only when skill_id was provided AND
                # matches a row — keeping it a narrowed str (not str | None) so
                # the UPDATE branch and the return value are provably typed.
                existing_id: str | None = None
                if skill_id is not None:
                    row = self._conn.execute(
                        "SELECT id FROM procedures WHERE id = ?", (skill_id,)
                    ).fetchone()
                    if row is None:
                        raise ValueError(
                            f"MemoryStore.put_skill(): skill_id {skill_id!r} not "
                            "found; pass skill_id=None to insert a new procedure"
                        )
                    existing_id = skill_id

                if existing_id is not None:
                    self._conn.execute(
                        """
                        UPDATE procedures SET
                            name = ?, when_to_use = ?, markdown_body = ?,
                            tools_required = ?, tool_sequence = ?,
                            success_count = ?, attempt_count = ?, provenance = ?,
                            version = ?, enabled = ?, embedding = ?
                        WHERE id = ?
                        """,
                        (
                            name,
                            when_to_use,
                            markdown_body,
                            tools_json,
                            seq_json,
                            int(success_count),
                            int(attempt_count),
                            prov_json,
                            version,
                            int(enabled),
                            embedding,
                            existing_id,
                        ),
                    )
                    result_id = existing_id
                    action = "updated"
                else:
                    # Reached only when skill_id is None (a provided-but-missing
                    # id raised above), so the id is always freshly generated.
                    result_id = f"proc_{uuid4().hex}"
                    self._conn.execute(
                        """
                        INSERT INTO procedures
                            (id, name, when_to_use, markdown_body, tools_required,
                             tool_sequence, success_count, attempt_count, provenance,
                             version, enabled, embedding, superseded_by, created_at,
                             last_used_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result_id,
                            name,
                            when_to_use,
                            markdown_body,
                            tools_json,
                            seq_json,
                            int(success_count),
                            int(attempt_count),
                            prov_json,
                            version,
                            int(enabled),
                            embedding,
                            None,  # superseded_by
                            now,  # created_at
                            None,  # last_used_at
                        ),
                    )
                    action = "stored"
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        logger.info("[MemoryStore] procedure %s id=%s name=%s", action, result_id, name)
        return result_id

    def search_skills(
        self,
        skill_id: str | None = None,
        name: str | None = None,
        enabled_only: bool = True,
        include_superseded: bool = False,
        with_embedding: bool = False,
        limit: int = 100,
    ) -> List[Dict]:
        """Query procedures by id / name / enabled flag.

        Args:
            skill_id: exact procedure id to fetch.  The ``enabled_only`` and
                ``include_superseded`` filters still apply, so an exact-id lookup
                of a disabled or superseded row returns nothing unless those
                flags are relaxed (pass ``enabled_only=False,
                include_superseded=True`` to fetch any row by id).
            name: exact procedure name to fetch.
            enabled_only: when True (default) exclude ``enabled = 0`` rows — the
                recall path passes this so a disabled procedure is never returned.
            include_superseded: when False (default) exclude superseded rows.
            with_embedding: when True include the ``embedding`` BLOB in each dict
                (used to build the procedures FAISS index); otherwise omit it.
            limit: maximum rows to return.

        Returns:
            A list of procedure dicts, newest-first.
        """
        cols = (
            self._PROCEDURE_COLS_WITH_EMBEDDING
            if with_embedding
            else self._PROCEDURE_COLS
        )
        conditions: list = []
        params: list = []
        if skill_id is not None:
            conditions.append("id = ?")
            params.append(skill_id)
        if name is not None:
            conditions.append("name = ?")
            params.append(name)
        if enabled_only:
            conditions.append("enabled = 1")
        if not include_superseded:
            conditions.append("superseded_by IS NULL")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        sql = f"""
            SELECT {cols} FROM procedures
            {where}
            ORDER BY created_at DESC
            LIMIT ?
        """
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()

        if with_embedding:
            return [self._row_to_procedure_dict_with_embedding(r) for r in rows]
        return [self._row_to_procedure_dict(r) for r in rows]

    def supersede_skill(self, skill_id: str, superseded_by: str) -> bool:
        """Mark ``skill_id`` as superseded by ``superseded_by`` (Zep-style lineage).

        The superseded row is kept, never deleted; recall excludes it via the
        ``superseded_by IS NULL`` filter in ``search_skills``.

        Returns:
            True if a row was updated, False if ``skill_id`` was not found.
        """
        with self._lock:
            try:
                rowcount = self._conn.execute(
                    "UPDATE procedures SET superseded_by = ? WHERE id = ?",
                    (superseded_by, skill_id),
                ).rowcount
                self._conn.commit()
                return rowcount > 0
            except Exception:
                self._conn.rollback()
                raise

    def touch_skills(self, skill_ids: List[str], when: str | None = None) -> int:
        """Stamp ``last_used_at`` on the given procedures (recall telemetry).

        Called when ``recall_skill`` surfaces procedures for a goal so
        ``gaia memory status`` can report when a skill was last reused.

        Args:
            skill_ids: procedure ids that were just recalled.
            when: ISO 8601 timestamp to record; defaults to now.

        Returns:
            Number of rows updated.
        """
        if not skill_ids:
            return 0
        stamp = when or _now_iso()
        placeholders = ",".join("?" for _ in skill_ids)
        with self._lock:
            try:
                rowcount = self._conn.execute(
                    f"UPDATE procedures SET last_used_at = ? WHERE id IN ({placeholders})",
                    (stamp, *skill_ids),
                ).rowcount
                self._conn.commit()
                return rowcount
            except Exception:
                self._conn.rollback()
                raise

    def iter_sessions(self, since: str | None = None, min_steps: int = 3) -> List[Dict]:
        """Return per-session successful tool spans + the session goal (DETECT).

        Walks ``tool_history`` grouped by ``session_id`` and returns, for each
        session whose successful tool calls number at least ``min_steps``, the
        ordered successful tool sequence together with the goal — derived with
        plain SQL as the first ``role='user'`` turn of that session
        (``tool_history`` has no goal column).  This is the DETECT primitive for
        skill synthesis: it runs no LLM and issues a single query (the heavy
        per-session eligibility filter runs in SQL, not as N per-session reads).

        Args:
            since: ISO 8601 watermark; only tool calls strictly newer are
                considered.  None scans all history.
            min_steps: minimum successful tool calls for a session to qualify.

        Returns:
            A list of dicts, oldest session first, each shaped
            ``{session_id, goal, tools, tool_sequence, success_count,
            attempt_count, started_at, last_at}``.  ``goal`` is None when the
            session has no user turn.
        """
        sql = """
            WITH eligible AS (
                SELECT session_id
                FROM tool_history
                WHERE (:since IS NULL OR timestamp > :since)
                GROUP BY session_id
                HAVING SUM(success) >= :min_steps
            )
            SELECT th.session_id, th.tool_name, th.args, th.success, th.timestamp,
                   g.content AS goal
            FROM tool_history th
            JOIN eligible e ON e.session_id = th.session_id
            LEFT JOIN (
                SELECT session_id, content
                FROM conversations
                WHERE id IN (
                    SELECT MIN(id) FROM conversations
                    WHERE role = 'user'
                    GROUP BY session_id
                )
            ) g ON g.session_id = th.session_id
            WHERE (:since IS NULL OR th.timestamp > :since)
            ORDER BY th.session_id, th.id
        """
        params = {"since": since, "min_steps": min_steps}
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        # Group the ordered rows into per-session spans.  The eligible CTE has
        # already pre-filtered to sessions with >= min_steps successes, so this
        # pass is cheap (no further DB reads).
        sessions: Dict[str, Dict] = {}
        order: List[str] = []
        for session_id, tool_name, args, success, timestamp, goal in rows:
            sess = sessions.get(session_id)
            if sess is None:
                sess = {
                    "session_id": session_id,
                    "goal": goal,
                    "tools": [],
                    "tool_sequence": [],
                    "success_count": 0,
                    "attempt_count": 0,
                    "started_at": timestamp,
                    "last_at": timestamp,
                }
                sessions[session_id] = sess
                order.append(session_id)
            sess["attempt_count"] += 1
            sess["last_at"] = timestamp
            if success:
                sess["success_count"] += 1
                sess["tools"].append(tool_name)
                sess["tool_sequence"].append(
                    {"tool": tool_name, "args": _safe_json_loads(args)}
                )

        # The CTE guarantees the floor, but keep it explicit so callers can rely
        # on every returned session having >= min_steps successful steps.
        return [
            sessions[sid]
            for sid in order
            if sessions[sid]["success_count"] >= min_steps
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
                          AND superseded_by IS NULL
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
                    WHERE confidence < ? AND last_used IS NOT NULL AND last_used < ?
                    """,
                    (LOW_CONFIDENCE_PRUNE_THRESHOLD, cutoff),
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

    def clear_all(self) -> Dict:
        """Permanently delete all knowledge, tool history, and conversation data.

        Wipes every knowledge entry, tool call log, and conversation turn
        from the database.  FTS5 indexes are reset to empty in the same
        transaction so search stays consistent.

        Returns:
            Dict with counts of deleted rows per table:
            ``{knowledge: int, tool_history: int, conversations: int}``
        """
        with self._lock:
            try:
                knowledge_deleted = self._conn.execute("DELETE FROM knowledge").rowcount
                self._rebuild_knowledge_fts_locked()
                tool_deleted = self._conn.execute("DELETE FROM tool_history").rowcount
                conv_deleted = self._conn.execute("DELETE FROM conversations").rowcount
                self._rebuild_conversations_fts_locked()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        logger.info(
            "[MemoryStore] cleared all: knowledge=%d tool_history=%d conversations=%d",
            knowledge_deleted,
            tool_deleted,
            conv_deleted,
        )
        return {
            "knowledge": knowledge_deleted,
            "tool_history": tool_deleted,
            "conversations": conv_deleted,
        }

    def clear_knowledge(self) -> Dict:
        """Delete all knowledge rows and rebuild the knowledge FTS index.

        Leaves tool_history and conversations untouched.  Used by eval to
        reset structural state between scenarios while keeping conversation
        history (or vice versa) for cross-session tests.
        """
        with self._lock:
            try:
                deleted = self._conn.execute("DELETE FROM knowledge").rowcount
                self._rebuild_knowledge_fts_locked()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        logger.info("[MemoryStore] cleared knowledge: %d rows", deleted)
        return {"knowledge": deleted}

    def clear_conversations(self) -> Dict:
        """Delete all conversation turns and rebuild the conversations FTS index.

        Leaves knowledge and tool_history untouched.
        """
        with self._lock:
            try:
                deleted = self._conn.execute("DELETE FROM conversations").rowcount
                self._rebuild_conversations_fts_locked()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        logger.info("[MemoryStore] cleared conversations: %d rows", deleted)
        return {"conversations": deleted}

    def seed_bulk(self, items: List[Dict]) -> List[str]:
        """Bulk-insert knowledge rows, bypassing dedup.

        Eval-only helper for stress scenarios that need a known cluttered
        memory state (e.g. "200 prior facts — does proactive surfacing still
        find the relevant one?").  Each item is a dict with the same keys
        accepted by :meth:`store`.

        Bypassing dedup is the whole point — we want exact rows, not
        merged-by-similarity rows.  In return, callers are responsible for
        not seeding contradictory clusters they then expect to find via
        normal search.

        Returns the list of generated knowledge IDs in input order.

        Raises ValueError on the FIRST malformed item, before any rows are
        inserted (atomic-or-nothing).
        """
        if not items:
            return []

        # Pre-validate the entire batch so a bad item at index 17 doesn't
        # leave 16 rows partially inserted.
        normalized: List[Dict] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"seed_bulk: item[{idx}] must be a dict")
            content = item.get("content", "")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(
                    f"seed_bulk: item[{idx}].content must be a non-empty string"
                )
            category = item.get("category", "fact")
            if category not in VALID_CATEGORIES:
                raise ValueError(
                    f"seed_bulk: item[{idx}].category={category!r} is not in "
                    f"{sorted(VALID_CATEGORIES)}"
                )
            confidence = float(item.get("confidence", 0.7))
            confidence = max(0.0, min(1.0, confidence))
            normalized.append(
                {
                    "id": str(uuid4()),
                    "category": category,
                    "content": content[:MAX_CONTENT_LENGTH],
                    "domain": item.get("domain") or None,
                    "source": item.get("source", "seed"),
                    "confidence": confidence,
                    "metadata": (
                        json.dumps(item["metadata"]) if item.get("metadata") else None
                    ),
                    "context": item.get("context", "global"),
                    "sensitive": int(bool(item.get("sensitive", False))),
                    "entity": item.get("entity") or None,
                    "due_at": item.get("due_at") or None,
                }
            )

        now = _now_iso()
        ids: List[str] = []
        with self._lock:
            try:
                for n in normalized:
                    self._conn.execute(
                        """
                        INSERT INTO knowledge (
                            id, category, content, domain, source, confidence,
                            metadata, use_count, context, sensitive, entity,
                            created_at, updated_at, last_used, due_at,
                            reminded_at, superseded_by
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, NULL, ?,
                            NULL, NULL
                        )
                        """,
                        (
                            n["id"],
                            n["category"],
                            n["content"],
                            n["domain"],
                            n["source"],
                            n["confidence"],
                            n["metadata"],
                            n["context"],
                            n["sensitive"],
                            n["entity"],
                            now,
                            now,
                            n["due_at"],
                        ),
                    )
                    self._insert_knowledge_fts_locked(n["id"])
                    ids.append(n["id"])
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        logger.info("[MemoryStore] seed_bulk: inserted %d items", len(ids))
        return ids

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
