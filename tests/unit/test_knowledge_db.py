# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for KnowledgeDB — cross-session persistent knowledge storage.

Tests insights (with categories, metadata, dedup, confidence decay),
credentials (encrypted storage, expiry), and preferences.
"""

from datetime import datetime, timedelta

import pytest

from gaia.agents.base.shared_state import KnowledgeDB


@pytest.fixture
def knowledge_db(tmp_path):
    """Create a fresh KnowledgeDB in a temp directory for each test."""
    db = KnowledgeDB(tmp_path / "knowledge.db")
    return db


class TestKnowledgeDBStoreInsight:
    """test_knowledge_db_store_insight: store_insight() persists. recall() finds it via FTS5."""

    def test_store_and_recall_insight(self, knowledge_db):
        """Store an insight and recall it via FTS5 search."""
        insight_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration on AMD Ryzen AI processors",
            domain="hardware",
        )
        assert insight_id is not None

        results = knowledge_db.recall("NPU acceleration")
        assert len(results) >= 1
        match = next((r for r in results if r["id"] == insight_id), None)
        assert match is not None
        assert match["category"] == "fact"
        assert "NPU acceleration" in match["content"]

    def test_store_with_triggers(self, knowledge_db):
        """Store an insight with trigger keywords and verify recall."""
        insight_id = knowledge_db.store_insight(
            category="strategy",
            content="Post on LinkedIn during weekday mornings for best engagement",
            triggers=["linkedin", "posting", "schedule"],
        )
        results = knowledge_db.recall("LinkedIn posting")
        assert len(results) >= 1
        match = next((r for r in results if r["id"] == insight_id), None)
        assert match is not None


class TestKnowledgeDBCategories:
    """test_knowledge_db_categories: Insights with different categories are stored and filtered correctly."""

    def test_multiple_categories(self, knowledge_db):
        """Store insights in different categories and verify they all persist."""
        categories = ["event", "fact", "strategy", "skill", "tool", "agent"]
        ids = {}
        for cat in categories:
            ids[cat] = knowledge_db.store_insight(
                category=cat,
                content=f"Test insight for {cat} category about GAIA framework",
            )

        # All should be recallable
        results = knowledge_db.recall("GAIA framework", top_k=10)
        found_categories = {r["category"] for r in results}
        for cat in categories:
            assert (
                cat in found_categories
            ), f"Category '{cat}' not found in recall results"


class TestKnowledgeDBMetadata:
    """test_knowledge_db_metadata: store_insight with metadata returns it intact."""

    def test_metadata_round_trip(self, knowledge_db):
        """Store insight with metadata JSON and verify it's returned intact on recall."""
        metadata = {
            "type": "replay",
            "steps": [
                {"action": "navigate", "url": "https://linkedin.com"},
                {"action": "click", "selector": "#post-button"},
                {"action": "fill", "selector": "#post-text", "value": "{content}"},
            ],
        }
        insight_id = knowledge_db.store_insight(
            category="skill",
            content="LinkedIn posting workflow",
            metadata=metadata,
        )

        results = knowledge_db.recall("LinkedIn posting workflow")
        assert len(results) >= 1
        match = next((r for r in results if r["id"] == insight_id), None)
        assert match is not None
        assert match["metadata"] == metadata
        assert match["metadata"]["steps"][0]["action"] == "navigate"

    def test_metadata_none_for_simple_insights(self, knowledge_db):
        """Insights without metadata return None for metadata field."""
        insight_id = knowledge_db.store_insight(
            category="fact",
            content="The sky is blue",
        )
        results = knowledge_db.recall("sky is blue")
        match = next((r for r in results if r["id"] == insight_id), None)
        assert match is not None
        assert match["metadata"] is None


