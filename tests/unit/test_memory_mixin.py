# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for MemoryMixin.

Tests:
- Tool registration (all 8 tools present in registry)
- Auto-extraction: conversation turn storage
- Auto-extraction: heuristic fact extraction
- Auto-extraction: preference extraction
- Auto-extraction: deduplication
- Session context building
- Memory session reset
- Keyword extraction helper
"""

import json

import pytest

# We need to reset the singleton and tool registry between tests
from gaia.agents.base.shared_state import SharedAgentState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_singleton():
    """Reset the SharedAgentState singleton between tests."""
    # Reset before test
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")
    yield
    # Reset after test
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory for DB files."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


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
def memory_mixin(temp_workspace):
    """Create a MemoryMixin instance with initialized memory."""
    from gaia.agents.base.memory_mixin import MemoryMixin

    class TestHost(MemoryMixin):
        """Minimal host class to test the mixin in isolation."""

        pass

    host = TestHost()
    host.init_memory(workspace_dir=temp_workspace)
    return host


@pytest.fixture
def memory_mixin_with_tools(memory_mixin):
    """MemoryMixin instance with tools registered."""
    memory_mixin.register_memory_tools()
    return memory_mixin


# ---------------------------------------------------------------------------
# Test: init_memory()
# ---------------------------------------------------------------------------


class TestInitMemory:
    """Tests for MemoryMixin.init_memory()."""

    def test_init_memory_creates_shared_state(self, temp_workspace):
        """init_memory() creates a SharedAgentState with MemoryDB + KnowledgeDB."""
        from gaia.agents.base.memory_mixin import MemoryMixin

        class Host(MemoryMixin):
            pass

        host = Host()
        host.init_memory(workspace_dir=temp_workspace)

        assert hasattr(host, "_shared_state")
        assert host._shared_state is not None
        assert hasattr(host._shared_state, "memory")
        assert hasattr(host._shared_state, "knowledge")

    def test_init_memory_creates_session_id(self, memory_mixin):
        """init_memory() generates a unique session ID."""
        assert memory_mixin.memory_session_id is not None
        assert len(memory_mixin.memory_session_id) == 36  # UUID format

    def test_memory_property_access(self, memory_mixin):
        """Memory and knowledge properties work after init."""
        from gaia.agents.base.shared_state import KnowledgeDB, MemoryDB

        assert isinstance(memory_mixin.memory, MemoryDB)
        assert isinstance(memory_mixin.knowledge, KnowledgeDB)

    def test_memory_property_raises_without_init(self):
        """Accessing .memory without init_memory() raises RuntimeError."""
        from gaia.agents.base.memory_mixin import MemoryMixin

        class Host(MemoryMixin):
            pass

        host = Host()
        with pytest.raises(RuntimeError, match="Call init_memory"):
            _ = host.memory

    def test_knowledge_property_raises_without_init(self):
        """Accessing .knowledge without init_memory() raises RuntimeError."""
        from gaia.agents.base.memory_mixin import MemoryMixin

        class Host(MemoryMixin):
            pass

        host = Host()
        with pytest.raises(RuntimeError, match="Call init_memory"):
            _ = host.knowledge

    def test_init_memory_creates_db_files(self, temp_workspace):
        """init_memory() creates memory.db and knowledge.db files."""
        from gaia.agents.base.memory_mixin import MemoryMixin

        class Host(MemoryMixin):
            pass

        host = Host()
        host.init_memory(workspace_dir=temp_workspace)

        # Access the databases to ensure they're created
        _ = host.memory
        _ = host.knowledge

        assert (temp_workspace / "memory.db").exists()
        assert (temp_workspace / "knowledge.db").exists()


# ---------------------------------------------------------------------------
# Test: register_memory_tools()
# ---------------------------------------------------------------------------


class TestRegisterMemoryTools:
    """Tests for MemoryMixin.register_memory_tools()."""

    def test_registers_all_8_tools(self, memory_mixin_with_tools):
        """register_memory_tools() registers all 8 expected tools."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        expected_tools = [
            "remember",
            "recall_memory",
            "forget_memory",
            "store_insight",
            "recall",
            "store_preference",
            "get_preference",
            "search_conversations",
        ]

        for tool_name in expected_tools:
            assert tool_name in _TOOL_REGISTRY, (
                f"Tool '{tool_name}' not found in registry. "
                f"Available: {list(_TOOL_REGISTRY.keys())}"
            )

    def test_tool_descriptions_not_empty(self, memory_mixin_with_tools):
        """All registered tools have non-empty descriptions."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        for name in [
            "remember",
            "recall_memory",
            "forget_memory",
            "store_insight",
            "recall",
            "store_preference",
            "get_preference",
            "search_conversations",
        ]:
            info = _TOOL_REGISTRY[name]
            assert info["description"].strip(), f"Tool '{name}' has empty description"

    def test_remember_tool_stores(self, memory_mixin_with_tools):
        """The remember tool stores a value and it can be retrieved."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["remember"]["function"]
        result = func(key="test_key", value="test_value", tags="tag1,tag2")
        assert result["status"] == "stored"
        assert result["key"] == "test_key"

        # Verify it's in the DB
        stored = memory_mixin_with_tools.memory.get_memory("test_key")
        assert stored == "test_value"

    def test_recall_memory_tool_finds(self, memory_mixin_with_tools):
        """The recall_memory tool finds stored memories."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Store something first
        memory_mixin_with_tools.memory.store_memory(
            "project_name", "GAIA Framework", tags=["project"]
        )

        func = _TOOL_REGISTRY["recall_memory"]["function"]
        result = func(key="project_name")
        assert result["status"] == "found"
        assert result["results"][0]["value"] == "GAIA Framework"

    def test_recall_memory_tool_search(self, memory_mixin_with_tools):
        """The recall_memory tool searches via FTS5."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        memory_mixin_with_tools.memory.store_memory(
            "auth", "JWT tokens with RS256 signing"
        )

        func = _TOOL_REGISTRY["recall_memory"]["function"]
        result = func(query="JWT tokens")
        assert result["status"] == "found"
        assert result["count"] >= 1

    def test_recall_memory_tool_not_found(self, memory_mixin_with_tools):
        """recall_memory with unknown key returns not_found."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["recall_memory"]["function"]
        result = func(key="nonexistent_key")
        assert result["status"] == "not_found"

    def test_forget_memory_tool(self, memory_mixin_with_tools):
        """The forget_memory tool removes entries."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        memory_mixin_with_tools.memory.store_memory("temp_key", "temp_val")
        func = _TOOL_REGISTRY["forget_memory"]["function"]

        result = func(key="temp_key")
        assert result["status"] == "removed"

        # Verify it's gone
        assert memory_mixin_with_tools.memory.get_memory("temp_key") is None

    def test_store_insight_tool(self, memory_mixin_with_tools):
        """The store_insight tool stores a persistent insight."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["store_insight"]["function"]
        result = func(
            category="fact",
            content="GAIA supports AMD NPU acceleration for local inference",
            domain="technology",
            triggers="NPU,AMD,acceleration",
        )
        assert result["status"] == "stored"
        assert "insight_id" in result

    def test_store_insight_tool_invalid_category(self, memory_mixin_with_tools):
        """store_insight with invalid category returns error."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["store_insight"]["function"]
        result = func(category="invalid", content="test content")
        assert result["status"] == "error"
        assert "Invalid category" in result["message"]

    def test_store_insight_tool_with_metadata(self, memory_mixin_with_tools):
        """store_insight stores metadata JSON correctly."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["store_insight"]["function"]
        metadata = json.dumps({"steps": ["draft", "review", "post"]})
        result = func(
            category="skill",
            content="LinkedIn post workflow",
            metadata=metadata,
        )
        assert result["status"] == "stored"

        # Verify metadata is stored
        insights = memory_mixin_with_tools.knowledge.recall("LinkedIn post workflow")
        assert len(insights) >= 1
        assert insights[0]["metadata"] is not None
        assert "steps" in insights[0]["metadata"]

    def test_store_insight_tool_invalid_metadata(self, memory_mixin_with_tools):
        """store_insight with invalid JSON metadata returns error."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["store_insight"]["function"]
        result = func(
            category="fact",
            content="test",
            metadata="not valid json {",
        )
        assert result["status"] == "error"
        assert "Invalid JSON" in result["message"]

    def test_recall_tool(self, memory_mixin_with_tools):
        """The recall tool searches the knowledge base."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Store an insight first
        memory_mixin_with_tools.knowledge.store_insight(
            category="fact",
            content="The target audience is AI developers using AMD hardware",
        )

        func = _TOOL_REGISTRY["recall"]["function"]
        result = func(query="AI developers AMD")
        assert result["status"] == "found"
        assert result["count"] >= 1

    def test_recall_tool_with_category_filter(self, memory_mixin_with_tools):
        """The recall tool filters by category."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Store insights in different categories
        memory_mixin_with_tools.knowledge.store_insight(
            category="fact", content="Python is the primary language"
        )
        memory_mixin_with_tools.knowledge.store_insight(
            category="strategy", content="Python code review before merge"
        )

        func = _TOOL_REGISTRY["recall"]["function"]
        result = func(query="Python", category="fact")
        assert result["status"] == "found"
        # All results should be in "fact" category
        for r in result["results"]:
            assert r["category"] == "fact"

    def test_store_preference_tool(self, memory_mixin_with_tools):
        """The store_preference tool stores a preference."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["store_preference"]["function"]
        result = func(key="tone", value="professional but friendly")
        assert result["status"] == "stored"
        assert result["key"] == "tone"

    def test_get_preference_tool(self, memory_mixin_with_tools):
        """The get_preference tool retrieves a stored preference."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Store a preference first
        memory_mixin_with_tools.knowledge.store_preference("timezone", "PST")

        func = _TOOL_REGISTRY["get_preference"]["function"]
        result = func(key="timezone")
        assert result["status"] == "found"
        assert result["value"] == "PST"

    def test_get_preference_tool_not_found(self, memory_mixin_with_tools):
        """get_preference with unknown key returns not_found."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["get_preference"]["function"]
        result = func(key="nonexistent_pref")
        assert result["status"] == "not_found"

    def test_search_conversations_tool(self, memory_mixin_with_tools):
        """The search_conversations tool searches past conversation history."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Store some conversation turns
        memory_mixin_with_tools.memory.store_conversation_turn(
            "sess1", "user", "How do I deploy to AMD NPU?"
        )
        memory_mixin_with_tools.memory.store_conversation_turn(
            "sess1", "assistant", "To deploy to AMD NPU, use the Lemonade server."
        )

        func = _TOOL_REGISTRY["search_conversations"]["function"]
        result = func(query="AMD NPU deploy")
        assert result["status"] == "found"
        assert result["count"] >= 1


