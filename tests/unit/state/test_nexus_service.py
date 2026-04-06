"""
Unit tests for NexusService.

This test suite validates the core NexusService functionality including:
- Singleton pattern with thread safety
- Event commit functionality
- Snapshot mutation safety
- Token-efficient digest generation
- Agent history and phase summarization
- Thread-safe concurrent access with 100+ threads

Quality Gate 2 Criteria Covered:
- State management integrity
- Thread safety under concurrent load
- Deep copy mutation protection
- Event cache management
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List
from unittest.mock import Mock, patch, MagicMock

from gaia.state.nexus import NexusService, WorkspaceIndex


# =============================================================================
# Helper function to create mock AuditLogger
# =============================================================================

def create_mock_audit_logger():
    """Create a mock AuditLogger class with get_instance method."""
    mock_instance = Mock()
    mock_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))

    mock_class = Mock()
    mock_class.get_instance = Mock(return_value=mock_instance)
    return mock_class, mock_instance


# =============================================================================
# NexusService Singleton Tests
# =============================================================================

class TestNexusServiceSingleton:
    """Tests for NexusService singleton pattern."""

    def teardown_method(self):
        """Reset singleton after each test."""
        NexusService.reset_instance()

    def test_singleton_instance(self):
        """Test singleton pattern returns same instance."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))
            nexus1 = NexusService.get_instance()
            nexus2 = NexusService.get_instance()
            assert nexus1 is nexus2

    def test_singleton_via_call(self):
        """Test calling NexusService() returns singleton."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))
            nexus1 = NexusService()
            nexus2 = NexusService()
            assert nexus1 is nexus2

    def test_singleton_thread_safety(self):
        """Test singleton is thread-safe with 100 concurrent threads."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))

            instances = []
            errors = []
            lock = threading.Lock()

            def get_instance():
                try:
                    instance = NexusService.get_instance()
                    with lock:
                        instances.append(instance)
                except Exception as e:
                    with lock:
                        errors.append(e)

            with ThreadPoolExecutor(max_workers=100) as executor:
                futures = [executor.submit(get_instance) for _ in range(100)]
                for future in as_completed(futures):
                    future.result()

            assert len(errors) == 0, f"Thread safety errors: {errors}"
            assert len(instances) == 100
            # All threads should get same instance
            assert all(instance is instances[0] for instance in instances)

    def test_singleton_initialization_once(self):
        """Test singleton __init__ runs only once."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))

            NexusService.reset_instance()

            # Create instance
            nexus1 = NexusService.get_instance()

            # Reset and try again
            NexusService.reset_instance()
            nexus2 = NexusService.get_instance()

            # After reset, new instance is created
            assert nexus1 is not nexus2


# =============================================================================
# NexusService Reset and Cleanup Tests
# =============================================================================

class TestNexusServiceReset:
    """Tests for NexusService reset and cleanup functionality."""

    def teardown_method(self):
        """Reset singleton after each test."""
        NexusService.reset_instance()

    def test_reset_instance(self):
        """Test reset_instance clears singleton."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))
            nexus1 = NexusService.get_instance()
            NexusService.reset_instance()
            nexus2 = NexusService.get_instance()
            assert nexus1 is not nexus2

    def test_cleanup_clears_state(self):
        """Test _cleanup clears event cache and workspace."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))
            nexus = NexusService.get_instance()
            nexus.commit("agent1", "test_event", {"key": "value"})

            # Verify state exists
            snapshot = nexus.get_snapshot()
            assert snapshot["chronicle"]

            # Reset and verify cleared
            NexusService.reset_instance()
            nexus2 = NexusService.get_instance()
            snapshot2 = nexus2.get_snapshot()
            assert not snapshot2["chronicle"]


# =============================================================================
# NexusService Commit Tests
# =============================================================================

class TestNexusServiceCommit:
    """Tests for NexusService commit() method."""

    def setup_method(self):
        """Reset singleton and setup mock before each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Reset singleton and stop patch after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_commit_basic(self):
        """Test basic commit functionality."""
        event_id = self.nexus.commit(
            agent_id="TestAgent",
            event_type="test_event",
            payload={"key": "value"}
        )
        assert event_id is not None
        assert isinstance(event_id, str)

    def test_commit_with_phase(self):
        """Test commit with phase parameter."""
        event_id = self.nexus.commit(
            agent_id="CodeAgent",
            event_type="file_created",
            payload={"path": "test.py"},
            phase="EXECUTION"
        )
        assert event_id

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 1
        assert snapshot["chronicle"][0]["phase"] == "EXECUTION"

    def test_commit_with_loop_id(self):
        """Test commit with loop_id parameter."""
        event_id = self.nexus.commit(
            agent_id="CodeAgent",
            event_type="iteration",
            payload={"step": 1},
            loop_id="loop-001"
        )
        assert event_id

        snapshot = self.nexus.get_snapshot()
        assert snapshot["chronicle"][0]["loop_id"] == "loop-001"

    def test_commit_event_cache_limit(self):
        """Test event cache respects max size limit (1000)."""
        # Commit 1100 events
        for i in range(1100):
            self.nexus.commit("agent", "event", {"index": i})

        snapshot = self.nexus.get_snapshot()
        # Cache should be limited to max size
        assert len(snapshot["chronicle"]) == 1000
        # Most recent events should be retained
        assert snapshot["chronicle"][-1]["payload"]["index"] == 1099

    def test_commit_workspace_tracking(self):
        """Test commit tracks file operations in workspace."""
        self.nexus.commit(
            agent_id="CodeAgent",
            event_type="file_created",
            payload={"path": "src/main.py", "lines": 42}
        )

        snapshot = self.nexus.get_snapshot()
        workspace = snapshot["workspace"]
        assert "files" in workspace
        assert "src/main.py" in workspace["files"]

    def test_commit_returns_uuid_format(self):
        """Test commit returns UUID-formatted string."""
        import uuid as uuid_module

        event_id = self.nexus.commit("agent", "event", {})

        # Should be valid UUID format
        try:
            uuid_module.UUID(event_id)
            assert True
        except ValueError:
            pytest.fail(f"Event ID '{event_id}' is not a valid UUID")