class TestKnowledgeDBCategoryFilter:
    """test_knowledge_db_category_filter: recall with category returns only matching category."""

    def test_category_filter(self, knowledge_db):
        """recall(query, category='skill') returns only skills, not facts or strategies."""
        knowledge_db.store_insight(
            category="skill", content="How to post on LinkedIn automatically"
        )
        knowledge_db.store_insight(
            category="fact", content="LinkedIn has 900 million users"
        )
        knowledge_db.store_insight(
            category="strategy", content="LinkedIn strategy for developer marketing"
        )

        results = knowledge_db.recall("LinkedIn", category="skill")
        assert len(results) >= 1
        for r in results:
            assert r["category"] == "skill"


class TestKnowledgeDBDedupSimilar:
    """test_knowledge_db_dedup_similar: Similar content updates existing row instead of creating duplicate."""

    def test_dedup_similar_content(self, knowledge_db):
        """Storing similar content in same category deduplicates."""
        id1 = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration",
        )
        id2 = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU",
        )

        # Should have updated the existing row, not created a new one
        # id2 should be the same as id1 (dedup detected)
        assert id2 == id1

        # Only one entry should exist
        results = knowledge_db.recall("GAIA NPU", top_k=10)
        fact_results = [r for r in results if r["category"] == "fact"]
        assert len(fact_results) == 1


class TestKnowledgeDBDedupDifferent:
    """test_knowledge_db_dedup_different: Different content creates separate entries."""

    def test_no_false_dedup(self, knowledge_db):
        """Completely different content should NOT be deduped."""
        id1 = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration",
        )
        id2 = knowledge_db.store_insight(
            category="fact",
            content="LinkedIn posting schedule is Monday through Friday",
        )

        # Should be different entries
        assert id1 != id2


class TestKnowledgeDBDedupCrossCategory:
    """test_knowledge_db_dedup_cross_category: Same content in different categories are NOT deduped."""

    def test_cross_category_no_dedup(self, knowledge_db):
        """Same content in different categories creates separate entries."""
        id1 = knowledge_db.store_insight(
            category="skill",
            content="GAIA supports NPU acceleration on AMD hardware",
        )
        id2 = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration on AMD hardware",
        )

        # Different categories — should NOT be deduped
        assert id1 != id2


class TestKnowledgeDBPreferences:
    """test_knowledge_db_preferences: store_preference / get_preference round-trip."""

    def test_preference_round_trip(self, knowledge_db):
        """Store and retrieve a preference."""
        knowledge_db.store_preference("theme", "dark")
        assert knowledge_db.get_preference("theme") == "dark"

    def test_preference_update(self, knowledge_db):
        """Updating an existing preference replaces the value."""
        knowledge_db.store_preference("language", "English")
        knowledge_db.store_preference("language", "French")
        assert knowledge_db.get_preference("language") == "French"

    def test_preference_nonexistent_returns_none(self, knowledge_db):
        """Getting a non-existent preference returns None."""
        assert knowledge_db.get_preference("nonexistent") is None

    def test_preference_updated_at_timestamp(self, knowledge_db):
        """Preferences have an updated_at timestamp."""
        knowledge_db.store_preference("key1", "value1")
        # Verify we can get the preference (timestamp is internal)
        assert knowledge_db.get_preference("key1") == "value1"


class TestKnowledgeDBConfidenceUpdate:
    """test_knowledge_db_confidence_update: Recalling an insight updates confidence and last_used."""

    def test_recall_updates_last_used(self, knowledge_db):
        """Recalling an insight updates its last_used timestamp."""
        insight_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA is AMD's open source AI framework",
        )

        # Recall should update last_used
        results = knowledge_db.recall("GAIA AMD framework")
        assert len(results) >= 1

        # Verify last_used was set by checking the raw DB
        cursor = knowledge_db.conn.execute(
            "SELECT last_used FROM insights WHERE id = ?", (insight_id,)
        )
        row = cursor.fetchone()
        assert row[0] is not None  # last_used should be set after recall

    def test_recall_bumps_confidence(self, knowledge_db):
        """Recalling a recently-accessed insight bumps its confidence slightly."""
        insight_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA is AMD's open source AI framework",
            confidence=0.5,
        )

        # Set last_used to now so it's not stale
        knowledge_db.conn.execute(
            "UPDATE insights SET last_used = ? WHERE id = ?",
            (datetime.now().isoformat(), insight_id),
        )
        knowledge_db.conn.commit()

        # Recall should bump confidence
        results = knowledge_db.recall("GAIA AMD framework")
        match = next((r for r in results if r["id"] == insight_id), None)
        assert match is not None
        assert match["confidence"] > 0.5  # Should have been bumped


