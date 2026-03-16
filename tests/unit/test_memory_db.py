# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for MemoryDB — session-scoped working memory.

Tests FTS5 search, AND/OR semantics, conversation history,
file cache, tool results, and clear operations.
"""

import pytest

from gaia.agents.base.shared_state import MemoryDB


@pytest.fixture
def memory_db(tmp_path):
    """Create a fresh MemoryDB in a temp directory for each test."""
    db = MemoryDB(tmp_path / "memory.db")
    return db


class TestMemoryDBStoreRecall:
    """test_memory_db_store_recall: store_memory() → recall_memories() returns it. Tags filter correctly."""

    def test_store_and_recall_basic(self, memory_db):
        """Store a memory and recall it by query."""
        memory_db.store_memory("current_project", "~/Work/gaia", tags=["project"])
        results = memory_db.recall_memories(query="gaia")
        assert len(results) >= 1
        match = next((r for r in results if r["key"] == "current_project"), None)
        assert match is not None
        assert match["value"] == "~/Work/gaia"

    def test_store_and_recall_by_key(self, memory_db):
        """Store a memory and get it by exact key."""
        memory_db.store_memory("auth_approach", "JWT with RS256", tags=["architecture"])
        value = memory_db.get_memory("auth_approach")
        assert value == "JWT with RS256"

    def test_recall_returns_tags(self, memory_db):
        """Recalled memories include their tags."""
        memory_db.store_memory(
            "db_choice", "PostgreSQL", tags=["database", "architecture"]
        )
        results = memory_db.recall_memories(query="PostgreSQL")
        assert len(results) >= 1
        match = next((r for r in results if r["key"] == "db_choice"), None)
        assert match is not None
        assert "database" in match["tags"]
        assert "architecture" in match["tags"]

    def test_recall_no_query_returns_recent(self, memory_db):
        """Recall without query returns most recently stored entries."""
        memory_db.store_memory("first", "value1")
        memory_db.store_memory("second", "value2")
        memory_db.store_memory("third", "value3")
        results = memory_db.recall_memories(limit=2)
        assert len(results) == 2
        # Most recent should be first
        keys = [r["key"] for r in results]
        assert "third" in keys

    def test_store_replaces_existing_key(self, memory_db):
        """Storing with same key replaces existing value."""
        memory_db.store_memory("target", "old_value")
        memory_db.store_memory("target", "new_value")
        value = memory_db.get_memory("target")
        assert value == "new_value"

    def test_forget_memory(self, memory_db):
        """forget_memory removes the entry."""
        memory_db.store_memory("temp_fact", "temporary")
        assert memory_db.get_memory("temp_fact") == "temporary"
        deleted = memory_db.forget_memory("temp_fact")
        assert deleted is True
        assert memory_db.get_memory("temp_fact") is None

    def test_forget_nonexistent_returns_false(self, memory_db):
        """Forgetting a non-existent key returns False."""
        assert memory_db.forget_memory("nonexistent") is False


class TestMemoryDBFTS5Search:
    """test_memory_db_fts5_search: FTS5 search finds entries by content keyword match (not just LIKE)."""

    def test_fts5_finds_by_value_keyword(self, memory_db):
        """FTS5 finds entries by keyword in value (not just LIKE prefix/suffix match)."""
        memory_db.store_memory(
            "project_info", "GAIA supports NPU acceleration on AMD hardware"
        )
        memory_db.store_memory("other_info", "The weather is sunny today")

        results = memory_db.recall_memories(query="NPU acceleration")
        assert len(results) >= 1
        match = next((r for r in results if r["key"] == "project_info"), None)
        assert match is not None

    def test_fts5_finds_by_key_keyword(self, memory_db):
        """FTS5 searches both key and value fields."""
        memory_db.store_memory("marketing_strategy", "focus on developer audience")

        results = memory_db.recall_memories(query="marketing")
        assert len(results) >= 1
        match = next((r for r in results if r["key"] == "marketing_strategy"), None)
        assert match is not None


class TestMemoryDBFTS5ANDSemantics:
    """test_memory_db_fts5_and_semantics: FTS5 with AND finds entries containing ALL query words."""

    def test_and_semantics_matches_both_words(self, memory_db):
        """Searching 'marketing strategy' finds entries with BOTH words."""
        memory_db.store_memory("plan", "our marketing strategy is content-first")
        memory_db.store_memory("budget", "marketing budget is $5000")
        memory_db.store_memory("approach", "our strategy is agile")

        results = memory_db.recall_memories(query="marketing strategy")

        # Should find the entry with both "marketing" AND "strategy"
        keys = [r["key"] for r in results]
        assert "plan" in keys

        # With AND semantics, entries with only one word should NOT appear
        # (unless OR fallback is triggered, which shouldn't happen here since AND returned results)
        assert "budget" not in keys
        assert "approach" not in keys


class TestMemoryDBFTS5ORFallback:
    """test_memory_db_fts5_or_fallback: When AND returns zero results, falls back to OR."""

    def test_or_fallback_on_zero_and_results(self, memory_db):
        """When no entries match ALL words, fall back to OR to return partial matches."""
        memory_db.store_memory("info1", "marketing is important for growth")
        memory_db.store_memory("info2", "quantum computing is the future")

        # "marketing quantum" — no entry has BOTH words, so AND returns 0
        # OR fallback should return entries with either word
        results = memory_db.recall_memories(query="marketing quantum")
        assert len(results) >= 1
        keys = [r["key"] for r in results]
        # At least one of the partial matches should appear
        assert "info1" in keys or "info2" in keys


class TestMemoryDBClearWorking:
    """test_memory_db_clear_working: clear_working_memory() removes active_state, file_cache, tool_results."""

    def test_clear_removes_working_memory(self, memory_db):
        """clear_working_memory removes active_state, file_cache, tool_results."""
        memory_db.store_memory("fact", "important")
        memory_db.cache_file("/tmp/test.py", "print('hello')")
        memory_db.store_tool_result("read_file", {"path": "/tmp"}, "content")

        memory_db.clear_working_memory()

        assert memory_db.get_memory("fact") is None
        assert memory_db.get_file("/tmp/test.py") is None
        # Tool results table should be empty
        results = memory_db.recall_memories()
        assert len(results) == 0

    def test_clear_retains_conversation_history(self, memory_db):
        """clear_working_memory does NOT remove conversation_history."""
        memory_db.store_conversation_turn("session1", "user", "Hello agent")
        memory_db.store_conversation_turn(
            "session1", "assistant", "Hello! How can I help?"
        )

        memory_db.clear_working_memory()

        history = memory_db.get_conversation_history("session1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"


class TestMemoryDBConversationHistory:
    """Additional tests for conversation history storage and FTS5 search."""

    def test_store_and_retrieve_conversation(self, memory_db):
        """Store conversation turns and retrieve them."""
        memory_db.store_conversation_turn("s1", "user", "Tell me about GAIA")
        memory_db.store_conversation_turn(
            "s1", "assistant", "GAIA is AMD's AI framework"
        )

        history = memory_db.get_conversation_history("s1")
        assert len(history) == 2
        assert history[0]["content"] == "Tell me about GAIA"
        assert history[1]["content"] == "GAIA is AMD's AI framework"

    def test_search_conversations_fts5(self, memory_db):
        """search_conversations uses FTS5 to find past discussions."""
        memory_db.store_conversation_turn(
            "s1", "user", "How do I use NPU acceleration?"
        )
        memory_db.store_conversation_turn(
            "s1", "assistant", "You can enable NPU through Lemonade Server"
        )
        memory_db.store_conversation_turn("s2", "user", "What is the weather today?")

        results = memory_db.search_conversations("NPU acceleration")
        assert len(results) >= 1
        # Should find the NPU-related conversation, not the weather one
        contents = [r["content"] for r in results]
        assert any("NPU" in c for c in contents)

    def test_conversation_history_limit(self, memory_db):
        """Conversation history respects limit parameter."""
        for i in range(10):
            memory_db.store_conversation_turn("s1", "user", f"Message {i}")
        history = memory_db.get_conversation_history("s1", limit=3)
        assert len(history) == 3

    def test_conversation_history_limit_returns_most_recent(self, memory_db):
        """BUG 6 regression: limit returns the MOST RECENT N turns, not oldest.

        If a session has 10 turns and limit=3, we should get turns 7, 8, 9
        (most recent), not turns 0, 1, 2 (oldest).
        """
        for i in range(10):
            memory_db.store_conversation_turn("s1", "user", f"Message {i}")
        history = memory_db.get_conversation_history("s1", limit=3)
        assert len(history) == 3
        # Should be the 3 most recent messages, in chronological order
        assert history[0]["content"] == "Message 7"
        assert history[1]["content"] == "Message 8"
        assert history[2]["content"] == "Message 9"

    def test_conversation_history_no_session_returns_most_recent(self, memory_db):
        """The no-session path should also return most recent turns."""
        for i in range(10):
            memory_db.store_conversation_turn("s1", "user", f"Message {i}")
        history = memory_db.get_conversation_history(limit=3)
        assert len(history) == 3
        assert history[0]["content"] == "Message 7"
        assert history[1]["content"] == "Message 8"
        assert history[2]["content"] == "Message 9"


class TestMemoryDBFileCache:
    """Tests for file cache operations."""

    def test_cache_and_retrieve_file(self, memory_db):
        """Cache a file and retrieve it."""
        memory_db.cache_file("/home/user/test.py", "print('hello world')")
        content = memory_db.get_file("/home/user/test.py")
        assert content == "print('hello world')"

    def test_cache_miss_returns_none(self, memory_db):
        """Cache miss returns None."""
        assert memory_db.get_file("/nonexistent/file.py") is None

    def test_cache_overwrites_existing(self, memory_db):
        """Caching same path overwrites existing content."""
        memory_db.cache_file("/test.py", "version1")
        memory_db.cache_file("/test.py", "version2")
        assert memory_db.get_file("/test.py") == "version2"


class TestMemoryDBToolResults:
    """Tests for tool result storage."""

    def test_store_tool_result(self, memory_db):
        """Store and verify tool results exist (retrieved via get_tool_results)."""
        memory_db.store_tool_result(
            "read_file", {"path": "/tmp/test.py"}, "file contents here"
        )
        results = memory_db.get_tool_results(limit=5)
        assert len(results) >= 1
        assert results[0]["tool_name"] == "read_file"
        assert results[0]["result"] == "file contents here"