# ---------------------------------------------------------------------------
# Test: _auto_extract_after_query()
# ---------------------------------------------------------------------------


class TestAutoExtract:
    """Tests for automatic extraction after queries."""

    def test_auto_extract_stores_conversation(self, memory_mixin):
        """After _auto_extract_after_query(), conversation turns are stored in MemoryDB."""
        stats = memory_mixin._auto_extract_after_query(
            user_input="How do I set up GAIA?",
            assistant_response="To set up GAIA, first install the dependencies...",
        )

        assert stats["conversation_turns"] == 2

        # Verify turns are in the database
        history = memory_mixin.memory.get_conversation_history(
            session_id=memory_mixin.memory_session_id
        )
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert "set up GAIA" in history[0]["content"]
        assert history[1]["role"] == "assistant"
        assert "install" in history[1]["content"]

    def test_auto_extract_stores_audience_fact(self, memory_mixin):
        """When user says 'our audience is AI developers', a fact is auto-stored."""
        stats = memory_mixin._auto_extract_after_query(
            user_input="Our audience is AI developers who use AMD hardware for local inference.",
            assistant_response="Great, I'll tailor the content for AI developers using AMD hardware.",
        )

        assert stats["facts_extracted"] >= 1

        # Verify the fact is in KnowledgeDB
        facts = memory_mixin.knowledge.recall("audience AI developers", category="fact")
        assert len(facts) >= 1

    def test_auto_extract_stores_product_fact(self, memory_mixin):
        """When user mentions their product name, it's auto-stored."""
        stats = memory_mixin._auto_extract_after_query(
            user_input="Our product is called GAIA Framework for local AI deployment.",
            assistant_response="I understand, GAIA Framework is your product for local AI deployment.",
        )

        assert stats["facts_extracted"] >= 1

    def test_auto_extract_stores_technology_fact(self, memory_mixin):
        """When user mentions technology they use, it's auto-stored."""
        stats = memory_mixin._auto_extract_after_query(
            user_input="We use Python and FastAPI for our backend services.",
            assistant_response="I see you're using Python and FastAPI for the backend.",
        )

        assert stats["facts_extracted"] >= 1

    def test_auto_extract_stores_preference(self, memory_mixin):
        """When user states a preference, it's auto-stored."""
        stats = memory_mixin._auto_extract_after_query(
            user_input="I prefer concise responses with code examples over verbose explanations.",
            assistant_response="Understood, I'll keep responses concise with code examples.",
        )

        assert stats["preferences_extracted"] >= 1

    def test_auto_extract_no_false_positives_short(self, memory_mixin):
        """Short/trivial messages don't produce false positive extractions."""
        stats = memory_mixin._auto_extract_after_query(
            user_input="Hello",
            assistant_response="Hi! How can I help you today?",
        )

        assert stats["facts_extracted"] == 0
        assert stats["preferences_extracted"] == 0
        # Short assistant response shouldn't produce strategies
        assert stats["strategies_extracted"] == 0

    def test_auto_extract_dedup(self, memory_mixin):
        """Running auto-extract on similar conversations doesn't create duplicates."""
        # First conversation
        memory_mixin._auto_extract_after_query(
            user_input="Our audience is AI developers who build locally.",
            assistant_response="Got it, targeting AI developers.",
        )

        # Second similar conversation
        memory_mixin._auto_extract_after_query(
            user_input="Our audience is AI developers who build on local hardware.",
            assistant_response="Understood, AI developers using local hardware.",
        )

        # Should be deduped by KnowledgeDB's built-in dedup (>80% word overlap)
        facts = memory_mixin.knowledge.recall("audience AI developers", category="fact")
        # There should be at most 1 fact (deduped), not 2
        assert len(facts) <= 1

    def test_auto_extract_without_init_returns_error(self):
        """_auto_extract_after_query() before init_memory() returns error dict."""
        from gaia.agents.base.memory_mixin import MemoryMixin

        class Host(MemoryMixin):
            pass

        host = Host()
        result = host._auto_extract_after_query("test", "test")
        assert "error" in result

    def test_auto_extract_disabled(self, memory_mixin):
        """When auto_extract is disabled, only conversation turns are stored."""
        memory_mixin._auto_extract_enabled = False

        stats = memory_mixin._auto_extract_after_query(
            user_input="Our audience is AI developers who build locally.",
            assistant_response="Got it!",
        )

        assert stats["conversation_turns"] == 2
        assert stats["facts_extracted"] == 0
        assert stats["preferences_extracted"] == 0

    def test_auto_extract_strategies_from_long_response(self, memory_mixin):
        """Decision patterns in assistant responses are extracted as strategies."""
        stats = memory_mixin._auto_extract_after_query(
            user_input="What approach should we take for the API?",
            assistant_response=(
                "Based on the requirements, I'll implement a RESTful API using FastAPI "
                "with JWT authentication and rate limiting. This approach provides good "
                "performance and is well-suited for the AMD NPU inference endpoints. "
                "Let's start with the authentication middleware first."
            ),
        )

        # The response is > 100 chars and contains "I'll" + decision
        assert stats["strategies_extracted"] >= 1