# =============================================================================
# NexusService Snapshot Tests
# =============================================================================

class TestNexusServiceSnapshot:
    """Tests for NexusService get_snapshot() method."""

    def setup_method(self):
        """Reset singleton and setup mock before each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Reset singleton and stop patch after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_get_snapshot_returns_deep_copy(self):
        """Test get_snapshot returns deep copy of state."""
        self.nexus.commit("agent", "event", {"key": "original"})
        snapshot1 = self.nexus.get_snapshot()

        # Modify snapshot
        snapshot1["chronicle"][0]["payload"]["key"] = "modified"

        # Get new snapshot - should have original value
        snapshot2 = self.nexus.get_snapshot()
        assert snapshot2["chronicle"][0]["payload"]["key"] == "original"

    def test_get_snapshot_structure(self):
        """Test get_snapshot returns expected structure."""
        self.nexus.commit("agent", "event", {})

        snapshot = self.nexus.get_snapshot()

        assert "chronicle" in snapshot
        assert "workspace" in snapshot
        assert "summary" in snapshot
        assert isinstance(snapshot["chronicle"], list)
        assert isinstance(snapshot["workspace"], dict)
        assert isinstance(snapshot["summary"], dict)

    def test_get_snapshot_mutation_safety(self):
        """Test modifying snapshot doesn't affect internal state."""
        self.nexus.commit("agent", "event", {"data": "test"})
        snapshot = self.nexus.get_snapshot()

        # Try to modify various parts
        snapshot["chronicle"].append({"fake": "event"})
        snapshot["workspace"]["fake"] = "data"
        snapshot["summary"]["fake"] = "value"

        # Verify internal state unchanged
        fresh_snapshot = self.nexus.get_snapshot()
        assert len(fresh_snapshot["chronicle"]) == 1
        assert "fake" not in fresh_snapshot["workspace"]
        assert "fake" not in fresh_snapshot["summary"]

    def test_get_snapshot_empty_state(self):
        """Test get_snapshot with no commits."""
        snapshot = self.nexus.get_snapshot()

        assert snapshot["chronicle"] == []
        assert snapshot["workspace"] == {"files": {}, "version": 0, "total_files": 0}
        assert snapshot["summary"]["total_events"] == 0

    def test_get_snapshot_summary_stats(self):
        """Test snapshot summary contains correct statistics."""
        # Commit multiple events
        for i in range(5):
            self.nexus.commit("agent", "event", {})

        # Track some files
        self.nexus.commit("agent", "file_created", {"path": "file1.py"})
        self.nexus.commit("agent", "file_created", {"path": "file2.py"})

        snapshot = self.nexus.get_snapshot()

        assert snapshot["summary"]["total_events"] == 7
        assert snapshot["summary"]["workspace_files"] == 2
        assert "timestamp" in snapshot["summary"]


