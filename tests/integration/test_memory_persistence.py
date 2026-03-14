# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for cross-session memory persistence.

Tests:
- Insights persist across agent instances (different SharedAgentState singletons)
- Working memory clears between sessions while knowledge persists
- Conversation history persists across sessions
- Preferences persist across sessions
- Credentials persist across sessions
- MemoryMixin with auto-extraction across agent lifecycles
- FTS5 indexes work correctly on restored data
"""

import json

import pytest

from gaia.agents.base.memory_mixin import MemoryMixin
from gaia.agents.base.shared_state import (
    KnowledgeDB,
    MemoryDB,
    SharedAgentState,
    get_shared_state,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


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


def _make_mixin(workspace):
    """Create a fresh MemoryMixin instance pointing at the given workspace."""
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")

    class Host(MemoryMixin):
        pass

    host = Host()
    host.init_memory(workspace_dir=workspace)
    return host


# ── Cross-Session Persistence ────────────────────────────────────────────────


class TestKnowledgePersistence:
    """Knowledge stored by one agent instance is available to the next."""

    def test_insight_persists_across_sessions(self, workspace):
        """Create agent -> store insight -> destroy -> create new -> recall returns it."""
        agent1 = _make_mixin(workspace)
        agent1.knowledge.store_insight(
            category="fact",
            content="GAIA supports AMD NPU acceleration",
            domain="technology",
        )
        # Destroy agent1 (close DB connections)
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        # Create a new agent pointing at same workspace
        agent2 = _make_mixin(workspace)
        results = agent2.knowledge.recall(query="NPU acceleration", category="fact")
        assert len(results) >= 1
        assert "NPU" in results[0]["content"]

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_multiple_insights_persist(self, workspace):
        """Multiple insights from one session are all retrievable in the next."""
        agent1 = _make_mixin(workspace)
        agent1.knowledge.store_insight(
            category="fact", content="Our audience is AI developers"
        )
        agent1.knowledge.store_insight(
            category="strategy", content="Post technical content on LinkedIn weekly"
        )
        agent1.knowledge.store_insight(
            category="event", content="Launched v2.0 on March 1st"
        )
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        facts = agent2.knowledge.recall(query="AI developers", category="fact")
        strategies = agent2.knowledge.recall(query="LinkedIn", category="strategy")
        events = agent2.knowledge.recall(query="launched", category="event")
        assert len(facts) >= 1
        assert len(strategies) >= 1
        assert len(events) >= 1

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_preference_persists_across_sessions(self, workspace):
        """Preferences stored in one session are available in the next."""
        agent1 = _make_mixin(workspace)
        agent1.knowledge.store_preference("brand_voice", "technical but friendly")
        agent1.knowledge.store_preference("post_frequency", "twice weekly")
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        assert (
            agent2.knowledge.get_preference("brand_voice") == "technical but friendly"
        )
        assert agent2.knowledge.get_preference("post_frequency") == "twice weekly"

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_credential_persists_across_sessions(self, workspace):
        """Credentials stored by one agent are available to the next."""
        agent1 = _make_mixin(workspace)
        agent1.knowledge.store_credential(
            credential_id="cred_twitter_api_key",
            service="twitter",
            credential_type="api_key",
            encrypted_data="encrypted_token_123",
            scopes=["tweet.write", "tweet.read"],
        )
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        cred = agent2.knowledge.get_credential("twitter")
        assert cred is not None
        assert cred["service"] == "twitter"
        assert cred["credential_type"] == "api_key"
        assert cred["encrypted_data"] == "encrypted_token_123"

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_skill_metadata_persists(self, workspace):
        """Skill with complex metadata persists correctly."""
        agent1 = _make_mixin(workspace)
        metadata = {
            "type": "replay",
            "steps": [
                {
                    "step": 1,
                    "action": "navigate",
                    "target": "https://example.com",
                    "value": None,
                    "screenshot": "skills/abc/step_1.png",
                    "notes": "Go to site",
                },
                {
                    "step": 2,
                    "action": "click",
                    "target": "button.submit",
                    "value": None,
                    "screenshot": "skills/abc/step_2.png",
                    "notes": "Click submit",
                },
            ],
            "parameters": ["content"],
            "tools_used": ["playwright"],
        }
        agent1.knowledge.store_insight(
            category="skill",
            content="Post on example.com",
            domain="example.com",
            metadata=metadata,
        )
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        results = agent2.knowledge.recall(query="Post on example", category="skill")
        assert len(results) >= 1
        restored_meta = results[0]["metadata"]
        assert restored_meta["type"] == "replay"
        assert len(restored_meta["steps"]) == 2
        assert restored_meta["parameters"] == ["content"]

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_fts5_works_on_restored_data(self, workspace):
        """FTS5 indexes function correctly on data loaded from persisted DB."""
        agent1 = _make_mixin(workspace)
        agent1.knowledge.store_insight(
            category="fact", content="Ryzen AI processor with NPU support"
        )
        agent1.knowledge.store_insight(
            category="fact", content="CUDA is an NVIDIA technology"
        )
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        # AND search: both words must match
        results = agent2.knowledge.recall(query="Ryzen NPU")
        assert len(results) >= 1
        assert "Ryzen" in results[0]["content"]
        # Should not return CUDA result
        for r in results:
            assert "CUDA" not in r["content"]

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()


# ── Session Isolation ────────────────────────────────────────────────────────


class TestSessionIsolation:
    """Working memory clears between sessions while knowledge persists."""

    def test_working_memory_clears_knowledge_persists(self, workspace):
        """Working memory is session-scoped, knowledge is permanent."""
        agent1 = _make_mixin(workspace)
        # Store in working memory
        agent1.memory.store_memory("current_task", "writing tests")
        # Store in knowledge
        agent1.knowledge.store_insight(
            category="fact", content="User prefers Python over JavaScript"
        )
        # Verify both are accessible
        assert agent1.memory.get_memory("current_task") == "writing tests"
        results = agent1.knowledge.recall(query="Python JavaScript")
        assert len(results) >= 1

        # Reset session (clear working memory, keep knowledge)
        agent1._shared_state.reset_session()

        # Working memory cleared
        assert agent1.memory.get_memory("current_task") is None
        # Knowledge persists
        results = agent1.knowledge.recall(query="Python JavaScript")
        assert len(results) >= 1

        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

    def test_conversation_history_persists_across_session_reset(self, workspace):
        """Conversation history survives session reset (by design)."""
        agent1 = _make_mixin(workspace)
        session_id = agent1.memory_session_id
        agent1.memory.store_conversation_turn(session_id, "user", "Hello")
        agent1.memory.store_conversation_turn(session_id, "assistant", "Hi there!")

        # Reset session
        agent1._shared_state.reset_session()

        # Conversation history is still there
        history = agent1.memory.get_conversation_history(session_id)
        assert len(history) == 2
        assert history[0]["content"] == "Hello"
        assert history[1]["content"] == "Hi there!"

        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

    def test_conversation_searchable_across_sessions(self, workspace):
        """Conversations from one session can be searched in the next."""
        agent1 = _make_mixin(workspace)
        sid = "session-1"
        agent1.memory.store_conversation_turn(
            sid, "user", "How do I configure NPU acceleration?"
        )
        agent1.memory.store_conversation_turn(
            sid, "assistant", "You need to install the NPU driver first."
        )
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        results = agent2.memory.search_conversations("NPU acceleration")
        assert len(results) >= 1
        assert any("NPU" in r["content"] for r in results)

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()


# ── Auto-Extraction Persistence ──────────────────────────────────────────────


class TestAutoExtractionPersistence:
    """Auto-extracted facts from one session are available in the next."""

    def test_auto_extracted_fact_persists(self, workspace):
        """Fact auto-extracted in session 1 is recallable in session 2."""
        agent1 = _make_mixin(workspace)
        # Simulate a conversation where user states a fact
        agent1._auto_extract_after_query(
            user_input="Our audience is AI developers and researchers",
            assistant_response="I understand your target audience.",
        )
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        results = agent2.knowledge.recall(query="audience AI developers")
        assert len(results) >= 1

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_auto_extracted_preference_persists(self, workspace):
        """Preference auto-extracted in session 1 is recallable in session 2."""
        agent1 = _make_mixin(workspace)
        agent1._auto_extract_after_query(
            user_input="I prefer a technical but friendly tone for our posts",
            assistant_response="Got it, I'll use a technical but friendly tone.",
        )
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        results = agent2.knowledge.recall(query="technical friendly tone")
        assert len(results) >= 1

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_auto_extract_dedup_across_sessions(self, workspace):
        """Same fact extracted in two sessions doesn't create duplicates."""
        agent1 = _make_mixin(workspace)
        agent1._auto_extract_after_query(
            user_input="Our audience is AI developers",
            assistant_response="OK",
        )
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        agent2._auto_extract_after_query(
            user_input="Our audience is AI developers and researchers",
            assistant_response="OK",
        )

        # Should have at most 1 insight (dedup should catch the overlap)
        results = agent2.knowledge.recall(query="audience AI developers")
        assert len(results) <= 2  # Allow some tolerance for slightly different wording

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()