# ---------------------------------------------------------------------------
# Test: Session Management
# ---------------------------------------------------------------------------


class TestSessionManagement:
    """Tests for session reset and context building."""

    def test_reset_memory_session(self, memory_mixin):
        """reset_memory_session() clears working memory and generates new session ID."""
        old_session_id = memory_mixin.memory_session_id

        # Store some working memory
        memory_mixin.memory.store_memory("temp_key", "temp_value")

        # Reset
        memory_mixin.reset_memory_session()

        new_session_id = memory_mixin.memory_session_id
        assert new_session_id != old_session_id

        # Working memory should be cleared
        assert memory_mixin.memory.get_memory("temp_key") is None

    def test_knowledge_survives_session_reset(self, memory_mixin):
        """Knowledge persists across session resets."""
        # Store knowledge
        memory_mixin.knowledge.store_insight(
            category="fact",
            content="GAIA runs on AMD hardware with NPU support",
        )

        # Reset session
        memory_mixin.reset_memory_session()

        # Knowledge should still be there
        results = memory_mixin.knowledge.recall("GAIA AMD NPU")
        assert len(results) >= 1

    def test_get_session_context_empty(self, memory_mixin):
        """get_session_context() returns empty string when nothing stored."""
        context = memory_mixin.get_session_context()
        assert context == ""

    def test_get_session_context_with_preferences(self, memory_mixin):
        """get_session_context() includes stored preferences."""
        memory_mixin.knowledge.store_preference("tone", "professional")
        memory_mixin.knowledge.store_preference("timezone", "PST")

        context = memory_mixin.get_session_context()
        assert "User preferences" in context
        assert "tone" in context
        assert "professional" in context

    def test_get_session_context_with_facts(self, memory_mixin):
        """get_session_context() includes high-confidence facts."""
        memory_mixin.knowledge.store_insight(
            category="fact",
            content="The user prefers Python over JavaScript",
            confidence=0.8,
        )

        context = memory_mixin.get_session_context()
        assert "Remembered context" in context
        assert "Python" in context