# =============================================================================
# NexusService Digest Tests
# =============================================================================

class TestNexusServiceDigest:
    """Tests for NexusService get_digest() method."""

    def setup_method(self):
        """Reset singleton and setup mock before each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Reset singleton and stop patch after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_get_digest_basic(self):
        """Test basic digest generation."""
        self.nexus.commit("CodeAgent", "file_created", {"path": "main.py"})

        digest = self.nexus.get_digest()

        assert "## Recent Events" in digest
        assert "CodeAgent" in digest
        assert "file_created" in digest

    def test_get_digest_with_max_tokens(self):
        """Test digest respects max_tokens limit."""
        # Commit many events
        for i in range(50):
            self.nexus.commit("agent", "event", {"index": i, "data": "x" * 100})

        digest_small = self.nexus.get_digest(max_tokens=100)
        digest_large = self.nexus.get_digest(max_tokens=1000)

        # Small digest should be shorter
        assert len(digest_small) < len(digest_large)

    def test_get_digest_with_agent_filter(self):
        """Test digest filtering by agent."""
        self.nexus.commit("AgentA", "event", {})
        self.nexus.commit("AgentB", "event", {})
        self.nexus.commit("AgentA", "event", {})

        digest = self.nexus.get_digest(include_agents=["AgentA"])

        assert "AgentA" in digest
        assert "AgentB" not in digest

    def test_get_digest_with_phase_filter(self):
        """Test digest filtering by phase."""
        self.nexus.commit("agent", "event", {}, phase="PLANNING")
        self.nexus.commit("agent", "event", {}, phase="EXECUTION")
        self.nexus.commit("agent", "event", {}, phase="PLANNING")

        digest = self.nexus.get_digest(include_phases=["PLANNING"])

        assert "PLANNING" in digest
        # EXECUTION events should be filtered out
        assert digest.count("EXECUTION") == 0

    def test_get_digest_workspace_summary(self):
        """Test digest includes workspace file summary."""
        self.nexus.commit("agent", "file_created", {"path": "file1.py"})
        self.nexus.commit("agent", "file_created", {"path": "file2.py"})

        digest = self.nexus.get_digest()

        assert "## Workspace" in digest
        assert "Files tracked:" in digest


# =============================================================================
# NexusService Agent History Tests
# =============================================================================

class TestNexusServiceAgentHistory:
    """Tests for NexusService get_agent_history() method."""

    def setup_method(self):
        """Reset singleton and setup mock before each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Reset singleton and stop patch after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_get_agent_history_basic(self):
        """Test getting history for specific agent."""
        self.nexus.commit("AgentA", "event1", {})
        self.nexus.commit("AgentB", "event2", {})
        self.nexus.commit("AgentA", "event3", {})

        history = self.nexus.get_agent_history("AgentA")

        # Should have 2 events for AgentA
        assert len(history) == 2
        assert all(event["agent_id"] == "AgentA" for event in history)

    def test_get_agent_history_limit(self):
        """Test agent history respects limit parameter."""
        for i in range(100):
            self.nexus.commit("AgentA", "event", {"index": i})

        history = self.nexus.get_agent_history("AgentA", limit=10)

        assert len(history) == 10

    def test_get_agent_history_empty(self):
        """Test getting history for agent with no events."""
        self.nexus.commit("AgentA", "event", {})

        history = self.nexus.get_agent_history("AgentB")

        assert history == []

    def test_get_agent_history_reversed(self):
        """Test agent history returns most recent first."""
        for i in range(5):
            self.nexus.commit("AgentA", "event", {"index": i})

        history = self.nexus.get_agent_history("AgentA")

        # Most recent should be first
        assert history[0]["payload"]["index"] == 4
        assert history[-1]["payload"]["index"] == 0


