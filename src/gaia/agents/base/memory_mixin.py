# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MemoryMixin: Persistent memory for any GAIA agent.

Provides:
- init_memory(): Initialize memory subsystem (MemoryDB + KnowledgeDB)
- register_memory_tools(): Register all memory tools with the agent
- .memory / .knowledge properties: Access databases directly
- _auto_extract_after_query(): Hook for automatic fact extraction

Usage:
    class MyAgent(Agent, MemoryMixin):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.init_memory()

        def _register_tools(self):
            self.register_memory_tools()

Auto-Store:
    After each process_query(), call _auto_extract_after_query() to:
    1. Store conversation turns in MemoryDB
    2. Extract facts/preferences via heuristic pattern matching (no LLM call)
    3. Deduplicate against existing knowledge
"""

import logging
import re
from typing import Any, Dict, List
from uuid import uuid4

logger = logging.getLogger(__name__)


# ============================================================================
# Heuristic Fact Extraction Patterns
# ============================================================================

# Patterns that indicate the user is stating a fact about themselves/their work.
# Each tuple: (compiled regex, category, domain_hint)
# These run on user input to auto-extract knowledge.
_USER_FACT_PATTERNS = [
    # Audience / target market
    (
        re.compile(
            r"(?:our|my|the)\s+(?:target\s+)?audience\s+(?:is|are)\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "fact",
        None,
    ),
    # Product / project identity
    (
        re.compile(
            r"(?:our|my)\s+(?:product|project|app|tool|company|startup)\s+(?:is\s+called|is|name\s+is)\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "fact",
        "product",
    ),
    # Technology stack / tools
    (
        re.compile(
            r"(?:we|I)\s+(?:use|prefer|work with|build with)\s+(.+?)(?:\s+for\s+|\.|,|$)",
            re.IGNORECASE,
        ),
        "fact",
        "technology",
    ),
    # Goals / objectives
    (
        re.compile(
            r"(?:our|my)\s+(?:goal|objective|aim|mission)\s+(?:is|are)\s+(?:to\s+)?(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "fact",
        None,
    ),
    # Name / role
    (
        re.compile(
            r"(?:my\s+name\s+is|I(?:'m| am)\s+(?:a|an|the)?\s*)\s*(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "fact",
        "identity",
    ),
]

# Patterns that indicate a preference or decision.
# These run on the combined conversation (user + assistant).
_PREFERENCE_PATTERNS = [
    # Explicit preferences
    (
        re.compile(
            r"(?:I\s+)?prefer\s+(.+?)(?:\s+(?:over|instead|rather)\s+|\.|,|$)",
            re.IGNORECASE,
        ),
        "fact",  # Stored as fact with domain="preference" for consistency
    ),
    # Style / tone preferences
    (
        re.compile(
            r"(?:use|keep|make\s+it)\s+(?:a\s+)?(.+?)\s+(?:tone|style|voice|format)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "fact",
    ),
    # "always" / "never" rules
    (
        re.compile(
            r"(?:always|never)\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "fact",
    ),
]

# Patterns on assistant responses indicating a decision was made.
_DECISION_PATTERNS = [
    (
        re.compile(
            r"(?:I(?:'ll| will)|Let(?:'s| us))\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "strategy",
    ),
]


class MemoryMixin:
    """
    Mixin that gives any Agent persistent memory across sessions.

    Provides:
    - Working memory (session-scoped): remember(), recall_memory(), forget_memory()
    - Knowledge (cross-session): store_insight(), recall(), store_preference(), get_preference()
    - Conversation search: search_conversations()
    - Auto-extraction: _auto_extract_after_query() for heuristic fact capture

    Requires the host class to have a _TOOL_REGISTRY-compatible tool system
    (i.e., be an Agent subclass or use the @tool decorator from tools.py).
    """

    def init_memory(self, workspace_dir=None):
        """Initialize the memory subsystem.

        Creates/gets the SharedAgentState singleton which provides
        MemoryDB (session-scoped) and KnowledgeDB (persistent).

        Args:
            workspace_dir: Optional workspace directory for DB files.
                          Defaults to ~/.gaia/workspace/
        """
        from gaia.agents.base.shared_state import get_shared_state

        self._shared_state = get_shared_state(workspace_dir)
        self._memory_session_id = str(uuid4())
        self._auto_extract_enabled = True
        logger.info("[MemoryMixin] initialized, session_id=%s", self._memory_session_id)

    @property
    def memory(self):
        """Access the session-scoped MemoryDB."""
        if not hasattr(self, "_shared_state"):
            raise RuntimeError("MemoryMixin not initialized. Call init_memory() first.")
        return self._shared_state.memory

    @property
    def knowledge(self):
        """Access the persistent KnowledgeDB."""
        if not hasattr(self, "_shared_state"):
            raise RuntimeError("MemoryMixin not initialized. Call init_memory() first.")
        return self._shared_state.knowledge

    @property
    def memory_session_id(self) -> str:
        """Get the current memory session ID."""
        if not hasattr(self, "_memory_session_id"):
            self._memory_session_id = str(uuid4())
        return self._memory_session_id

    # ------------------------------------------------------------------
    # Tool Registration
    # ------------------------------------------------------------------

    def register_memory_tools(self) -> None:
        """Register all memory tools with the agent's tool registry.

        Call this from _register_tools() in your agent subclass.
        Tools registered:
        - remember: Store a fact in working memory
        - recall_memory: Search working memory
        - forget_memory: Remove a working memory entry
        - store_insight: Store a persistent insight in KnowledgeDB
        - recall: Search persistent knowledge
        - store_preference: Store a user preference
        - get_preference: Retrieve a user preference
        - search_conversations: Search past conversation history
        """
        from gaia.agents.base.tools import tool

        @tool(
            name="remember",
            description=(
                "Store a key fact or context value in working memory (session-scoped). "
                "Use this to track important context across tool calls.\n"
                "Examples:\n"
                '  remember(key="current_project", value="~/Work/gaia")\n'
                '  remember(key="auth_approach", value="JWT with RS256", tags="architecture,security")\n'
                '  remember(key="user_timezone", value="PST")'
            ),
            parameters={
                "key": {
                    "type": "str",
                    "description": "Unique key for the memory entry",
                    "required": True,
                },
                "value": {
                    "type": "str",
                    "description": "The value to store",
                    "required": True,
                },
                "tags": {
                    "type": "str",
                    "description": "Comma-separated tags for categorization (optional)",
                    "required": False,
                },
            },
        )
        def remember(key: str, value: str, tags: str = "") -> Dict[str, Any]:
            """Store a fact in working memory."""
            tag_list = (
                [t.strip() for t in tags.split(",") if t.strip()] if tags else None
            )
            self.memory.store_memory(key, value, tags=tag_list)
            return {
                "status": "stored",
                "key": key,
                "message": f"Remembered: {key} = {value}",
            }

        @tool(
            name="recall_memory",
            description=(
                "Search working memory for relevant facts and context. "
                "Uses full-text search with AND semantics (falls back to OR).\n"
                "Examples:\n"
                '  recall_memory(query="authentication approach")\n'
                '  recall_memory(key="current_project")\n'
                '  recall_memory(query="user preferences", limit=5)'
            ),
            parameters={
                "query": {
                    "type": "str",
                    "description": "Search terms to find relevant memories",
                    "required": False,
                },
                "key": {
                    "type": "str",
                    "description": "Exact key to look up (optional, for direct access)",
                    "required": False,
                },
                "limit": {
                    "type": "int",
                    "description": "Maximum results to return (default: 10)",
                    "required": False,
                },
            },
        )
        def recall_memory(
            query: str = "", key: str = "", limit: int = 10
        ) -> Dict[str, Any]:
            """Search working memory for facts and context."""
            # Direct key lookup
            if key:
                value = self.memory.get_memory(key)
                if value is not None:
                    return {
                        "status": "found",
                        "results": [{"key": key, "value": value}],
                    }
                return {"status": "not_found", "message": f"No memory with key '{key}'"}

            # FTS5 search
            results = self.memory.recall_memories(query=query or None, limit=limit)
            return {
                "status": "found" if results else "empty",
                "count": len(results),
                "results": results,
            }

        @tool(
            name="forget_memory",
            description=(
                "Remove a specific working memory entry by key.\n"
                "Example: forget_memory(key='old_project_path')"
            ),
            parameters={
                "key": {
                    "type": "str",
                    "description": "Key of the memory to remove",
                    "required": True,
                },
            },
        )
        def forget_memory(key: str) -> Dict[str, Any]:
            """Remove a working memory entry."""
            removed = self.memory.forget_memory(key)
            if removed:
                return {"status": "removed", "key": key}
            return {"status": "not_found", "key": key}

        @tool(
            name="store_insight",
            description=(
                "Store a persistent insight in the knowledge base (survives across sessions). "
                "Use for important learnings, patterns, facts about the user/project.\n"
                "Categories: fact, strategy, event, error_fix, skill, tool, agent\n"
                "Examples:\n"
                '  store_insight(category="fact", content="User prefers Python over JS")\n'
                '  store_insight(category="error_fix", content="ImportError for torch: install with pip install torch --index-url ...", '
                'domain="python", triggers="ImportError,torch,pytorch")\n'
                '  store_insight(category="strategy", content="Always run linting before commits", domain="development")\n'
                '  store_insight(category="skill", content="LinkedIn post workflow", '
                'metadata=\'{"steps": ["draft", "review", "post"]}\', triggers="linkedin,social")'
            ),
            parameters={
                "category": {
                    "type": "str",
                    "description": "Insight type: fact, strategy, event, error_fix, skill, tool, agent",
                    "required": True,
                },
                "content": {
                    "type": "str",
                    "description": "The insight content (human-readable description)",
                    "required": True,
                },
                "domain": {
                    "type": "str",
                    "description": "Domain/context (e.g., 'python', 'social_media', 'development')",
                    "required": False,
                },
                "triggers": {
                    "type": "str",
                    "description": "Comma-separated trigger keywords for recall",
                    "required": False,
                },
                "metadata": {
                    "type": "str",
                    "description": "JSON string with structured data (workflow steps, params, etc.)",
                    "required": False,
                },
            },
        )
        def store_insight(
            category: str,
            content: str,
            domain: str = "",
            triggers: str = "",
            metadata: str = "",
        ) -> Dict[str, Any]:
            """Store a persistent insight in the knowledge base."""
            import json as _json

            valid_categories = [
                "fact",
                "strategy",
                "event",
                "error_fix",
                "skill",
                "tool",
                "agent",
            ]
            if category not in valid_categories:
                return {
                    "status": "error",
                    "message": f"Invalid category '{category}'. Must be one of: {valid_categories}",
                }

            trigger_list = (
                [t.strip() for t in triggers.split(",") if t.strip()]
                if triggers
                else None
            )

            metadata_dict = None
            if metadata:
                try:
                    metadata_dict = _json.loads(metadata)
                except _json.JSONDecodeError:
                    return {
                        "status": "error",
                        "message": f"Invalid JSON in metadata: {metadata}",
                    }

            insight_id = self.knowledge.store_insight(
                category=category,
                content=content,
                domain=domain or None,
                triggers=trigger_list,
                metadata=metadata_dict,
            )

            return {
                "status": "stored",
                "insight_id": insight_id,
                "category": category,
                "message": f"Insight stored: {content[:80]}...",
            }

        @tool(
            name="recall",
            description=(
                "Search the persistent knowledge base for relevant insights. "
                "Uses FTS5 full-text search with relevance ranking.\n"
                "Examples:\n"
                '  recall(query="NPU acceleration")\n'
                '  recall(query="posting schedule", category="strategy")\n'
                '  recall(query="error torch import", category="error_fix", top_k=3)'
            ),
            parameters={
                "query": {
                    "type": "str",
                    "description": "Search terms to find relevant knowledge",
                    "required": True,
                },
                "category": {
                    "type": "str",
                    "description": "Optional category filter (fact, strategy, skill, etc.)",
                    "required": False,
                },
                "top_k": {
                    "type": "int",
                    "description": "Maximum results to return (default: 5)",
                    "required": False,
                },
            },
        )
        def recall(query: str, category: str = "", top_k: int = 5) -> Dict[str, Any]:
            """Search persistent knowledge base."""
            results = self.knowledge.recall(
                query=query,
                category=category or None,
                top_k=top_k,
            )
            return {
                "status": "found" if results else "empty",
                "count": len(results),
                "results": results,
            }

        @tool(
            name="store_preference",
            description=(
                "Store a user preference (persistent key-value pair). "
                "Updates existing preference if key already exists.\n"
                "Examples:\n"
                '  store_preference(key="tone", value="professional but friendly")\n'
                '  store_preference(key="timezone", value="America/Los_Angeles")\n'
                '  store_preference(key="code_style", value="black formatter, 88 char lines")'
            ),
            parameters={
                "key": {
                    "type": "str",
                    "description": "Preference key",
                    "required": True,
                },
                "value": {
                    "type": "str",
                    "description": "Preference value",
                    "required": True,
                },
            },
        )
        def store_preference(key: str, value: str) -> Dict[str, Any]:
            """Store a user preference."""
            self.knowledge.store_preference(key, value)
            return {
                "status": "stored",
                "key": key,
                "message": f"Preference saved: {key} = {value}",
            }

        @tool(
            name="get_preference",
            description=(
                "Retrieve a user preference by key.\n"
                "Example: get_preference(key='tone')"
            ),
            parameters={
                "key": {
                    "type": "str",
                    "description": "Preference key to look up",
                    "required": True,
                },
            },
        )
        def get_preference(key: str) -> Dict[str, Any]:
            """Retrieve a user preference."""
            value = self.knowledge.get_preference(key)
            if value is not None:
                return {"status": "found", "key": key, "value": value}
            return {"status": "not_found", "key": key}

        @tool(
            name="search_conversations",
            description=(
                "Search past conversation history using full-text search. "
                "Finds relevant exchanges from previous sessions.\n"
                "Example: search_conversations(query='deployment strategy', limit=5)"
            ),
            parameters={
                "query": {
                    "type": "str",
                    "description": "Search terms to find in past conversations",
                    "required": True,
                },
                "limit": {
                    "type": "int",
                    "description": "Maximum results to return (default: 10)",
                    "required": False,
                },
            },
        )
        def search_conversations(query: str, limit: int = 10) -> Dict[str, Any]:
            """Search past conversation history."""
            results = self.memory.search_conversations(query, limit=limit)
            return {
                "status": "found" if results else "empty",
                "count": len(results),
                "results": results,
            }

        logger.info("[MemoryMixin] registered 8 memory tools")

    # ------------------------------------------------------------------
    # Auto-Extraction After Query
    # ------------------------------------------------------------------

    def _auto_extract_after_query(
        self, user_input: str, assistant_response: str
    ) -> Dict[str, Any]:
        """Extract and store key facts from the conversation automatically.

        Called after each process_query(). Performs:
        1. Store conversation turns in MemoryDB (always)
        2. Heuristic fact extraction from user input (pattern matching, no LLM)
        3. Heuristic preference extraction from conversation
        4. Deduplication via KnowledgeDB's built-in dedup

        Args:
            user_input: The user's message.
            assistant_response: The agent's response.

        Returns:
            Dict with counts of stored items.
        """
        if not hasattr(self, "_shared_state"):
            logger.warning(
                "[MemoryMixin] _auto_extract called but init_memory() not called"
            )
            return {"error": "Memory not initialized"}

        stats = {
            "conversation_turns": 0,
            "facts_extracted": 0,
            "preferences_extracted": 0,
            "strategies_extracted": 0,
        }

        # 1. Always store conversation turns
        try:
            session_id = self.memory_session_id
            self.memory.store_conversation_turn(session_id, "user", user_input)
            self.memory.store_conversation_turn(
                session_id, "assistant", assistant_response
            )
            stats["conversation_turns"] = 2
            logger.debug(
                "[MemoryMixin] stored 2 conversation turns, session=%s",
                session_id,
            )
        except Exception as e:
            logger.error("[MemoryMixin] failed to store conversation turns: %s", e)

        # 2. Extract facts from user input (heuristic, no LLM)
        if self._auto_extract_enabled:
            stats["facts_extracted"] = self._extract_user_facts(user_input)
            stats["preferences_extracted"] = self._extract_preferences(user_input)
            stats["strategies_extracted"] = self._extract_decisions(assistant_response)

        total = (
            stats["facts_extracted"]
            + stats["preferences_extracted"]
            + stats["strategies_extracted"]
        )
        if total > 0:
            logger.info(
                "[MemoryMixin] auto-extracted %d items (facts=%d, prefs=%d, strategies=%d)",
                total,
                stats["facts_extracted"],
                stats["preferences_extracted"],
                stats["strategies_extracted"],
            )

        return stats

    # ------------------------------------------------------------------
    # Heuristic Extraction Helpers
    # ------------------------------------------------------------------

    def _extract_user_facts(self, user_input: str) -> int:
        """Extract facts from user input using pattern matching.

        Returns the number of facts stored.
        """
        count = 0
        for pattern, category, domain_hint in _USER_FACT_PATTERNS:
            match = pattern.search(user_input)
            if match:
                extracted = match.group(0).strip()
                # Skip very short or very long extractions (likely false positives)
                if len(extracted) < 10 or len(extracted) > 500:
                    continue

                try:
                    self.knowledge.store_insight(
                        category=category,
                        content=extracted,
                        domain=domain_hint,
                        triggers=_extract_keywords(extracted),
                    )
                    count += 1
                    logger.debug("[MemoryMixin] auto-stored fact: %s", extracted[:60])
                except Exception as e:
                    logger.warning(
                        "[MemoryMixin] failed to store extracted fact: %s", e
                    )

        return count

    def _extract_preferences(self, user_input: str) -> int:
        """Extract preference statements from user input.

        Stores as category="fact" with domain="preference" to stay consistent
        with the valid categories list (fact, strategy, event, error_fix,
        skill, tool, agent). The domain field distinguishes preference-facts
        from other facts.

        Returns the number of preferences stored.
        """
        count = 0
        for pattern, pref_type in _PREFERENCE_PATTERNS:
            match = pattern.search(user_input)
            if match:
                extracted = match.group(0).strip()
                if len(extracted) < 8 or len(extracted) > 300:
                    continue

                try:
                    self.knowledge.store_insight(
                        category=pref_type,
                        content=extracted,
                        domain="preference",
                        triggers=_extract_keywords(extracted),
                    )
                    count += 1
                    logger.debug(
                        "[MemoryMixin] auto-stored preference: %s", extracted[:60]
                    )
                except Exception as e:
                    logger.warning("[MemoryMixin] failed to store preference: %s", e)

        return count

    def _extract_decisions(self, assistant_response: str) -> int:
        """Extract decision/strategy statements from assistant responses.

        Only extracts from responses longer than 100 chars to avoid
        storing trivial responses as strategies.

        Returns the number of strategies stored.
        """
        if len(assistant_response) < 100:
            return 0

        count = 0
        for pattern, category in _DECISION_PATTERNS:
            matches = pattern.findall(assistant_response)
            for match_text in matches[:2]:  # Cap at 2 per pattern to avoid noise
                extracted = match_text.strip()
                if len(extracted) < 15 or len(extracted) > 300:
                    continue

                try:
                    self.knowledge.store_insight(
                        category=category,
                        content=extracted,
                        triggers=_extract_keywords(extracted),
                    )
                    count += 1
                    logger.debug(
                        "[MemoryMixin] auto-stored strategy: %s", extracted[:60]
                    )
                except Exception as e:
                    logger.warning("[MemoryMixin] failed to store strategy: %s", e)

        return count

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    def reset_memory_session(self):
        """Start a fresh memory session.

        Clears working memory but preserves all persistent knowledge.
        Generates a new session ID.
        """
        if hasattr(self, "_shared_state"):
            self._shared_state.reset_session()
            self._memory_session_id = str(uuid4())
            logger.info(
                "[MemoryMixin] session reset, new session_id=%s",
                self._memory_session_id,
            )

    def get_session_context(self, max_preferences: int = 5) -> str:
        """Build a curated context summary for the start of a session.

        Returns a string suitable for injection into the system prompt.
        Only includes recent preferences and high-confidence insights —
        does NOT dump everything (avoids context pollution).

        Args:
            max_preferences: Max number of preferences to include.

        Returns:
            Formatted context string, or empty string if nothing relevant.
        """
        if not hasattr(self, "_shared_state"):
            return ""

        sections = []

        # Get recent preferences (acquire lock for thread safety)
        try:
            with self.knowledge.lock:
                cursor = self.knowledge.conn.execute(
                    "SELECT key, value FROM preferences ORDER BY updated_at DESC LIMIT ?",
                    (max_preferences,),
                )
                prefs = cursor.fetchall()
            if prefs:
                pref_lines = [f"  - {k}: {v}" for k, v in prefs]
                sections.append("User preferences:\n" + "\n".join(pref_lines))
        except Exception as e:
            logger.debug("[MemoryMixin] failed to get preferences for context: %s", e)

        # Get top high-confidence facts (acquire lock for thread safety)
        try:
            with self.knowledge.lock:
                cursor = self.knowledge.conn.execute(
                    """
                    SELECT content, category FROM insights
                    WHERE category = 'fact'
                      AND confidence >= 0.5
                    ORDER BY confidence DESC, last_used DESC
                    LIMIT 5
                    """,
                )
                insights = cursor.fetchall()
            if insights:
                insight_lines = [f"  - [{cat}] {content}" for content, cat in insights]
                sections.append("Remembered context:\n" + "\n".join(insight_lines))
        except Exception as e:
            logger.debug("[MemoryMixin] failed to get insights for context: %s", e)

        if not sections:
            return ""

        return "\n\n".join(sections)


# ============================================================================
# Module-Level Helpers
# ============================================================================

# Common English stop words — module-level constant to avoid re-creation per call.
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "because",
        "but",
        "and",
        "or",
        "if",
        "while",
        "about",
        "up",
        "it",
        "its",
        "my",
        "our",
        "we",
        "i",
        "me",
        "you",
        "your",
        "he",
        "she",
        "they",
        "them",
        "this",
        "that",
        "these",
        "those",
        "what",
        "which",
        "who",
        "whom",
    }
)


def _extract_keywords(text: str, max_keywords: int = 5) -> List[str]:
    """Extract meaningful keywords from text for trigger-based recall.

    Filters out common stop words and returns the most distinctive terms.

    Args:
        text: Input text to extract keywords from.
        max_keywords: Maximum number of keywords to return.

    Returns:
        List of keyword strings.
    """
    # Extract words, lowercase, filter stops, keep words 3+ chars
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) >= 3]

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return unique[:max_keywords]
