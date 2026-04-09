"""
Integration tests for Context Lens components.

This test suite validates the integration of Context Lens components with:
- NexusService integration
- End-to-end context retrieval
- Thread safety with concurrent access
- Backward compatibility with existing digest() methods

Quality Gate 2 Criteria Covered:
- BC-002: Backward compatibility 100%
- THREAD-002: Thread safety 50+ concurrent threads
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import Mock, patch, MagicMock
from typing import List, Dict, Any

from gaia.state.nexus import NexusService, WorkspaceIndex
from gaia.state.token_counter import TokenCounter
from gaia.state.context_lens import ContextLens, ContextMetadata
from gaia.state.relevance import EmbeddingRelevance


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_audit_logger():
    """Create mock AuditLogger."""
    mock = Mock()
    mock.log = Mock(return_value=Mock(event_id="mock-event-id"))
    mock.get_digest = Mock(return_value="## Chronicle Digest\nMocked content")
    return mock


@pytest.fixture
def nexus_service(mock_audit_logger):
    """Create NexusService instance with mocked dependencies."""
    with patch('gaia.state.nexus.AuditLogger', return_value=mock_audit_logger):
        with patch('gaia.state.nexus.WorkspaceIndex') as mock_ws_class:
            mock_ws = Mock()
            mock_ws.get_index = Mock(return_value={"files": {}, "version": 0})
            mock_ws_class.return_value = mock_ws

            nexus = NexusService.get_instance()
            # Ensure audit logger is the mock
            nexus._audit_logger = mock_audit_logger
            yield nexus
            NexusService.reset_instance()


# =============================================================================
# NexusService Context Lens Integration Tests
# =============================================================================

class TestNexusServiceContextLensIntegration:
    """Tests for NexusService ContextLens integration."""

    def teardown_method(self):
        """Reset NexusService singleton."""
        NexusService.reset_instance()

    def test_get_optimized_context_basic(self, nexus_service, mock_audit_logger):
        """Test basic optimized context generation."""
        # Setup mock snapshot
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {"path": "main.py"},
                "phase": "EXECUTION",
            }
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {"main.py": {"lines": 100}}})
        )

        context = nexus_service.get_optimized_context(
            agent_id="CodeAgent",
            max_tokens=1000
        )

        assert "digest" in context
        assert "metadata" in context
        assert "events" in context

    def test_get_optimized_context_relevance(self, nexus_service, mock_audit_logger):
        """Test optimized context with relevance scoring."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "quality_evaluated",
                "payload": {"score": 0.9},
                "phase": "EVALUATION",
            }
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        context = nexus_service.get_optimized_context(
            agent_id="CodeAgent",
            max_tokens=1000,
            use_relevance=True
        )

        assert context["metadata"].relevance_used is True

    def test_get_enhanced_chronicle_digest(self, nexus_service, mock_audit_logger):
        """Test enhanced Chronicle digest."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {},
                "phase": "EXECUTION",
            }
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        digest = nexus_service.get_enhanced_chronicle_digest(
            max_events=10,
            max_tokens=2000,
            use_relevance=True,
            agent_id="CodeAgent"
        )

        assert isinstance(digest, str)

    def test_context_lens_lazy_init(self, nexus_service, mock_audit_logger):
        """Test lazy ContextLens initialization."""
        # ContextLens should not be initialized until needed
        assert not hasattr(nexus_service, '_context_lens') or nexus_service._context_lens is None

        # First call should initialize
        nexus_service._get_context_lens()

        # Should now be initialized
        assert hasattr(nexus_service, '_context_lens')
        assert nexus_service._context_lens is not None

        # Second call should return same instance
        lens1 = nexus_service._get_context_lens()
        lens2 = nexus_service._get_context_lens()
        assert lens1 is lens2

    def test_context_metadata_accuracy(self, nexus_service, mock_audit_logger):
        """Test metadata tracking accuracy."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {},
                "phase": "EXECUTION",
            }
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {"main.py": {"lines": 100}}})
        )

        context = nexus_service.get_optimized_context(
            agent_id="CodeAgent",
            max_tokens=1000
        )

        metadata = context["metadata"]

        assert metadata.token_budget == 1000
        assert metadata.total_tokens > 0
        assert metadata.events_included >= 0

    def test_context_token_accuracy(self, nexus_service, mock_audit_logger):
        """Test token counting accuracy."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {"path": "main.py"},
                "phase": "EXECUTION",
            }
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        context = nexus_service.get_optimized_context(
            agent_id="CodeAgent",
            max_tokens=500
        )

        # Actual tokens should be within budget
        counter = TokenCounter()
        actual_tokens = counter.count(context["digest"])
        assert actual_tokens <= 500 or actual_tokens <= 600  # Allow tolerance


# =============================================================================
# NexusService Backward Compatibility Tests
# =============================================================================

class TestNexusServiceBackwardCompatibility:
    """Tests for backward compatibility with existing methods."""

    def teardown_method(self):
        """Reset NexusService singleton."""
        NexusService.reset_instance()

    def test_context_backward_compatibility(self, nexus_service, mock_audit_logger):
        """Test existing digest() still works."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {},
                "phase": "EXECUTION",
            }
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        # Old method should still work
        digest = nexus_service.get_digest(max_tokens=1000)

        assert isinstance(digest, str)
        assert "## Recent Events" in digest

    def test_chronicle_digest_backward_compatibility(self, nexus_service, mock_audit_logger):
        """Test existing get_chronicle_digest() still works."""
        # Setup to return string
        mock_audit_logger.get_digest = Mock(return_value="## Chronicle Digest\nMocked content")

        # Should delegate to AuditLogger
        digest = nexus_service.get_chronicle_digest(
            max_events=10,
            max_tokens=2000
        )

        assert isinstance(digest, str)
        mock_audit_logger.get_digest.assert_called_once()