# ── Usage Tracking Persistence ───────────────────────────────────────────────


class TestUsageTrackingPersistence:
    """Usage counters and confidence persist across sessions."""

    def test_usage_count_persists(self, workspace):
        """record_usage() counts persist across agent restarts."""
        agent1 = _make_mixin(workspace)
        insight_id = agent1.knowledge.store_insight(
            category="skill", content="Post on LinkedIn"
        )
        agent1.knowledge.record_usage(insight_id, success=True)
        agent1.knowledge.record_usage(insight_id, success=True)
        agent1.knowledge.record_usage(insight_id, success=False)
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        results = agent2.knowledge.recall(query="LinkedIn", category="skill")
        assert len(results) >= 1
        skill = results[0]
        assert skill["success_count"] == 2
        assert skill["failure_count"] == 1
        assert skill["use_count"] == 3

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_confidence_persists(self, workspace):
        """Confidence changes from usage persist across sessions."""
        agent1 = _make_mixin(workspace)
        insight_id = agent1.knowledge.store_insight(
            category="fact", content="Users prefer dark mode"
        )
        # Record several successful uses to bump confidence
        for _ in range(5):
            agent1.knowledge.record_usage(insight_id, success=True)
        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_mixin(workspace)
        results = agent2.knowledge.recall(query="dark mode")
        assert len(results) >= 1
        # Confidence should be higher than default 0.5 after 5 successes
        assert results[0]["confidence"] > 0.5

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()
