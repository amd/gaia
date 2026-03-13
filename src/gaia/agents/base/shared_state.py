# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
SharedAgentState: Persistent Memory Infrastructure

Core data layer for agent memory across sessions. Provides:
- MemoryDB: Session-scoped working memory (key-value + FTS5, file cache,
  tool results, conversation history with FTS5)
- KnowledgeDB: Cross-session persistent storage (insights with categories/metadata,
  credentials, preferences)
- SharedAgentState: Thread-safe singleton holding MemoryDB + KnowledgeDB

This module is agent-agnostic — it imports NOTHING from specific agent
implementations. Only stdlib dependencies (sqlite3, threading, etc.).

Ported and simplified from gaia-v2 SharedAgentState:
- Consolidated 7+ databases into 2 (memory.db + knowledge.db)
- FTS5 uses AND by default (not OR) with OR fallback on zero results
- Added insight deduplication (>80% word overlap)
- Added confidence decay (0.9x after 30 days of no access)
- Added FTS5 on active_state (working memory), not just LIKE search
- Dropped: LogsDB, MasterPlan, AgentCallStack, SkillsDB, ToolsDB, AgentsDB
"""

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
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
        logger.debug("[FTS5] query empty/invalid, returning None")
        return None

    # Replace FTS5 special chars with spaces, keep alphanumeric and underscores
    sanitized = re.sub(r"[^\w\s]", " ", query)
    # Collapse multiple spaces
    sanitized = re.sub(r"\s+", " ", sanitized).strip()

    if not sanitized:
        logger.debug("[FTS5] query empty after sanitization, returning None")
        return None

    words = sanitized.split()
    if len(words) > 1:
        operator = " AND " if use_and else " OR "
        result = operator.join(words)
        logger.debug("[FTS5] sanitized %r -> %r (use_and=%s)", query, result, use_and)
        return result

    logger.debug("[FTS5] sanitized %r -> %r (single word)", query, sanitized)
    return sanitized


def _word_overlap(text1: str, text2: str) -> float:
    """Calculate word overlap ratio between two texts using overlap coefficient.

    Uses Szymkiewicz-Simpson coefficient: |intersection| / min(|A|, |B|)
    This is appropriate for dedup because a subset of a longer text should
    still be considered a match.

    Args:
        text1: First text to compare.
        text2: Second text to compare.

    Returns:
        Float between 0.0 and 1.0 representing overlap ratio.
    """
    words1 = set(re.sub(r"[^\w\s]", " ", text1.lower()).split())
    words2 = set(re.sub(r"[^\w\s]", " ", text2.lower()).split())

    if not words1 or not words2:
        return 0.0

    intersection = words1 & words2
    min_size = min(len(words1), len(words2))

    return len(intersection) / min_size if min_size > 0 else 0.0


# ============================================================================
# MemoryDB: Session-Scoped Working Memory
# ============================================================================


class MemoryDB:
    """
    Session-scoped working memory cache.

    Stores:
    - Active state: key-value facts with FTS5 search
    - File cache: contents read during this session
    - Tool results: recent tool call outputs
    - Conversation history: persistent across sessions with FTS5

    Thread-safe via internal lock. Shared across all agents in a session.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.lock = threading.Lock()
        self._create_tables()
        logger.debug("[MemoryDB] initialized at %s", db_path)

    def _create_tables(self):
        """Create memory cache tables with FTS5 indexes."""
        with self.lock:
            # File cache
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS file_cache (
                    path TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    last_accessed TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
                )
                """)

            # Tool results
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    args TEXT,
                    result TEXT,
                    timestamp TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
                )
                """)

            # Active state: key-value working memory
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS active_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    tags TEXT,
                    stored_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
                    last_accessed TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
                )
                """)

            # FTS5 on active_state for content search (not just LIKE)
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS active_state_fts
                USING fts5(key, value, tags)
                """)

            # Triggers to keep active_state_fts in sync
            # INSERT trigger
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS as_ai
                AFTER INSERT ON active_state BEGIN
                    INSERT INTO active_state_fts(rowid, key, value, tags)
                    VALUES (new.rowid, new.key, new.value, COALESCE(new.tags, ''));
                END
                """)

            # DELETE trigger (also fires on the DELETE part of INSERT OR REPLACE)
            # For standalone FTS5 tables, use regular DELETE (not the special
            # 'delete' command which is only for content= external tables)
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS as_ad
                AFTER DELETE ON active_state BEGIN
                    DELETE FROM active_state_fts WHERE rowid = old.rowid;
                END
                """)

            # Conversation history — persistent across sessions
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
                )
                """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_session
                ON conversation_history(session_id)
                """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_timestamp
                ON conversation_history(timestamp DESC)
                """)

            # FTS5 for conversation search
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts
                USING fts5(content, content=conversation_history, content_rowid=id)
                """)
            # Sync triggers for conversation FTS5
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS conv_ai
                AFTER INSERT ON conversation_history BEGIN
                    INSERT INTO conversation_fts(rowid, content) VALUES (new.id, new.content);
                END
                """)
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS conv_ad
                AFTER DELETE ON conversation_history BEGIN
                    INSERT INTO conversation_fts(conversation_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                END
                """)

            self.conn.commit()

    # ------------------------------------------------------------------
    # Active State (Working Memory)
    # ------------------------------------------------------------------

    def store_memory(
        self,
        key: str,
        value: str,
        tags: Optional[List[str]] = None,
    ):
        """Store an arbitrary fact or context value under a key.

        This is the agent's working memory — used to persist important context
        across tool calls and sub-tasks within a session. Examples:
            store_memory("current_project", "~/Work/gaia")
            store_memory("auth_approach", "JWT with RS256", tags=["architecture"])
        """
        tags_json = json.dumps(tags) if tags else None
        with self.lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO active_state
                    (key, value, tags, stored_at, last_accessed)
                VALUES (?, ?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'),
                        strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
                """,
                (key, value, tags_json),
            )
            self.conn.commit()
        logger.debug("[MemoryDB] stored key=%s", key)

    def recall_memories(
        self,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Recall memories from active_state.

        Uses FTS5 search with AND semantics by default. Falls back to OR
        on zero results. Without a query, returns most recent entries.

        Args:
            query: Search terms (FTS5-sanitized). None returns recent entries.
            limit: Maximum results to return.

        Returns:
            List of dicts with keys: key, value, tags, stored_at.
        """
        with self.lock:
            if query:
                # Try FTS5 with AND semantics first
                safe_query = _sanitize_fts5_query(query, use_and=True)
                if safe_query:
                    rows = self._fts5_search_active_state(safe_query, limit)
                    if not rows:
                        # Fallback to OR on zero AND results
                        safe_query_or = _sanitize_fts5_query(query, use_and=False)
                        if safe_query_or:
                            rows = self._fts5_search_active_state(safe_query_or, limit)
                        else:
                            rows = []
                else:
                    rows = []
            else:
                # No query — return most recent (rowid DESC as tiebreaker for same-second inserts)
                cursor = self.conn.execute(
                    """
                    SELECT key, value, tags, stored_at
                    FROM active_state
                    ORDER BY last_accessed DESC, rowid DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()

        results = []
        for r in rows:
            results.append(
                {
                    "key": r[0],
                    "value": r[1],
                    "tags": json.loads(r[2]) if r[2] else [],
                    "stored_at": r[3],
                }
            )

        logger.debug("[MemoryDB] recall query=%r results=%d", query, len(results))
        return results

    def _fts5_search_active_state(self, fts_query: str, limit: int) -> List[Tuple]:
        """Execute FTS5 search on active_state_fts.

        Must be called with self.lock held.

        Returns list of tuples: (key, value, tags, stored_at).
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT a.key, a.value, a.tags, a.stored_at
                FROM active_state a
                JOIN active_state_fts f ON a.rowid = f.rowid
                WHERE active_state_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
            )
            return cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("[MemoryDB] FTS5 search error: %s", e)
            return []

    def get_memory(self, key: str) -> Optional[str]:
        """Get a specific memory by exact key."""
        with self.lock:
            cursor = self.conn.execute(
                "SELECT value FROM active_state WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            if row:
                self.conn.execute(
                    "UPDATE active_state SET last_accessed = strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime') WHERE key = ?",
                    (key,),
                )
                self.conn.commit()
        value = row[0] if row else None
        logger.debug("[MemoryDB] get_memory key=%s found=%s", key, value is not None)
        return value

    def forget_memory(self, key: str) -> bool:
        """Remove a specific memory entry."""
        with self.lock:
            rowcount = self.conn.execute(
                "DELETE FROM active_state WHERE key = ?", (key,)
            ).rowcount
            self.conn.commit()
        logger.debug("[MemoryDB] forget key=%s deleted=%s", key, rowcount > 0)
        return rowcount > 0

    # ------------------------------------------------------------------
    # File Cache
    # ------------------------------------------------------------------

    def cache_file(self, path: str, content: str):
        """Cache a file's contents."""
        with self.lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO file_cache (path, content, last_accessed)
                VALUES (?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
                """,
                (path, content),
            )
            self.conn.commit()
        logger.debug("[MemoryDB] cached path=%s size=%d", path, len(content))

    def get_file(self, path: str) -> Optional[str]:
        """Get cached file contents."""
        with self.lock:
            cursor = self.conn.execute(
                "SELECT content FROM file_cache WHERE path = ?", (path,)
            )
            row = cursor.fetchone()
        content = row[0] if row else None
        if content is not None:
            logger.debug("[MemoryDB] cache hit path=%s", path)
        else:
            logger.debug("[MemoryDB] cache miss path=%s", path)
        return content

    # ------------------------------------------------------------------
    # Tool Results
    # ------------------------------------------------------------------

    def store_tool_result(self, tool_name: str, args: Dict, result: str):
        """Store a tool call result."""
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO tool_results (tool_name, args, result)
                VALUES (?, ?, ?)
                """,
                (tool_name, json.dumps(args), result),
            )
            self.conn.commit()
        logger.debug("[MemoryDB] tool result stored tool=%s", tool_name)

    def get_tool_results(self, limit: int = 20) -> List[Dict]:
        """Get recent tool results."""
        with self.lock:
            cursor = self.conn.execute(
                """
                SELECT tool_name, args, result, timestamp
                FROM tool_results
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return [
            {
                "tool_name": r[0],
                "args": json.loads(r[1]) if r[1] else None,
                "result": r[2],
                "timestamp": r[3],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Conversation History
    # ------------------------------------------------------------------

    def store_conversation_turn(self, session_id: str, role: str, content: str):
        """Persist one conversation turn (role='user' or 'assistant') to the DB."""
        with self.lock:
            self.conn.execute(
                "INSERT INTO conversation_history (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            self.conn.commit()
        logger.debug(
            "[MemoryDB] stored conversation turn session=%s role=%s",
            session_id,
            role,
        )

    def get_conversation_history(
        self, session_id: str = None, limit: int = 20
    ) -> List[Dict]:
        """Retrieve recent conversation turns, optionally filtered by session.

        Returns list ordered oldest-first for direct use as messages array.
        """
        with self.lock:
            if session_id:
                # Get the most recent N turns for this session, then re-order oldest-first
                cursor = self.conn.execute(
                    """
                    SELECT id, session_id, role, content, timestamp
                    FROM (
                        SELECT id, session_id, role, content, timestamp
                        FROM conversation_history
                        WHERE session_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    ) ORDER BY id ASC
                    """,
                    (session_id, limit),
                )
            else:
                # Get the most recent N turns across all sessions, then re-order oldest-first
                cursor = self.conn.execute(
                    """
                    SELECT id, session_id, role, content, timestamp
                    FROM (
                        SELECT id, session_id, role, content, timestamp
                        FROM conversation_history
                        ORDER BY id DESC
                        LIMIT ?
                    ) ORDER BY id ASC
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "timestamp": r[4],
            }
            for r in rows
        ]

    def search_conversations(self, query: str, limit: int = 10) -> List[Dict]:
        """Full-text search across all stored conversation turns.

        Uses FTS5 with AND semantics, falls back to OR on zero results.
        """
        safe_query = _sanitize_fts5_query(query, use_and=True)
        if not safe_query:
            return []

        with self.lock:
            try:
                results = self._fts5_search_conversations(safe_query, limit)
                if not results:
                    safe_query_or = _sanitize_fts5_query(query, use_and=False)
                    if safe_query_or:
                        results = self._fts5_search_conversations(safe_query_or, limit)
                    else:
                        results = []
            except Exception:
                results = []

        logger.debug(
            "[MemoryDB] conversation search query=%r results=%d",
            query,
            len(results),
        )
        return results

    def _fts5_search_conversations(self, fts_query: str, limit: int) -> List[Dict]:
        """Execute FTS5 search on conversation_fts.

        Must be called with self.lock held.
        """
        cursor = self.conn.execute(
            """
            SELECT c.id, c.session_id, c.role, c.content, c.timestamp
            FROM conversation_history c
            JOIN conversation_fts f ON c.id = f.rowid
            WHERE conversation_fts MATCH ?
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
                "timestamp": r[4],
            }
            for r in cursor.fetchall()
        ]

    # ------------------------------------------------------------------
    # Clear / Reset
    # ------------------------------------------------------------------

    def clear_working_memory(self):
        """Clear all working/session-scoped tables.

        Preserves conversation_history (persistent across sessions by design).
        Clears: active_state, file_cache, tool_results.
        """
        with self.lock:
            self.conn.execute("DELETE FROM active_state")
            self.conn.execute("DELETE FROM file_cache")
            self.conn.execute("DELETE FROM tool_results")
            self.conn.commit()
        logger.info(
            "[MemoryDB] working memory cleared (active_state, file_cache, tool_results)"
        )

    def close(self):
        """Close the database connection."""
        try:
            self.conn.close()
        except Exception:
            pass


# ============================================================================
# KnowledgeDB: Cross-Session Persistent Storage
# ============================================================================


class KnowledgeDB:
    """
    Cross-session persistent knowledge database.

    Stores:
    - Insights: consolidated table handling facts, strategies, skills, tools,
      agents via category field + metadata JSON column.
    - Credentials: encrypted storage for API keys, OAuth tokens, etc.
    - Preferences: simple key-value user preferences.

    Features:
    - FTS5 search with AND default, OR fallback, bm25 ranking
    - Insight deduplication (>80% word overlap in same category)
    - Confidence decay (0.9x after 30 days of no access)
    - Usage tracking (success/failure counts)
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.lock = threading.Lock()
        self._create_tables()
        logger.debug("[KnowledgeDB] initialized at %s", db_path)

    def _create_tables(self):
        """Create knowledge tables with FTS5 search."""
        with self.lock:
            # Consolidated insights table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS insights (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    domain TEXT,
                    content TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    triggers TEXT,
                    metadata TEXT,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    use_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
                    last_used TIMESTAMP
                )
                """)

            # Credentials table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    id TEXT PRIMARY KEY,
                    service TEXT NOT NULL,
                    credential_type TEXT NOT NULL,
                    encrypted_data TEXT NOT NULL,
                    scopes TEXT,
                    created_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
                    expires_at TIMESTAMP,
                    last_used TIMESTAMP,
                    last_refreshed TIMESTAMP
                )
                """)

            # Preferences table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
                )
                """)

            # FTS5 for insights search
            # Standalone FTS5 table — manually synced in store/delete operations.
            # Column weights in bm25: content(10), triggers(1), domain(1), category(1)
            # This ensures content matches rank higher than trigger-only matches.
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS insights_fts USING fts5(
                    content, triggers, domain, category
                )
                """)

            self.conn.commit()

    # ------------------------------------------------------------------
    # Insights
    # ------------------------------------------------------------------

    def store_insight(
        self,
        category: str,
        content: str,
        domain: Optional[str] = None,
        triggers: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
        confidence: float = 0.5,
    ) -> str:
        """Store a new insight with deduplication.

        Before inserting, searches for existing insights with >80% word overlap
        in the same category. If found, updates the existing row instead.

        Args:
            category: Type of insight (fact, strategy, event, error_fix, skill, tool, agent).
            content: Human-readable description.
            domain: Optional domain (e.g., "social_media", "linkedin.com").
            triggers: Optional trigger keywords for recall.
            metadata: Optional structured data (workflow steps, tool params, etc.).
            confidence: Initial confidence score (default 0.5).

        Returns:
            The insight ID (existing ID if deduped, new UUID if created).
        """
        triggers_json = json.dumps(triggers) if triggers else None
        metadata_json = json.dumps(metadata) if metadata else None

        with self.lock:
            # Check for dedup: find existing insights with similar content in same category
            existing_id = self._find_similar_locked(content, category)

            if existing_id:
                # Update existing insight instead of creating duplicate.
                # Keep the LONGER content to avoid data loss (BUG 3 fix).
                now = datetime.now().isoformat()
                self.conn.execute(
                    """
                    UPDATE insights SET
                        content = CASE
                            WHEN length(?) > length(content) THEN ?
                            ELSE content
                        END,
                        confidence = MAX(confidence, ?),
                        triggers = COALESCE(?, triggers),
                        metadata = COALESCE(?, metadata),
                        domain = COALESCE(?, domain),
                        last_used = ?
                    WHERE id = ?
                    """,
                    (
                        content,
                        content,
                        confidence,
                        triggers_json,
                        metadata_json,
                        domain,
                        now,
                        existing_id,
                    ),
                )
                # Re-read the actual stored content (may be old or new depending
                # on which was longer) for FTS index consistency.
                actual_row = self.conn.execute(
                    "SELECT content, triggers, domain, category FROM insights WHERE id = ?",
                    (existing_id,),
                ).fetchone()
                actual_content = actual_row[0]
                actual_triggers = actual_row[1]
                actual_domain = actual_row[2]
                actual_category = actual_row[3]

                # Update FTS5 index — delete old entry and insert new
                self.conn.execute(
                    "DELETE FROM insights_fts WHERE rowid = (SELECT rowid FROM insights WHERE id = ?)",
                    (existing_id,),
                )
                self._insert_fts_locked(
                    existing_id,
                    actual_content,
                    actual_triggers,
                    actual_domain,
                    actual_category,
                )
                self.conn.commit()
                logger.info(
                    "[KnowledgeDB] insight deduped id=%s category=%s",
                    existing_id,
                    category,
                )
                return existing_id

            # No dedup match — create new insight
            insight_id = str(uuid4())
            now = datetime.now().isoformat()
            self.conn.execute(
                """
                INSERT INTO insights (id, category, domain, content, confidence,
                                      triggers, metadata, created_at, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    insight_id,
                    category,
                    domain,
                    content,
                    confidence,
                    triggers_json,
                    metadata_json,
                    now,
                    now,
                ),
            )
            self._insert_fts_locked(
                insight_id, content, triggers_json, domain, category
            )
            self.conn.commit()

        logger.info(
            "[KnowledgeDB] insight stored id=%s category=%s domain=%s",
            insight_id,
            category,
            domain,
        )
        return insight_id

    def _insert_fts_locked(
        self,
        insight_id: str,
        content: str,
        triggers_json: Optional[str],
        domain: Optional[str],
        category: str,
    ):
        """Insert a row into the FTS5 index. Must be called with self.lock held."""
        # Get the rowid for this insight
        cursor = self.conn.execute(
            "SELECT rowid FROM insights WHERE id = ?", (insight_id,)
        )
        row = cursor.fetchone()
        if row:
            self.conn.execute(
                """
                INSERT INTO insights_fts (rowid, content, triggers, domain, category)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row[0], content, triggers_json or "", domain or "", category),
            )

    def _find_similar_locked(self, content: str, category: str) -> Optional[str]:
        """Find an existing insight with >80% word overlap in the same category.

        Must be called with self.lock held.

        Returns the insight ID if a similar one exists, None otherwise.
        """
        # Use OR query to find candidates (broader net for dedup check)
        safe_query = _sanitize_fts5_query(content, use_and=False)
        if not safe_query:
            return None

        try:
            cursor = self.conn.execute(
                """
                SELECT i.id, i.content
                FROM insights i
                JOIN insights_fts f ON i.rowid = f.rowid
                WHERE insights_fts MATCH ? AND i.category = ?
                ORDER BY rank
                LIMIT 10
                """,
                (safe_query, category),
            )
            for row in cursor.fetchall():
                existing_id, existing_content = row[0], row[1]
                overlap = _word_overlap(content, existing_content)
                if overlap >= 0.8:
                    logger.debug(
                        "[KnowledgeDB] dedup match: overlap=%.2f existing_id=%s",
                        overlap,
                        existing_id,
                    )
                    return existing_id
        except sqlite3.OperationalError as e:
            logger.debug("[KnowledgeDB] FTS5 dedup search error: %s", e)

        return None

    def recall(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict]:
        """Search insights using FTS5 full-text search.

        Uses AND semantics by default, falls back to OR on zero results.
        Results are ranked by bm25 with content column weighted higher.
        On recall, applies confidence decay for stale insights (30+ days)
        and bumps confidence for recently-accessed insights.

        Args:
            query: Search terms.
            category: Optional category filter (e.g., "skill", "fact").
            top_k: Maximum results to return.

        Returns:
            List of dicts with keys: id, category, domain, content, confidence,
            triggers, metadata, use_count.
        """
        safe_query = _sanitize_fts5_query(query, use_and=True)
        if safe_query is None:
            logger.debug("[KnowledgeDB] recall skipped, empty/invalid query")
            return []

        with self.lock:
            results = self._fts5_recall_locked(safe_query, category, top_k)
            if not results:
                # Fallback to OR semantics
                safe_query_or = _sanitize_fts5_query(query, use_and=False)
                if safe_query_or and safe_query_or != safe_query:
                    results = self._fts5_recall_locked(safe_query_or, category, top_k)

            # Apply confidence decay/bump and update last_used for each result.
            # Commit once after the loop (not per-insight) for performance.
            now = datetime.now()
            for r in results:
                self._update_confidence_on_recall_locked(r, now)
            if results:
                self.conn.commit()

        logger.debug(
            "[KnowledgeDB] recall query=%r category=%r results=%d",
            query,
            category,
            len(results),
        )
        return results

    def _fts5_recall_locked(
        self,
        fts_query: str,
        category: Optional[str],
        top_k: int,
    ) -> List[Dict]:
        """Execute FTS5 recall query. Must be called with self.lock held."""
        try:
            if category:
                cursor = self.conn.execute(
                    """
                    SELECT i.id, i.category, i.domain, i.content, i.confidence,
                           i.triggers, i.metadata, i.use_count, i.last_used,
                           i.success_count, i.failure_count
                    FROM insights i
                    JOIN insights_fts f ON i.rowid = f.rowid
                    WHERE insights_fts MATCH ? AND i.category = ?
                    ORDER BY bm25(insights_fts, 10.0, 1.0, 1.0, 1.0), i.confidence DESC
                    LIMIT ?
                    """,
                    (fts_query, category, top_k),
                )
            else:
                cursor = self.conn.execute(
                    """
                    SELECT i.id, i.category, i.domain, i.content, i.confidence,
                           i.triggers, i.metadata, i.use_count, i.last_used,
                           i.success_count, i.failure_count
                    FROM insights i
                    JOIN insights_fts f ON i.rowid = f.rowid
                    WHERE insights_fts MATCH ?
                    ORDER BY bm25(insights_fts, 10.0, 1.0, 1.0, 1.0), i.confidence DESC
                    LIMIT ?
                    """,
                    (fts_query, top_k),
                )

            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "id": row[0],
                        "category": row[1],
                        "domain": row[2],
                        "content": row[3],
                        "confidence": row[4],
                        "triggers": json.loads(row[5]) if row[5] else None,
                        "metadata": json.loads(row[6]) if row[6] else None,
                        "use_count": row[7],
                        "last_used": row[8],
                        "success_count": row[9],
                        "failure_count": row[10],
                    }
                )
            return results
        except sqlite3.OperationalError as e:
            logger.debug("[KnowledgeDB] FTS5 recall error: %s", e)
            return []

    def _update_confidence_on_recall_locked(self, result: Dict, now: datetime):
        """Update confidence and last_used for a recalled insight.

        Must be called with self.lock held.

        - If last_used is 30+ days ago: decay confidence by 0.9
        - If recently accessed: bump confidence slightly (+0.02)
        """
        insight_id = result["id"]
        old_confidence = result["confidence"]
        last_used_str = result.get("last_used")

        # Determine if stale
        is_stale = False
        if last_used_str:
            try:
                last_used = datetime.fromisoformat(last_used_str)
                if (now - last_used) > timedelta(days=30):
                    is_stale = True
            except (ValueError, TypeError):
                pass

        # Calculate new confidence
        if is_stale:
            new_confidence = old_confidence * 0.9
        else:
            new_confidence = min(old_confidence + 0.02, 1.0)

        # Update the database — only confidence + last_used.
        # use_count is managed exclusively by record_usage().
        # Note: caller (recall) commits once after all updates — no commit here.
        self.conn.execute(
            """
            UPDATE insights SET
                confidence = ?,
                last_used = ?
            WHERE id = ?
            """,
            (new_confidence, now.isoformat(), insight_id),
        )

        # Update the result dict in-place so the caller sees updated values
        result["confidence"] = new_confidence
        result["last_used"] = now.isoformat()

    # ------------------------------------------------------------------
    # Usage Tracking
    # ------------------------------------------------------------------

    def record_usage(self, insight_id: str, success: bool = True):
        """Record usage of an insight and update confidence.

        Args:
            insight_id: The insight to record usage for.
            success: Whether the usage was successful.
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT success_count, failure_count, use_count, confidence FROM insights WHERE id = ?",
                (insight_id,),
            )
            row = cursor.fetchone()
            if not row:
                logger.warning(
                    "[KnowledgeDB] record_usage: insight %s not found",
                    insight_id,
                )
                return

            success_count, failure_count, use_count, confidence = (
                row[0],
                row[1],
                row[2],
                row[3],
            )

            if success:
                success_count += 1
                confidence = min(confidence + 0.1, 1.0)
            else:
                failure_count += 1
                confidence = max(confidence - 0.1, 0.0)

            use_count += 1

            self.conn.execute(
                """
                UPDATE insights SET
                    success_count = ?,
                    failure_count = ?,
                    use_count = ?,
                    confidence = ?,
                    last_used = strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')
                WHERE id = ?
                """,
                (success_count, failure_count, use_count, confidence, insight_id),
            )
            self.conn.commit()

        logger.debug(
            "[KnowledgeDB] usage recorded id=%s success=%s confidence=%.2f",
            insight_id,
            success,
            confidence,
        )

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    def store_preference(self, key: str, value: str):
        """Store a user preference (upsert)."""
        with self.lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO preferences (key, value, updated_at)
                VALUES (?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
                """,
                (key, value),
            )
            self.conn.commit()
        logger.info("[KnowledgeDB] preference stored key=%s", key)

    def get_preference(self, key: str) -> Optional[str]:
        """Get a user preference."""
        with self.lock:
            cursor = self.conn.execute(
                "SELECT value FROM preferences WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
        value = row[0] if row else None
        logger.debug("[KnowledgeDB] preference key=%s found=%s", key, value is not None)
        return value

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def store_credential(
        self,
        credential_id: str,
        service: str,
        credential_type: str,
        encrypted_data: str,
        scopes: Optional[List[str]] = None,
        expires_at: Optional[str] = None,
    ):
        """Store an encrypted credential.

        Args:
            credential_id: Unique ID (e.g., "cred_gmail_oauth").
            service: Service name (e.g., "gmail", "twitter").
            credential_type: Type (e.g., "oauth2", "api_key", "bearer_token").
            encrypted_data: Encrypted JSON string.
            scopes: Optional list of permission scopes.
            expires_at: Optional expiry timestamp (ISO format). None = no expiry.
        """
        scopes_json = json.dumps(scopes) if scopes else None
        with self.lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO credentials
                    (id, service, credential_type, encrypted_data, scopes, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    credential_id,
                    service,
                    credential_type,
                    encrypted_data,
                    scopes_json,
                    expires_at,
                ),
            )
            self.conn.commit()
        logger.info(
            "[KnowledgeDB] credential stored id=%s service=%s",
            credential_id,
            service,
        )

    def get_credential(self, service: str) -> Optional[Dict]:
        """Get a credential by service name.

        Returns a dict with an added 'expired' boolean field indicating
        whether the credential has passed its expires_at date.
        """
        with self.lock:
            cursor = self.conn.execute(
                """
                SELECT id, service, credential_type, encrypted_data,
                       scopes, created_at, expires_at, last_used, last_refreshed
                FROM credentials
                WHERE service = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (service,),
            )
            row = cursor.fetchone()

        if not row:
            return None

        # Check expiry
        expired = False
        expires_at = row[6]
        if expires_at:
            try:
                expires_dt = datetime.fromisoformat(expires_at)
                if expires_dt < datetime.now():
                    expired = True
            except (ValueError, TypeError):
                pass

        return {
            "id": row[0],
            "service": row[1],
            "credential_type": row[2],
            "encrypted_data": row[3],
            "scopes": json.loads(row[4]) if row[4] else None,
            "created_at": row[5],
            "expires_at": row[6],
            "last_used": row[7],
            "last_refreshed": row[8],
            "expired": expired,
        }

    def update_credential(
        self,
        credential_id: str,
        encrypted_data: Optional[str] = None,
        expires_at: Optional[str] = None,
    ):
        """Update a credential (e.g., refresh token).

        Updates last_refreshed timestamp. Only updates fields that are provided.
        Does nothing if neither encrypted_data nor expires_at is provided.
        """
        if encrypted_data is None and expires_at is None:
            logger.debug(
                "[KnowledgeDB] update_credential called with no fields to update id=%s",
                credential_id,
            )
            return

        with self.lock:
            now = datetime.now().isoformat()
            if encrypted_data is not None and expires_at is not None:
                self.conn.execute(
                    """
                    UPDATE credentials SET
                        encrypted_data = ?,
                        expires_at = ?,
                        last_refreshed = ?
                    WHERE id = ?
                    """,
                    (encrypted_data, expires_at, now, credential_id),
                )
            elif encrypted_data is not None:
                self.conn.execute(
                    """
                    UPDATE credentials SET
                        encrypted_data = ?,
                        last_refreshed = ?
                    WHERE id = ?
                    """,
                    (encrypted_data, now, credential_id),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE credentials SET
                        expires_at = ?,
                        last_refreshed = ?
                    WHERE id = ?
                    """,
                    (expires_at, now, credential_id),
                )
            self.conn.commit()
        logger.info("[KnowledgeDB] credential updated id=%s", credential_id)

    def close(self):
        """Close the database connection."""
        try:
            self.conn.close()
        except Exception:
            pass