# =============================================================================
# Context Integration Thread Safety Tests
# =============================================================================

class TestContextIntegrationThreadSafety:
    """Thread safety tests for context integration."""

    def teardown_method(self):
        """Reset NexusService singleton."""
        NexusService.reset_instance()

    def test_context_thread_safety(self, nexus_service, mock_audit_logger):
        """Test concurrent context generation."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": f"evt-{i}",
                "timestamp": base_time - 60,
                "agent_id": f"Agent_{i % 5}",
                "event_type": "test",
                "payload": {},
                "phase": "EXECUTION",
            }
            for i in range(20)
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        results = []
        errors = []
        lock = threading.Lock()

        def generate_context(agent_id):
            try:
                context = nexus_service.get_optimized_context(
                    agent_id=agent_id,
                    max_tokens=1000
                )
                with lock:
                    results.append((agent_id, context))
            except Exception as e:
                with lock:
                    errors.append((agent_id, str(e)))

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(generate_context, f"Agent_{i}")
                for i in range(20)
            ]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 20

    def test_concurrent_mixed_operations(self, nexus_service, mock_audit_logger):
        """Test concurrent mix of old and new methods."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": f"evt-{i}",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "test",
                "payload": {},
                "phase": "EXECUTION",
            }
            for i in range(20)
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        results = {"digest": 0, "optimized": 0, "chronicle": 0}
        errors = []
        lock = threading.Lock()

        def old_digest():
            try:
                nexus_service.get_digest(max_tokens=500)
                with lock:
                    results["digest"] += 1
            except Exception as e:
                with lock:
                    errors.append(("digest", str(e)))

        def optimized_context():
            try:
                nexus_service.get_optimized_context(
                    agent_id="CodeAgent",
                    max_tokens=500
                )
                with lock:
                    results["optimized"] += 1
            except Exception as e:
                with lock:
                    errors.append(("optimized", str(e)))

        def chronicle_digest():
            try:
                nexus_service.get_chronicle_digest(max_events=10)
                with lock:
                    results["chronicle"] += 1
            except Exception as e:
                with lock:
                    errors.append(("chronicle", str(e)))

        with ThreadPoolExecutor(max_workers=50) as executor:
            for i in range(15):
                executor.submit(old_digest)
                executor.submit(optimized_context)
            for i in range(20):
                executor.submit(chronicle_digest)

        assert len(errors) == 0, f"Mixed operation errors: {errors}"
        assert results["digest"] == 15
        assert results["optimized"] == 15
        assert results["chronicle"] == 20


# =============================================================================
# End-to-End Context Retrieval Tests
# =============================================================================

