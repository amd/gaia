# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end integration tests for MemoryMixin.

Tests:
- Memory and knowledge persistence across sessions (via workspace DB files)
- Heuristic auto-extraction of user facts and preferences
- Knowledge deduplication (>80% word overlap)
- FTS5 query sanitization with special characters
"""

import pytest

from gaia.agents.base.memory_mixin import (
    MemoryMixin,
    _PREFERENCE_PATTERNS,
    _USER_FACT_PATTERNS,
)
from gaia.agents.base.shared_state import SharedAgentState


# -- Fixtures ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_singleton():
    """Reset the SharedAgentState singleton between tests."""
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")
    yield
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")


@pytest.fixture(autouse=True)
def clean_tool_registry():
    """Clear tool registry before each test to avoid cross-test pollution."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def workspace(tmp_path):
    """Create a persistent workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _make_host(workspace):
    """Create a fresh MemoryMixin host pointing at the given workspace."""
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")

    from gaia.agents.base.tools import _TOOL_REGISTRY

    _TOOL_REGISTRY.clear()

    class _Host(MemoryMixin):
        pass

    host = _Host()
    host.init_memory(workspace_dir=workspace)
    return host


# -- TestMemoryMixinPersistence ------------------------------------------------


class TestMemoryMixinPersistence:
    """Verify that memory and knowledge persist across independent sessions."""

    def test_memory_persists_across_sessions(self, workspace):
        """Working memory stored in session 1 is accessible from the same DB in session 2."""
        # Session 1: store a memory entry
        host1 = _make_host(workspace)
        host1.memory.store_memory("project_dir", "/home/user/gaia")
        host1._shared_state.memory.close()
        host1._shared_state.knowledge.close()

        # Session 2: recall the memory from the same workspace
        host2 = _make_host(workspace)
        value = host2.memory.get_memory("project_dir")
        assert value == "/home/user/gaia"

        host2._shared_state.memory.close()
        host2._shared_state.knowledge.close()

    def test_knowledge_persists_across_sessions(self, workspace):
        """Knowledge insight stored in session 1 is available in session 2."""
        # Session 1: store an insight
        host1 = _make_host(workspace)
        host1.knowledge.store_insight(
            category="fact",
            content="AMD Ryzen AI 300 series includes NPU support for local inference",
            domain="hardware",
        )
        host1._shared_state.memory.close()
        host1._shared_state.knowledge.close()

        # Session 2: recall the insight
        host2 = _make_host(workspace)
        results = host2.knowledge.recall(query="Ryzen NPU inference")
        assert len(results) >= 1
        assert "NPU" in results[0]["content"]

        host2._shared_state.memory.close()
        host2._shared_state.knowledge.close()


# -- TestAutoExtraction --------------------------------------------------------


class TestAutoExtraction:
    """Test heuristic fact and preference extraction from user input."""

    def test_extract_user_facts(self, workspace):
        """_extract_user_facts stores facts matching known patterns."""
        host = _make_host(workspace)

        # This sentence matches the technology stack pattern:
        # "(?:we|I) (?:use|prefer|work with|build with) (.+?)(?:\\s+for\\s+|\\.|,|$)"
        count = host._extract_user_facts(
            "We use Python and FastAPI for our backend services."
        )
        assert count >= 1, "Should extract at least one fact from technology pattern"

        # Verify the fact was stored in knowledge DB
        results = host.knowledge.recall(query="Python FastAPI backend")
        assert len(results) >= 1

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_extract_preferences(self, workspace):
        """_extract_preferences stores preference statements."""
        host = _make_host(workspace)

        # This matches the "prefer X over Y" pattern
        count = host._extract_preferences(
            "I prefer Python over Java for data processing."
        )
        assert count >= 1, "Should extract at least one preference"

        # Verify the preference was stored
        results = host.knowledge.recall(query="prefer Python Java")
        assert len(results) >= 1

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_auto_extract_after_query_stores_conversation(self, workspace):
        """_auto_extract_after_query always stores conversation turns."""
        host = _make_host(workspace)

        stats = host._auto_extract_after_query(
            user_input="Tell me about AMD hardware",
            assistant_response="AMD produces CPUs and GPUs for various workloads.",
        )
        assert stats["conversation_turns"] == 2

        # Verify conversation is searchable
        results = host.memory.search_conversations("AMD hardware")
        assert len(results) >= 1

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_fact_pattern_coverage(self):
        """Verify regex patterns match expected input strings."""
        # Technology pattern
        tech_pattern = _USER_FACT_PATTERNS[2][0]  # "(?:we|I) use/prefer/work with..."
        match = tech_pattern.search("I use TensorFlow for model training")
        assert match is not None, "Technology pattern should match 'I use TensorFlow...'"

        # Product pattern
        product_pattern = _USER_FACT_PATTERNS[1][0]  # "our product is called..."
        match = product_pattern.search("Our product is called GAIA Agent")
        assert match is not None, "Product pattern should match 'Our product is called...'"

        # Preference pattern
        pref_pattern = _PREFERENCE_PATTERNS[0][0]  # "prefer X over Y"
        match = pref_pattern.search("I prefer dark mode over light mode")
        assert match is not None, "Preference pattern should match 'I prefer...'"


# -- TestKnowledgeDedup --------------------------------------------------------


class TestKnowledgeDedup:
    """Test knowledge deduplication based on word overlap."""

    def test_duplicate_insight_not_stored(self, workspace):
        """Insights with >80% word overlap in same category are deduplicated."""
        host = _make_host(workspace)

        # Store first insight
        id1 = host.knowledge.store_insight(
            category="fact",
            content="GAIA framework supports AMD Ryzen AI NPU acceleration",
            domain="technology",
        )

        # Store nearly identical insight (>80% word overlap)
        id2 = host.knowledge.store_insight(
            category="fact",
            content="GAIA framework supports AMD Ryzen AI NPU acceleration features",
            domain="technology",
        )

        # Dedup should return the same ID (updated the existing insight)
        assert id1 == id2, (
            "Insights with >80% word overlap should be deduplicated (same ID returned)"
        )

        # Verify only one insight exists for this query
        results = host.knowledge.recall(query="GAIA AMD Ryzen NPU")
        assert len(results) == 1

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_different_insights_both_stored(self, workspace):
        """Genuinely different insights are stored separately."""
        host = _make_host(workspace)

        id1 = host.knowledge.store_insight(
            category="fact",
            content="Python is a dynamically typed programming language with garbage collection",
            domain="programming",
        )

        id2 = host.knowledge.store_insight(
            category="fact",
            content="AMD XDNA architecture provides dedicated AI acceleration via neural processing units",
            domain="hardware",
        )

        # Different content should produce different IDs
        assert id1 != id2, "Distinct insights should get different IDs"

        # Both should be retrievable
        py_results = host.knowledge.recall(query="Python dynamically typed")
        assert len(py_results) >= 1

        amd_results = host.knowledge.recall(query="XDNA neural processing")
        assert len(amd_results) >= 1

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()


# -- TestFTSSanitization -------------------------------------------------------


class TestFTSSanitization:
    """Test that FTS5 queries with special characters are handled safely."""

    def test_recall_with_special_characters(self, workspace):
        """Recall with special chars in query does not crash."""
        host = _make_host(workspace)

        # Store a simple insight first
        host.knowledge.store_insight(
            category="fact",
            content="Unit testing is important for code quality",
            domain="development",
        )

        # Queries with FTS5 special chars should not raise
        for query in [
            "test & query",
            "test (parens)",
            'test "quoted"',
            "test * wildcard",
            "test OR query",
            "test AND query",
            "test: colon",
            "test + plus - minus",
        ]:
            results = host.knowledge.recall(query=query)
            # Should return a list (possibly empty), not raise
            assert isinstance(results, list), (
                f"recall with query {query!r} should return a list"
            )

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_recall_empty_query(self, workspace):
        """Recall with empty string returns empty list, not an error."""
        host = _make_host(workspace)

        results = host.knowledge.recall(query="")
        assert isinstance(results, list)
        assert len(results) == 0

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_recall_whitespace_only_query(self, workspace):
        """Recall with whitespace-only string returns empty list."""
        host = _make_host(workspace)

        results = host.knowledge.recall(query="   ")
        assert isinstance(results, list)
        assert len(results) == 0

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_search_conversations_with_special_chars(self, workspace):
        """Conversation search with special characters does not crash."""
        host = _make_host(workspace)

        # Store a conversation turn
        host.memory.store_conversation_turn("sid-1", "user", "How do I fix this error?")

        # Search with special chars
        results = host.memory.search_conversations("fix & error (test)")
        assert isinstance(results, list)

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()