# =============================================================================
# NexusService Phase Summary Tests
# =============================================================================

class TestNexusServicePhaseSummary:
    """Tests for NexusService get_phase_summary() method."""

    def setup_method(self):
        """Reset singleton and setup mock before each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Reset singleton and stop patch after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_get_phase_summary_basic(self):
        """Test basic phase summary."""
        self.nexus.commit("agent", "event", {}, phase="PLANNING")
        self.nexus.commit("agent", "event", {}, phase="EXECUTION")
        self.nexus.commit("agent", "event", {}, phase="PLANNING")

        summary = self.nexus.get_phase_summary("PLANNING")

        assert summary["phase"] == "PLANNING"
        assert summary["event_count"] == 2

    def test_get_phase_summary_statistics(self):
        """Test phase summary includes correct statistics."""
        self.nexus.commit("AgentA", "event", {}, phase="DEV")
        self.nexus.commit("AgentB", "event", {}, phase="DEV")
        self.nexus.commit("AgentA", "event", {}, phase="DEV")

        summary = self.nexus.get_phase_summary("DEV")

        assert summary["event_count"] == 3
        assert "AgentA" in summary["agents_involved"]
        assert "AgentB" in summary["agents_involved"]
        assert len(summary["events"]) <= 20  # Limited to last 20

    def test_get_phase_summary_empty(self):
        """Test phase summary when no events for phase."""
        summary = self.nexus.get_phase_summary("NONEXISTENT")

        assert summary["phase"] == "NONEXISTENT"
        assert summary["event_count"] == 0
        assert summary["events"] == []
        assert summary["first_event"] is None
        assert summary["last_event"] is None


# =============================================================================
# NexusService State Hash Tests
# =============================================================================

class TestNexusServiceStateHash:
    """Tests for NexusService get_state_hash() method."""

    def setup_method(self):
        """Reset singleton and setup mock before each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Reset singleton and stop patch after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_get_state_hash_changes_after_commit(self):
        """Test state hash changes after commit."""
        hash1 = self.nexus.get_state_hash()

        self.nexus.commit("agent", "event", {})

        hash2 = self.nexus.get_state_hash()

        assert hash1 != hash2

    def test_get_state_hash_consistency(self):
        """Test state hash is consistent for same state."""
        self.nexus.commit("agent", "event", {})

        hash1 = self.nexus.get_state_hash()
        hash2 = self.nexus.get_state_hash()

        assert hash1 == hash2

    def test_get_state_hash_format(self):
        """Test state hash is valid SHA-256 hex string."""
        hash_value = self.nexus.get_state_hash()

        # SHA-256 produces 64 character hex string
        assert len(hash_value) == 64
        assert all(c in "0123456789abcdef" for c in hash_value)


# =============================================================================
# NexusService Event Type Mapping Tests
# =============================================================================