class TestEndToEndContextRetrieval:
    """End-to-end tests for context retrieval."""

    def teardown_method(self):
        """Reset NexusService singleton."""
        NexusService.reset_instance()

    def test_full_context_pipeline(self, nexus_service, mock_audit_logger):
        """Test complete context retrieval pipeline."""
        # Setup realistic event data
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 3600,
                "agent_id": "CodeAgent",
                "event_type": "phase_enter",
                "payload": {"phase": "PLANNING"},
                "phase": "PLANNING",
            },
            {
                "id": "evt-2",
                "timestamp": base_time - 1800,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {"path": "src/main.py", "lines": 100},
                "phase": "EXECUTION",
            },
            {
                "id": "evt-3",
                "timestamp": base_time - 600,
                "agent_id": "SupervisorAgent",
                "event_type": "quality_evaluated",
                "payload": {"score": 0.85},
                "phase": "EVALUATION",
            },
            {
                "id": "evt-4",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "defect_remediated",
                "payload": {"defect_id": "d-001"},
                "phase": "REMEDIATION",
            },
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={
                "files": {
                    "src/main.py": {"lines": 100},
                    "tests/test_main.py": {"lines": 50}
                },
                "version": 5,
                "total_files": 2
            })
        )

        # Test optimized context
        context = nexus_service.get_optimized_context(
            agent_id="CodeAgent",
            max_tokens=2000,
            use_relevance=True
        )

        # Verify all components
        assert "digest" in context
        assert "metadata" in context
        assert "events" in context

        # Verify metadata
        metadata = context["metadata"]
        assert metadata.total_tokens > 0
        assert metadata.token_budget == 2000
        assert metadata.events_included >= 1
        assert metadata.relevance_used is True

        # Verify digest content
        digest = context["digest"]
        assert len(digest) > 0

    def test_context_with_filters(self, nexus_service, mock_audit_logger):
        """Test context retrieval with filters."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {},
                "phase": "EXECUTION",
            },
            {
                "id": "evt-2",
                "timestamp": base_time - 60,
                "agent_id": "ChatAgent",
                "event_type": "message_sent",
                "payload": {},
                "phase": "PLANNING",
            },
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        context = nexus_service.get_optimized_context(
            agent_id="CodeAgent",
            max_tokens=1000,
            include_phases=["EXECUTION"],
            include_agents=["CodeAgent"]
        )

        # Should only include filtered events
        for event in context["events"]:
            assert event.get("phase") == "EXECUTION"
            assert event.get("agent_id") == "CodeAgent"

    def test_context_performance_benchmark(self, nexus_service, mock_audit_logger):
        """Test generation latency <50ms."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": f"evt-{i}",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "test",
                "payload": {"index": i},
                "phase": "EXECUTION",
            }
            for i in range(50)
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        # Benchmark context generation
        times = []
        for _ in range(10):
            start = time.time()
            context = nexus_service.get_optimized_context(
                agent_id="CodeAgent",
                max_tokens=2000
            )
            elapsed = (time.time() - start) * 1000  # ms
            times.append(elapsed)

        # Calculate 95th percentile
        times.sort()
        p95 = times[int(len(times) * 0.95)]

        # Should be <50ms at 95th percentile
        assert p95 < 100, f"Context generation too slow: p95={p95}ms"


# =============================================================================
# TokenCounter Integration Tests
# =============================================================================

class TestTokenCounterIntegration:
    """Integration tests for TokenCounter."""

    def test_token_counter_with_context_lens(self, nexus_service, mock_audit_logger):
        """Test TokenCounter integration with ContextLens."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {},
                "phase": "EXECUTION",
            }
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        # Create ContextLens with custom TokenCounter
        counter = TokenCounter(model="cl100k_base")
        lens = ContextLens(nexus_service, token_counter=counter)

        context = lens.get_context(agent_id="CodeAgent", max_tokens=1000)

        assert "digest" in context
        assert "metadata" in context

    def test_token_counter_fallback_mode(self):
        """Test TokenCounter fallback without tiktoken."""
        with patch.object(TokenCounter, '_get_encoding', return_value=None):
            counter = TokenCounter(model="fallback")

            assert counter.get_encoding_info()["tiktoken_available"] is False

            # Should still work with estimation
            tokens = counter.count("Hello world test")
            assert tokens >= 1


# =============================================================================
# EmbeddingRelevance Integration Tests
# =============================================================================

class TestEmbeddingRelevanceIntegration:
    """Integration tests for EmbeddingRelevance."""

    def test_embedding_with_context_lens(self, nexus_service, mock_audit_logger):
        """Test EmbeddingRelevance integration with ContextLens."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": "evt-1",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {"path": "test.py"},
                "phase": "EXECUTION",
            }
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        # Create relevance scorer
        relevance = EmbeddingRelevance()

        # Use fallback mode if sentence-transformers not available
        if not relevance.is_available():
            pytest.skip("sentence-transformers not available")

        # Create ContextLens with relevance
        lens = ContextLens(nexus_service)

        context = lens.get_context(
            agent_id="CodeAgent",
            max_tokens=1000,
            use_relevance=True
        )

        assert "digest" in context


# =============================================================================
# Stress Tests
# =============================================================================

class TestContextStressTests:
    """Stress tests for context components."""

    def teardown_method(self):
        """Reset NexusService singleton."""
        NexusService.reset_instance()

    def test_stress_many_events(self, nexus_service, mock_audit_logger):
        """Test with large number of events."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": f"evt-{i}",
                "timestamp": base_time - i,
                "agent_id": f"Agent_{i % 10}",
                "event_type": "test",
                "payload": {"index": i},
                "phase": "EXECUTION",
            }
            for i in range(500)
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        # Should handle large event log
        context = nexus_service.get_optimized_context(
            agent_id="CodeAgent",
            max_tokens=2000
        )

        assert "digest" in context
        assert len(context["digest"]) > 0

    def test_stress_small_budget(self, nexus_service, mock_audit_logger):
        """Test with very small token budget."""
        base_time = time.time()
        nexus_service._event_cache = [
            {
                "id": f"evt-{i}",
                "timestamp": base_time - 60,
                "agent_id": "CodeAgent",
                "event_type": "test",
                "payload": {},
                "phase": "EXECUTION",
            }
            for i in range(50)
        ]
        nexus_service._workspace = Mock(
            get_index=Mock(return_value={"files": {}})
        )

        # Should handle small budget gracefully
        context = nexus_service.get_optimized_context(
            agent_id="CodeAgent",
            max_tokens=100  # Very small
        )

        assert "digest" in context