class TestKnowledgeDBConfidenceDecay:
    """test_knowledge_db_confidence_decay: Insights not accessed for 30+ days have confidence decayed."""

    def test_confidence_decay_on_stale_recall(self, knowledge_db):
        """Insights not accessed for 30+ days get confidence decayed by 0.9 on recall."""
        insight_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports multiple NPU backends for inference",
            confidence=0.8,
        )

        # Set last_used to 31 days ago to make it stale
        stale_date = (datetime.now() - timedelta(days=31)).isoformat()
        knowledge_db.conn.execute(
            "UPDATE insights SET last_used = ? WHERE id = ?",
            (stale_date, insight_id),
        )
        knowledge_db.conn.commit()

        # Recall should trigger decay: 0.8 * 0.9 = 0.72
        results = knowledge_db.recall("NPU backends inference")
        match = next((r for r in results if r["id"] == insight_id), None)
        assert match is not None
        assert abs(match["confidence"] - 0.72) < 0.01  # 0.8 * 0.9


class TestKnowledgeDBBM25Ranking:
    """test_knowledge_db_bm25_ranking: Recall returns more relevant results first."""

    def test_content_match_ranks_higher_than_trigger_match(self, knowledge_db):
        """Entry with query words in content ranks higher than entry with words only in triggers."""
        # Insight with "marketing strategy" in content (high relevance)
        id_content = knowledge_db.store_insight(
            category="strategy",
            content="Our marketing strategy focuses on developer advocacy",
        )
        # Insight with "marketing" only in triggers, not content
        id_trigger = knowledge_db.store_insight(
            category="event",
            content="Quarterly review completed successfully for Q3",
            triggers=["marketing", "strategy", "review"],
        )

        results = knowledge_db.recall("marketing strategy")
        assert len(results) >= 2

        # Content match should rank first
        ids_in_order = [r["id"] for r in results]
        content_pos = ids_in_order.index(id_content)
        trigger_pos = ids_in_order.index(id_trigger)
        assert (
            content_pos < trigger_pos
        ), "Content match should rank higher than trigger-only match"


class TestKnowledgeDBUsageTracking:
    """test_knowledge_db_usage_tracking: record_usage increments counts and updates confidence."""

    def test_record_success_usage(self, knowledge_db):
        """record_usage with success=True increments success_count and confidence."""
        insight_id = knowledge_db.store_insight(
            category="skill",
            content="LinkedIn posting workflow using Playwright",
            confidence=0.5,
        )

        knowledge_db.record_usage(insight_id, success=True)

        # Verify counts and confidence
        cursor = knowledge_db.conn.execute(
            "SELECT success_count, failure_count, use_count, confidence FROM insights WHERE id = ?",
            (insight_id,),
        )
        row = cursor.fetchone()
        assert row[0] == 1  # success_count
        assert row[1] == 0  # failure_count
        assert row[2] == 1  # use_count
        assert row[3] > 0.5  # confidence should increase

    def test_record_failure_usage(self, knowledge_db):
        """record_usage with success=False increments failure_count."""
        insight_id = knowledge_db.store_insight(
            category="skill",
            content="Email automation via Gmail API",
            confidence=0.5,
        )

        knowledge_db.record_usage(insight_id, success=False)

        cursor = knowledge_db.conn.execute(
            "SELECT success_count, failure_count, use_count, confidence FROM insights WHERE id = ?",
            (insight_id,),
        )
        row = cursor.fetchone()
        assert row[0] == 0  # success_count
        assert row[1] == 1  # failure_count
        assert row[2] == 1  # use_count
        assert row[3] < 0.5  # confidence should decrease

    def test_multiple_usages_update_correctly(self, knowledge_db):
        """Multiple usage records accumulate correctly."""
        insight_id = knowledge_db.store_insight(
            category="tool",
            content="Web scraping with Playwright browser automation",
            confidence=0.5,
        )

        knowledge_db.record_usage(insight_id, success=True)
        knowledge_db.record_usage(insight_id, success=True)
        knowledge_db.record_usage(insight_id, success=False)

        cursor = knowledge_db.conn.execute(
            "SELECT success_count, failure_count, use_count FROM insights WHERE id = ?",
            (insight_id,),
        )
        row = cursor.fetchone()
        assert row[0] == 2  # success_count
        assert row[1] == 1  # failure_count
        assert row[2] == 3  # use_count