# ============================================================================
# SharedAgentState: Thread-Safe Singleton
# ============================================================================


class SharedAgentState:
    """
    Thread-safe singleton holding MemoryDB + KnowledgeDB.

    Creates exactly 2 DB files at the workspace directory:
    - memory.db: Session-scoped working memory
    - knowledge.db: Cross-session persistent storage

    Every agent in the system shares THE SAME instance.
    No LogsDB, no MasterPlan, no AgentCallStack.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern — only one SharedAgentState per process."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    logger.info("[SharedState] creating singleton instance")
        else:
            logger.debug("[SharedState] returning existing singleton")
        return cls._instance

    def __init__(self, workspace_dir: Optional[Path] = None):
        """Initialize SharedAgentState (only runs once due to singleton).

        Args:
            workspace_dir: Directory for database files.
                          Defaults to ~/.gaia/workspace/
        """
        # Double-checked locking: fast path without lock, then locked check.
        # This prevents two threads from both passing the hasattr check
        # and initializing concurrently (BUG 2 fix).
        if hasattr(self, "_initialized"):
            return

        with self.__class__._lock:
            if hasattr(self, "_initialized"):
                return

            # Set up workspace directory
            if workspace_dir is None:
                workspace_dir = Path.home() / ".gaia" / "workspace"
            workspace_dir = Path(workspace_dir)
            workspace_dir.mkdir(parents=True, exist_ok=True)
            self.workspace_dir = workspace_dir

            # Initialize exactly 2 databases
            self.memory = MemoryDB(workspace_dir / "memory.db")
            self.knowledge = KnowledgeDB(workspace_dir / "knowledge.db")

            # Mark as initialized — must be LAST inside the lock
            self._initialized = True
            logger.info("[SharedState] initialized workspace=%s", workspace_dir)

    def reset_session(self):
        """Reset working memory for a new session while keeping all persistent knowledge.

        Clears:
        - active_state (agent's working memory notes)
        - file_cache (cached file contents)
        - tool_results (tool call history)

        Keeps (persistent across sessions):
        - knowledge.db (insights, preferences, credentials)
        - conversation_history in memory.db
        """
        self.memory.clear_working_memory()
        logger.info(
            "[SharedState] session reset — working memory cleared, knowledge retained"
        )


def get_shared_state(workspace_dir: Optional[Path] = None) -> SharedAgentState:
    """Get the singleton SharedAgentState instance.

    This ensures all agents share the same state.

    Args:
        workspace_dir: Optional workspace directory. Only used on first call.

    Returns:
        The singleton SharedAgentState instance.
    """
    return SharedAgentState(workspace_dir)