# ---------------------------------------------------------------------------
# Test: _extract_keywords() helper
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    """Tests for the keyword extraction helper."""

    def test_extracts_meaningful_words(self):
        """Extracts meaningful keywords, skipping stop words."""
        from gaia.agents.base.memory_mixin import _extract_keywords

        keywords = _extract_keywords("The audience is AI developers using AMD hardware")
        assert "audience" in keywords
        assert "developers" in keywords
        assert "the" not in keywords
        assert "is" not in keywords

    def test_respects_max_keywords(self):
        """Limits keywords to max_keywords."""
        from gaia.agents.base.memory_mixin import _extract_keywords

        keywords = _extract_keywords(
            "Python FastAPI JWT authentication rate limiting AMD NPU inference",
            max_keywords=3,
        )
        assert len(keywords) <= 3

    def test_deduplicates_keywords(self):
        """Keywords are unique (no duplicates)."""
        from gaia.agents.base.memory_mixin import _extract_keywords

        keywords = _extract_keywords("AMD AMD AMD hardware hardware")
        assert keywords.count("amd") == 1
        assert keywords.count("hardware") == 1

    def test_handles_empty_input(self):
        """Empty input returns empty list."""
        from gaia.agents.base.memory_mixin import _extract_keywords

        assert _extract_keywords("") == []
        assert _extract_keywords("   ") == []

    def test_handles_only_stop_words(self):
        """Input with only stop words returns empty list."""
        from gaia.agents.base.memory_mixin import _extract_keywords

        keywords = _extract_keywords("the is are was a an")
        assert keywords == []

    def test_strips_punctuation(self):
        """Punctuation is stripped from keywords."""
        from gaia.agents.base.memory_mixin import _extract_keywords

        keywords = _extract_keywords("Hello, world! This is great.")
        assert "hello" in keywords
        assert "world" in keywords
        assert "great" in keywords
        # No punctuation in keywords
        for kw in keywords:
            assert "," not in kw
            assert "!" not in kw
            assert "." not in kw