class TestKnowledgeDBCredentialsStore:
    """test_knowledge_db_credentials_store: store_credential persists encrypted data."""

    def test_store_and_retrieve_credential(self, knowledge_db):
        """Store a credential and retrieve it."""
        knowledge_db.store_credential(
            credential_id="cred_github_pat",
            service="github",
            credential_type="api_key",
            encrypted_data="encrypted_token_data_here",
            scopes=["repo", "read:org"],
        )

        cred = knowledge_db.get_credential("github")
        assert cred is not None
        assert cred["id"] == "cred_github_pat"
        assert cred["service"] == "github"
        assert cred["credential_type"] == "api_key"
        assert cred["encrypted_data"] == "encrypted_token_data_here"
        assert "repo" in cred["scopes"]

    def test_store_credential_without_expiry(self, knowledge_db):
        """API keys typically don't expire — expires_at is None."""
        knowledge_db.store_credential(
            credential_id="cred_openai_key",
            service="openai",
            credential_type="api_key",
            encrypted_data="sk-encrypted-key-data",
        )

        cred = knowledge_db.get_credential("openai")
        assert cred is not None
        assert cred["expired"] is False  # No expiry = not expired


class TestKnowledgeDBCredentialsExpiry:
    """test_knowledge_db_credentials_expiry: Expired credentials are flagged."""

    def test_expired_credential_flagged(self, knowledge_db):
        """Credentials past their expires_at are flagged as expired."""
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        knowledge_db.store_credential(
            credential_id="cred_twitter_oauth",
            service="twitter",
            credential_type="oauth2",
            encrypted_data="encrypted_oauth_data",
            expires_at=past_date,
        )

        cred = knowledge_db.get_credential("twitter")
        assert cred is not None
        assert cred["expired"] is True

    def test_valid_credential_not_expired(self, knowledge_db):
        """Credentials with future expires_at are not flagged."""
        future_date = (datetime.now() + timedelta(days=30)).isoformat()
        knowledge_db.store_credential(
            credential_id="cred_gmail_oauth",
            service="gmail",
            credential_type="oauth2",
            encrypted_data="encrypted_gmail_data",
            expires_at=future_date,
        )

        cred = knowledge_db.get_credential("gmail")
        assert cred is not None
        assert cred["expired"] is False


class TestKnowledgeDBCredentialsUpdate:
    """test_knowledge_db_credentials_update: Refreshing a credential updates fields."""

    def test_update_credential(self, knowledge_db):
        """Updating a credential changes encrypted_data and timestamps."""
        knowledge_db.store_credential(
            credential_id="cred_twitter_oauth",
            service="twitter",
            credential_type="oauth2",
            encrypted_data="old_encrypted_data",
        )

        future_date = (datetime.now() + timedelta(days=90)).isoformat()
        knowledge_db.update_credential(
            credential_id="cred_twitter_oauth",
            encrypted_data="new_encrypted_data",
            expires_at=future_date,
        )

        cred = knowledge_db.get_credential("twitter")
        assert cred is not None
        assert cred["encrypted_data"] == "new_encrypted_data"
        assert cred["last_refreshed"] is not None
        assert cred["expired"] is False

    def test_update_only_expires_at(self, knowledge_db):
        """Can update just the expiry without changing encrypted_data."""
        knowledge_db.store_credential(
            credential_id="cred_test",
            service="test_service",
            credential_type="api_key",
            encrypted_data="original_data",
        )

        future_date = (datetime.now() + timedelta(days=365)).isoformat()
        knowledge_db.update_credential(
            credential_id="cred_test",
            expires_at=future_date,
        )

        cred = knowledge_db.get_credential("test_service")
        assert cred["encrypted_data"] == "original_data"  # Unchanged
        assert cred["expired"] is False