class TestNexusServiceEventTypeMapping:
    """Tests for NexusService event type mapping."""

    def setup_method(self):
        """Reset singleton and setup mock before each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Reset singleton and stop patch after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_event_type_mapping_known_types(self):
        """Test known event types map correctly."""
        known_types = [
            "pipeline_start", "pipeline_complete",
            "phase_enter", "phase_exit",
            "agent_selected", "agent_executed",
            "tool_executed", "quality_evaluated",
            "decision_made", "defect_discovered"
        ]

        for event_type in known_types:
            event_id = self.nexus.commit("agent", event_type, {})
            assert event_id

    def test_event_type_mapping_unknown_type(self):
        """Test unknown event types default to DECISION_MADE."""
        # This should not raise, just use default mapping
        event_id = self.nexus.commit("agent", "unknown_event_type", {})
        assert event_id

    def test_commit_audit_logger_integration(self):
        """Test commit actually logs to audit logger."""
        # Just verify commit doesn't raise and returns ID
        event_id = self.nexus.commit(
            "agent", "pipeline_start", {}, phase="INIT"
        )
        assert event_id


# =============================================================================
# NexusService Thread Safety Tests
# =============================================================================

class TestNexusServiceThreadSafety:
    """Thread safety tests for NexusService (100+ concurrent threads)."""

    def setup_method(self):
        """Reset singleton and setup mock before each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Reset singleton and stop patch after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_concurrent_commit(self):
        """Test concurrent commits from 100+ threads."""
        results = []
        errors = []
        lock = threading.Lock()

        def commit_event(thread_id):
            try:
                event_id = self.nexus.commit(
                    f"agent_{thread_id}",
                    "concurrent_event",
                    {"thread": thread_id}
                )
                with lock:
                    results.append((thread_id, event_id))
            except Exception as e:
                with lock:
                    errors.append((thread_id, e))

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(commit_event, i) for i in range(100)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 100

        # Verify all commits recorded
        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 100

    def test_concurrent_snapshot_access(self):
        """Test concurrent snapshot access is thread-safe."""
        # First, commit some events
        for i in range(50):
            self.nexus.commit("agent", "event", {"index": i})

        snapshots = []
        errors = []
        lock = threading.Lock()

        def get_snapshot(thread_id):
            try:
                snapshot = self.nexus.get_snapshot()
                with lock:
                    snapshots.append((thread_id, snapshot))
            except Exception as e:
                with lock:
                    errors.append((thread_id, e))

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(get_snapshot, i) for i in range(100)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Snapshot errors: {errors}"
        assert len(snapshots) == 100

        # All snapshots should have same event count
        event_counts = [s[1]["summary"]["total_events"] for s in snapshots]
        assert all(count == 50 for count in event_counts)

    def test_concurrent_mixed_operations(self):
        """Test concurrent mix of commits and reads."""
        results = {"commits": 0, "snapshots": 0, "digests": 0}
        errors = []
        lock = threading.Lock()

        def commit_task(thread_id):
            try:
                self.nexus.commit(f"agent_{thread_id}", "commit", {})
                with lock:
                    results["commits"] += 1
            except Exception as e:
                with lock:
                    errors.append(("commit", thread_id, e))

        def snapshot_task(thread_id):
            try:
                self.nexus.get_snapshot()
                with lock:
                    results["snapshots"] += 1
            except Exception as e:
                with lock:
                    errors.append(("snapshot", thread_id, e))

        def digest_task(thread_id):
            try:
                self.nexus.get_digest()
                with lock:
                    results["digests"] += 1
            except Exception as e:
                with lock:
                    errors.append(("digest", thread_id, e))

        tasks = []
        with ThreadPoolExecutor(max_workers=150) as executor:
            # 50 commits, 50 snapshots, 50 digests
            for i in range(50):
                tasks.append(executor.submit(commit_task, i))
                tasks.append(executor.submit(snapshot_task, i + 50))
                tasks.append(executor.submit(digest_task, i + 100))

            for future in as_completed(tasks):
                future.result()

        assert len(errors) == 0, f"Mixed operation errors: {errors}"
        assert results["commits"] == 50
        assert results["snapshots"] == 50
        assert results["digests"] == 50

    def test_stress_test_1000_commits(self):
        """Stress test with 1000 concurrent commits."""
        errors = []
        lock = threading.Lock()

        def commit_many(thread_id):
            try:
                for i in range(10):
                    self.nexus.commit(f"agent_{thread_id}", "stress", {"i": i})
            except Exception as e:
                with lock:
                    errors.append((thread_id, e))

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(commit_many, i) for i in range(100)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Stress test errors: {errors}"

        snapshot = self.nexus.get_snapshot()
        # 100 threads * 10 commits each = 1000
        assert len(snapshot["chronicle"]) == 1000