# ---------------------------------------------------------------------------
# Test: Integration-style scenarios
# ---------------------------------------------------------------------------


class TestMemoryMixinIntegration:
    """Integration-style tests simulating real usage patterns."""

    def test_full_conversation_cycle(self, memory_mixin_with_tools):
        """Simulate a full conversation with auto-extraction and manual recall."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # 1. User provides context, auto-extract captures it
        memory_mixin_with_tools._auto_extract_after_query(
            user_input="Our target audience is machine learning engineers at enterprise companies.",
            assistant_response="I understand your target audience is ML engineers in enterprise settings.",
        )

        # 2. Agent can manually store a working memory note
        remember_fn = _TOOL_REGISTRY["remember"]["function"]
        remember_fn(
            key="meeting_topic", value="Q2 content planning", tags="meeting,planning"
        )

        # 3. Later, agent can recall both auto-extracted and manually stored
        recall_fn = _TOOL_REGISTRY["recall"]["function"]
        result = recall_fn(query="machine learning engineers")
        assert result["status"] == "found"

        recall_mem_fn = _TOOL_REGISTRY["recall_memory"]["function"]
        result = recall_mem_fn(key="meeting_topic")
        assert result["status"] == "found"
        assert result["results"][0]["value"] == "Q2 content planning"

    def test_preferences_persist_through_tools(self, memory_mixin_with_tools):
        """Preferences stored via tool can be retrieved via tool."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        store_fn = _TOOL_REGISTRY["store_preference"]["function"]
        get_fn = _TOOL_REGISTRY["get_preference"]["function"]

        store_fn(key="response_length", value="concise with examples")
        result = get_fn(key="response_length")
        assert result["status"] == "found"
        assert result["value"] == "concise with examples"

    def test_conversation_search_across_sessions(self, memory_mixin_with_tools):
        """Conversation search finds results across multiple sessions."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Simulate two sessions
        memory_mixin_with_tools._auto_extract_after_query(
            "How do I optimize for AMD NPU?",
            "Use the Lemonade server with quantized models for NPU acceleration.",
        )

        # Switch to a different session
        memory_mixin_with_tools._memory_session_id = "session-2"

        memory_mixin_with_tools._auto_extract_after_query(
            "What about GPU performance?",
            "For GPU optimization, use ROCm with PyTorch for best AMD GPU performance.",
        )

        # Search should find across sessions
        search_fn = _TOOL_REGISTRY["search_conversations"]["function"]
        result = search_fn(query="AMD NPU")
        assert result["status"] == "found"
        assert result["count"] >= 1

    def test_goal_extraction(self, memory_mixin):
        """User goal statements are extracted as facts."""
        stats = memory_mixin._auto_extract_after_query(
            user_input="Our goal is to make AI accessible to developers on consumer hardware.",
            assistant_response="That's a great mission. Let me help you achieve that with GAIA.",
        )

        assert stats["facts_extracted"] >= 1

    def test_multiple_facts_in_one_message(self, memory_mixin):
        """Multiple patterns in one message extract multiple facts."""
        stats = memory_mixin._auto_extract_after_query(
            user_input=(
                "Our product is called GAIA. "
                "We use Python and FastAPI for the backend. "
                "Our target audience is AMD hardware users."
            ),
            assistant_response="I see you're building GAIA with Python/FastAPI for AMD users.",
        )

        # Should extract at least 2 facts (product + technology + audience)
        assert stats["facts_extracted"] >= 2