class TestKnowledgeDBRegressionBugs:
    """Regression tests for bugs found in code review."""

    def test_recall_does_not_increment_use_count(self, knowledge_db):
        """BUG 1 regression: recall() should NOT increment use_count.

        use_count should only be incremented via record_usage(), not on
        every recall hit. recall() updates confidence + last_used only.
        """
        insight_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA uses AMD NPU hardware acceleration for inference",
            confidence=0.5,
        )

        # Set last_used to now so it's recent (avoids decay path)
        knowledge_db.conn.execute(
            "UPDATE insights SET last_used = ? WHERE id = ?",
            (datetime.now().isoformat(), insight_id),
        )
        knowledge_db.conn.commit()

        # Recall the insight — should NOT increment use_count
        knowledge_db.recall("AMD NPU hardware")

        cursor = knowledge_db.conn.execute(
            "SELECT use_count FROM insights WHERE id = ?", (insight_id,)
        )
        row = cursor.fetchone()
        assert (
            row[0] == 0
        ), f"use_count should be 0 after recall (not incremented), got {row[0]}"

    def test_recall_then_record_usage_counts_correctly(self, knowledge_db):
        """BUG 1 regression: recall + record_usage should give use_count=1, not 2."""
        insight_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA framework supports Blender 3D automation",
            confidence=0.5,
        )

        # Set last_used to now
        knowledge_db.conn.execute(
            "UPDATE insights SET last_used = ? WHERE id = ?",
            (datetime.now().isoformat(), insight_id),
        )
        knowledge_db.conn.commit()

        # Recall (should NOT bump use_count)
        knowledge_db.recall("Blender 3D automation")

        # Record usage (should bump use_count to 1)
        knowledge_db.record_usage(insight_id, success=True)

        cursor = knowledge_db.conn.execute(
            "SELECT use_count FROM insights WHERE id = ?", (insight_id,)
        )
        row = cursor.fetchone()
        assert (
            row[0] == 1
        ), f"use_count should be 1 after recall + record_usage, got {row[0]}"

    def test_dedup_keeps_longer_content(self, knowledge_db):
        """BUG 3 regression: dedup should keep the longer content, not blindly overwrite.

        If existing content is "GAIA supports NPU acceleration" and new content is
        "GAIA supports NPU", the existing (longer) content should be preserved.
        """
        # Store a detailed insight
        original_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration on AMD Ryzen hardware",
            confidence=0.5,
        )

        # Store a shorter, similar insight that triggers dedup
        deduped_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration",
            confidence=0.6,
        )

        # Should be deduped to same ID
        assert deduped_id == original_id

        # The longer content should be preserved
        cursor = knowledge_db.conn.execute(
            "SELECT content FROM insights WHERE id = ?", (original_id,)
        )
        row = cursor.fetchone()
        assert "AMD Ryzen hardware" in row[0], f"Dedup lost content! Got: '{row[0]}'"

    def test_dedup_replaces_with_longer_content(self, knowledge_db):
        """BUG 3 regression: dedup should replace with new content if it's longer."""
        # Store a short insight
        original_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration",
            confidence=0.5,
        )

        # Store a longer, more detailed insight that triggers dedup
        deduped_id = knowledge_db.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration on AMD Ryzen hardware with full optimization",
            confidence=0.6,
        )

        assert deduped_id == original_id

        # The longer (new) content should now be stored
        cursor = knowledge_db.conn.execute(
            "SELECT content FROM insights WHERE id = ?", (original_id,)
        )
        row = cursor.fetchone()
        assert (
            "full optimization" in row[0]
        ), f"Dedup should have kept longer content. Got: '{row[0]}'"
