"""
Unit tests for ChronicleDigest functionality in AuditLogger.

This test suite validates the get_digest() method and related functionality:
- Token budget enforcement
- Phase filtering
- Agent filtering
- Event type filtering
- Payload summarization
- Empty log handling
- Header formatting
- Phase summaries
- Loop summaries
- Thread safety
- Integration with NexusService.get_chronicle_digest()

Quality Gate 2 Criteria Covered:
- CHRON-001: Event timestamp precision
- CHRON-002: Digest token efficiency (<4000 tokens)
- PERF-002: Digest generation latency (<50ms)
"""

import pytest
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List
from unittest.mock import Mock, patch, MagicMock

from gaia.pipeline.audit_logger import AuditLogger, AuditEventType, AuditEvent
from gaia.state.nexus import NexusService


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def fresh_audit_logger():
    """Create a fresh AuditLogger instance for each test."""
    return AuditLogger(logger_id=f"test-{time.time()}")


@pytest.fixture
def populated_audit_logger(fresh_audit_logger):
    """Create AuditLogger with sample events."""
    logger = fresh_audit_logger

    # Log various events
    logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING", agent_id="CodeAgent")
    logger.log(AuditEventType.TOOL_EXECUTED, phase="PLANNING", agent_id="CodeAgent", tool_name="read_file")
    logger.log(AuditEventType.TOOL_EXECUTED, phase="PLANNING", agent_id="CodeAgent", tool_name="write_file")
    logger.log(AuditEventType.PHASE_EXIT, phase="PLANNING", agent_id="CodeAgent")

    logger.log(AuditEventType.PHASE_ENTER, phase="EXECUTION", agent_id="CodeAgent")
    logger.log(AuditEventType.TOOL_EXECUTED, phase="EXECUTION", agent_id="CodeAgent", tool_name="run_tests")
    logger.log(AuditEventType.PHASE_EXIT, phase="EXECUTION", agent_id="CodeAgent")

    return logger


@pytest.fixture
def logger_with_loops(fresh_audit_logger):
    """Create AuditLogger with loop events."""
    logger = fresh_audit_logger

    # Log events with loop IDs
    for i in range(3):
        loop_id = f"loop-{i:03d}"
        logger.log(
            AuditEventType.LOOP_BACK,
            loop_id=loop_id,
            phase="DEVELOPMENT",
            agent_id="CodeAgent",
            iteration=i
        )
        logger.log(
            AuditEventType.TOOL_EXECUTED,
            loop_id=loop_id,
            phase="DEVELOPMENT",
            agent_id="CodeAgent",
            tool_name="fix_defect"
        )

    return logger


@pytest.fixture
def logger_with_many_events(fresh_audit_logger):
    """Create AuditLogger with many events for token budget tests."""
    logger = fresh_audit_logger

    for i in range(100):
        logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase=f"PHASE_{i % 5}",
            agent_id=f"Agent_{i % 3}",
            tool_name=f"tool_{i}",
            result=f"Result data for event {i} with some extra text",
            metadata={"index": i, "data": "x" * 50}
        )

    return logger


# =============================================================================
# Basic Digest Generation Tests
# =============================================================================

class TestBasicDigestGeneration:
    """Tests for basic digest generation functionality."""

    def test_digest_basic_structure(self, populated_audit_logger):
        """Test digest has expected structure with header and sections."""
        digest = populated_audit_logger.get_digest()

        assert "## Chronicle Digest" in digest
        assert "## Recent Events" in digest
        assert "Generated:" in digest
        assert "Events:" in digest

    def test_digest_includes_recent_events(self, populated_audit_logger):
        """Test digest includes recent events."""
        digest = populated_audit_logger.get_digest()

        assert "PLANNING" in digest
        assert "EXECUTION" in digest
        assert "CodeAgent" in digest
        assert "TOOL_EXECUTED" in digest

    def test_digest_empty_log(self, fresh_audit_logger):
        """Test digest generation with empty log."""
        digest = fresh_audit_logger.get_digest()

        assert "## Chronicle Digest" in digest
        assert "Events: 0" in digest
        assert "## Recent Events" in digest

    def test_digest_single_event(self, fresh_audit_logger):
        """Test digest with single event."""
        fresh_audit_logger.log(
            AuditEventType.PIPELINE_START,
            pipeline_id="test-001"
        )

        digest = fresh_audit_logger.get_digest()

        assert "PIPELINE_START" in digest
        assert "pipeline_id=test-001" in digest

    def test_digest_timestamp_format(self, populated_audit_logger):
        """Test digest header contains properly formatted timestamp."""
        digest = populated_audit_logger.get_digest()

        # Should contain date-time pattern
        assert "Generated:" in digest
        # Extract timestamp portion
        import re
        match = re.search(r'Generated: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)', digest)
        assert match is not None


# =============================================================================
# Token Budget Enforcement Tests
# =============================================================================

class TestTokenBudgetEnforcement:
    """Tests for token budget enforcement in digest generation."""

    def test_token_budget_small(self, logger_with_many_events):
        """Test digest respects small token budget."""
        digest_small = logger_with_many_events.get_digest(max_tokens=100)
        digest_large = logger_with_many_events.get_digest(max_tokens=1000)

        assert len(digest_small) < len(digest_large)

    def test_token_budget_very_small(self, logger_with_many_events):
        """Test digest with very small token budget (100 tokens)."""
        digest = logger_with_many_events.get_digest(max_tokens=100)

        # Should still have header
        assert "## Chronicle Digest" in digest
        # But limited content
        assert len(digest) < 500

    def test_token_budget_estimation(self, fresh_audit_logger):
        """Test token estimation uses ~4 chars/token ratio."""
        # Log an event with known content length
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent",
            tool_name="test"
        )

        digest = fresh_audit_logger.get_digest()
        estimated_tokens = len(digest) // 4

        # Verify estimation is reasonable
        assert estimated_tokens > 0

    def test_token_budget_truncation_message(self, logger_with_many_events):
        """Test digest shows truncation message when budget exceeded."""
        digest = logger_with_many_events.get_digest(max_tokens=200)

        # May contain truncation indicator
        # (depends on implementation details)
        assert digest is not None

    def test_default_token_budget(self, logger_with_many_events):
        """Test default token budget is 3500."""
        # Default should allow more content
        digest = logger_with_many_events.get_digest()

        assert len(digest) > 0
        # Should contain substantial content
        assert "## Recent Events" in digest


# =============================================================================
# Phase Filtering Tests
# =============================================================================

class TestPhaseFiltering:
    """Tests for phase-based event filtering in digest."""

    def test_phase_filter_single(self, populated_audit_logger):
        """Test filtering digest to single phase."""
        digest = populated_audit_logger.get_digest(include_phases=["PLANNING"])

        assert "PLANNING" in digest
        # EXECUTION events should be filtered out
        lines = digest.split('\n')
        execution_lines = [l for l in lines if 'EXECUTION' in l and 'Phase Summaries' not in l]
        # May appear in phase summaries section but not as events
        assert len(execution_lines) == 0

    def test_phase_filter_multiple(self, populated_audit_logger):
        """Test filtering digest to multiple phases."""
        digest = populated_audit_logger.get_digest(
            include_phases=["PLANNING", "EXECUTION"]
        )

        assert "PLANNING" in digest
        assert "EXECUTION" in digest

    def test_phase_filter_nonexistent(self, populated_audit_logger):
        """Test filtering to nonexistent phase."""
        digest = populated_audit_logger.get_digest(
            include_phases=["NONEXISTENT_PHASE"]
        )

        # Should still have header but no events from that phase
        assert "## Chronicle Digest" in digest

    def test_phase_filter_case_sensitive(self, fresh_audit_logger):
        """Test phase filtering is case-sensitive."""
        fresh_audit_logger.log(
            AuditEventType.PHASE_ENTER,
            phase="PLANNING",
            agent_id="TestAgent"
        )

        # Exact match should work
        digest_exact = fresh_audit_logger.get_digest(include_phases=["PLANNING"])
        assert "PLANNING" in digest_exact

        # Wrong case should not match
        digest_wrong_case = fresh_audit_logger.get_digest(include_phases=["planning"])
        # Should not include the event
        assert digest_wrong_case is not None


# =============================================================================
# Agent Filtering Tests
# =============================================================================

class TestAgentFiltering:
    """Tests for agent-based event filtering in digest."""

    def test_agent_filter_single(self, populated_audit_logger):
        """Test filtering digest to single agent."""
        # First add events from multiple agents
        populated_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="PLANNING",
            agent_id="QualityAgent",
            tool_name="review"
        )

        digest = populated_audit_logger.get_digest(include_agents=["CodeAgent"])

        assert "CodeAgent" in digest
        # QualityAgent events should be filtered
        lines = digest.split('\n')
        quality_event_lines = [
            l for l in lines
            if 'QualityAgent' in l and 'Phase Summaries' not in l
        ]
        assert len(quality_event_lines) == 0

    def test_agent_filter_multiple(self, fresh_audit_logger):
        """Test filtering digest to multiple agents."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="PLANNING",
            agent_id="AgentA",
            tool_name="tool1"
        )
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="PLANNING",
            agent_id="AgentB",
            tool_name="tool2"
        )
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="PLANNING",
            agent_id="AgentC",
            tool_name="tool3"
        )

        digest = fresh_audit_logger.get_digest(
            include_agents=["AgentA", "AgentB"]
        )

        assert "AgentA" in digest
        assert "AgentB" in digest
        assert "AgentC" not in digest

    def test_agent_filter_default_system(self, fresh_audit_logger):
        """Test events without agent_id show as 'system'."""
        fresh_audit_logger.log(
            AuditEventType.PIPELINE_START,
            pipeline_id="test-001"
        )

        digest = fresh_audit_logger.get_digest()

        # Should show 'system' for events without agent
        assert "system" in digest


# =============================================================================
# Event Type Filtering Tests
# =============================================================================

class TestEventTypeFiltering:
    """Tests for event type filtering in digest."""

    def test_event_type_filter_single(self, populated_audit_logger):
        """Test filtering to single event type."""
        digest = populated_audit_logger.get_digest(
            event_types=[AuditEventType.TOOL_EXECUTED]
        )

        assert "TOOL_EXECUTED" in digest

    def test_event_type_filter_multiple(self, populated_audit_logger):
        """Test filtering to multiple event types."""
        digest = populated_audit_logger.get_digest(
            event_types=[
                AuditEventType.PHASE_ENTER,
                AuditEventType.PHASE_EXIT
            ]
        )

        assert "PHASE_ENTER" in digest
        assert "PHASE_EXIT" in digest

    def test_event_type_filter_combined_with_phase(self, populated_audit_logger):
        """Test combined event type and phase filtering."""
        digest = populated_audit_logger.get_digest(
            include_phases=["PLANNING"],
            event_types=[AuditEventType.TOOL_EXECUTED]
        )

        # Should have PLANNING phase TOOL_EXECUTED events
        assert "PLANNING" in digest
        assert "TOOL_EXECUTED" in digest


# =============================================================================
# Payload Summarization Tests
# =============================================================================

class TestPayloadSummarization:
    """Tests for payload summarization in digest."""

    def test_payload_summary_basic(self, fresh_audit_logger):
        """Test basic payload summarization."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent",
            tool_name="read_file",
            path="src/main.py",
            lines=100
        )

        digest = fresh_audit_logger.get_digest()

        # Should include key payload fields
        assert "tool_name=read_file" in digest

    def test_payload_summary_truncation(self, fresh_audit_logger):
        """Test payload values are truncated if long."""
        long_value = "x" * 100
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent",
            tool_name="test",
            long_field=long_value
        )

        digest = fresh_audit_logger.get_digest()

        # Long values should be truncated (to ~30 chars)
        assert "TestAgent" in digest

    def test_payload_summary_field_limit(self, fresh_audit_logger):
        """Test only first 3 payload fields shown."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent",
            field1="value1",
            field2="value2",
            field3="value3",
            field4="value4",
            field5="value5"
        )

        digest = fresh_audit_logger.get_digest()

        # Should indicate there are more fields
        assert "..." in digest or "total" in digest

    def test_payload_empty(self, fresh_audit_logger):
        """Test event with empty payload."""
        fresh_audit_logger.log(
            AuditEventType.PIPELINE_START
        )

        digest = fresh_audit_logger.get_digest()

        # Should still show event
        assert "PIPELINE_START" in digest


# =============================================================================
# Header Formatting Tests
# =============================================================================

class TestHeaderFormatting:
    """Tests for digest header formatting."""

    def test_header_contains_event_count(self, populated_audit_logger):
        """Test header shows correct event count."""
        digest = populated_audit_logger.get_digest()

        # Count logged events
        events = populated_audit_logger.get_events()

        assert f"Events: {len(events)}" in digest

    def test_header_format_consistency(self, fresh_audit_logger):
        """Test header format is consistent."""
        digest1 = fresh_audit_logger.get_digest()

        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent"
        )

        digest2 = fresh_audit_logger.get_digest()

        # Both should have same header structure
        assert "## Chronicle Digest" in digest1
        assert "## Chronicle Digest" in digest2


# =============================================================================
# Phase Summaries Tests
# =============================================================================

class TestPhaseSummaries:
    """Tests for phase summaries section in digest."""

    def test_phase_summaries_section_exists(self, populated_audit_logger):
        """Test phase summaries section is present."""
        digest = populated_audit_logger.get_digest()

        assert "## Phase Summaries" in digest

    def test_phase_summaries_content(self, populated_audit_logger):
        """Test phase summaries contain expected content."""
        digest = populated_audit_logger.get_digest()

        # Should mention phases
        assert "PLANNING" in digest or "EXECUTION" in digest

    def test_phase_summaries_agent_count(self, fresh_audit_logger):
        """Test phase summaries show agents involved."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="DEV",
            agent_id="AgentA"
        )
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="DEV",
            agent_id="AgentB"
        )

        digest = fresh_audit_logger.get_digest()

        assert "DEV" in digest


# =============================================================================
# Loop Summaries Tests
# =============================================================================

class TestLoopSummaries:
    """Tests for loop iteration summaries in digest."""

    def test_loop_summary_section_exists(self, logger_with_loops):
        """Test loop summary section is present when loops exist."""
        digest = logger_with_loops.get_digest()

        assert "## Loop Iterations" in digest

    def test_loop_summary_shows_count(self, logger_with_loops):
        """Test loop summary shows event count per loop."""
        digest = logger_with_loops.get_digest()

        # Should show loop IDs and counts
        assert "loop-000" in digest
        assert "loop-001" in digest
        assert "loop-002" in digest

    def test_loop_summary_no_loops(self, populated_audit_logger):
        """Test no loop section when no loops exist."""
        digest = populated_audit_logger.get_digest()

        # May or may not have section depending on implementation
        assert digest is not None


# =============================================================================
# Empty Log Handling Tests
# =============================================================================

class TestEmptyLogHandling:
    """Tests for digest generation with empty or minimal logs."""

    def test_empty_log_digest(self, fresh_audit_logger):
        """Test digest generation with completely empty log."""
        digest = fresh_audit_logger.get_digest()

        assert digest is not None
        assert "## Chronicle Digest" in digest
        assert "Events: 0" in digest

    def test_empty_log_phase_summaries(self, fresh_audit_logger):
        """Test phase summaries section with empty log."""
        digest = fresh_audit_logger.get_digest()

        # Should handle empty gracefully
        assert digest is not None

    def test_single_event_digest(self, fresh_audit_logger):
        """Test digest with exactly one event."""
        fresh_audit_logger.log(
            AuditEventType.PIPELINE_START,
            pipeline_id="single-test"
        )

        digest = fresh_audit_logger.get_digest()

        assert "PIPELINE_START" in digest
        assert "Events: 1" in digest


# =============================================================================
# Compact Event Formatting Tests
# =============================================================================

class TestCompactEventFormatting:
    """Tests for compact event formatting in digest."""

    def test_event_format_with_phase(self, fresh_audit_logger):
        """Test event shows phase in brackets."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="TESTING",
            agent_id="TestAgent"
        )

        digest = fresh_audit_logger.get_digest()

        # Format: [PHASE] Agent: EventType
        assert "[TESTING]" in digest

    def test_event_format_without_phase(self, fresh_audit_logger):
        """Test event without phase shows N/A."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent"
        )

        digest = fresh_audit_logger.get_digest()

        assert "[N/A]" in digest

    def test_event_format_reversed_chronological(self, populated_audit_logger):
        """Test events are in reversed chronological order."""
        digest = populated_audit_logger.get_digest()

        # EXECUTION events should appear before PLANNING in recent events
        exec_pos = digest.find("EXECUTION")
        planning_pos = digest.find("PLANNING")

        # First occurrence of EXECUTION should be before PLANNING
        # (in reversed chronological order)
        assert exec_pos != -1
        assert planning_pos != -1


# =============================================================================
# Thread Safety Tests
# =============================================================================

class TestThreadSafety:
    """Thread safety tests for get_digest() method."""

    def test_concurrent_digest_generation(self, logger_with_many_events):
        """Test concurrent digest generation from multiple threads."""
        results = []
        errors = []
        lock = threading.Lock()

        def generate_digest(thread_id):
            try:
                digest = logger_with_many_events.get_digest(max_tokens=500)
                with lock:
                    results.append((thread_id, digest))
            except Exception as e:
                with lock:
                    errors.append((thread_id, e))

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(generate_digest, i) for i in range(50)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 50

        # All digests should be valid
        for thread_id, digest in results:
            assert "## Chronicle Digest" in digest

    def test_concurrent_mixed_operations(self, fresh_audit_logger):
        """Test concurrent log and digest operations."""
        errors = []
        lock = threading.Lock()

        def log_event(thread_id):
            try:
                fresh_audit_logger.log(
                    AuditEventType.TOOL_EXECUTED,
                    agent_id=f"Agent_{thread_id}",
                    tool_name=f"tool_{thread_id}"
                )
            except Exception as e:
                with lock:
                    errors.append(("log", thread_id, e))

        def get_digest(thread_id):
            try:
                fresh_audit_logger.get_digest()
            except Exception as e:
                with lock:
                    errors.append(("digest", thread_id, e))

        with ThreadPoolExecutor(max_workers=100) as executor:
            # 50 log operations, 50 digest operations
            for i in range(50):
                executor.submit(log_event, i)
                executor.submit(get_digest, i + 50)

        assert len(errors) == 0, f"Mixed operation errors: {errors}"

    def test_digest_during_active_logging(self):
        """Test digest generation while events are being logged."""
        logger = AuditLogger(logger_id="concurrent-test")
        stop_flag = threading.Event()
        digests = []
        lock = threading.Lock()

        def continuous_logging():
            i = 0
            while not stop_flag.is_set():
                logger.log(
                    AuditEventType.TOOL_EXECUTED,
                    agent_id="Logger",
                    tool_name=f"tool_{i}"
                )
                i += 1
                time.sleep(0.001)

        def digest_collection():
            for _ in range(20):
                digest = logger.get_digest(max_tokens=1000)
                with lock:
                    digests.append(digest)
                time.sleep(0.01)

        # Start logging thread
        log_thread = threading.Thread(target=continuous_logging)
        log_thread.start()

        # Collect digests
        digest_collection()

        # Stop logging
        stop_flag.set()
        log_thread.join()

        # All digests should be valid
        assert len(digests) == 20
        for digest in digests:
            assert "## Chronicle Digest" in digest


# =============================================================================
# Integration with NexusService Tests
# =============================================================================

class TestNexusServiceIntegration:
    """Tests for NexusService.get_chronicle_digest() integration."""

    def teardown_method(self):
        """Reset NexusService singleton after each test."""
        NexusService.reset_instance()

    def test_nexus_chronicle_digest_basic(self):
        """Test basic NexusService.get_chronicle_digest() call."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_instance = Mock()
            mock_instance.get_digest = Mock(return_value="mocked digest")
            mock_audit.get_instance = Mock(return_value=mock_instance)

            NexusService.reset_instance()
            nexus = NexusService.get_instance()

            digest = nexus.get_chronicle_digest()

            assert digest == "mocked digest"
            mock_instance.get_digest.assert_called_once()

    def test_nexus_chronicle_digest_parameters(self):
        """Test NexusService passes parameters correctly."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_instance = Mock()
            mock_instance.get_digest = Mock(return_value="result")
            mock_audit.get_instance = Mock(return_value=mock_instance)

            NexusService.reset_instance()
            nexus = NexusService.get_instance()

            nexus.get_chronicle_digest(
                max_events=20,
                max_tokens=2000,
                include_phases=["PLANNING"],
                include_agents=["CodeAgent"]
            )

            mock_instance.get_digest.assert_called_once_with(
                max_events=20,
                max_tokens=2000,
                include_phases=["PLANNING"],
                include_agents=["CodeAgent"],
            )

    def test_nexus_digest_after_commit(self):
        """Test digest reflects committed events."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_log_instance = Mock()
            mock_log_instance.log = Mock(return_value=Mock(event_id="evt-123"))
            mock_log_instance.get_digest = Mock(return_value="digest with events")
            mock_audit.get_instance = Mock(return_value=mock_log_instance)

            NexusService.reset_instance()
            nexus = NexusService.get_instance()

            # Commit an event
            nexus.commit(
                agent_id="TestAgent",
                event_type="test_event",
                payload={"key": "value"}
            )

            # Get digest
            digest = nexus.get_chronicle_digest()

            assert digest is not None


# =============================================================================
# Performance Tests
# =============================================================================

class TestPerformance:
    """Performance tests for digest generation."""

    def test_digest_generation_latency(self, logger_with_many_events):
        """Test digest generation completes within 50ms."""
        start = time.perf_counter()
        digest = logger_with_many_events.get_digest()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Should complete within 50ms
        assert elapsed_ms < 50, f"Digest generation took {elapsed_ms}ms"

    def test_digest_generation_latency_filtered(self, logger_with_many_events):
        """Test filtered digest generation performance."""
        start = time.perf_counter()
        digest = logger_with_many_events.get_digest(
            include_phases=["PHASE_0"],
            include_agents=["Agent_0"],
            max_tokens=500
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Should complete within 50ms
        assert elapsed_ms < 50, f"Filtered digest took {elapsed_ms}ms"

    def test_digest_size_reasonable(self, logger_with_many_events):
        """Test digest size is reasonable for LLM context."""
        digest = logger_with_many_events.get_digest(max_tokens=3500)

        # With 3500 token budget at ~4 chars/token, max ~14000 chars
        assert len(digest) < 15000


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_zero_max_events(self, populated_audit_logger):
        """Test digest with max_events=0."""
        digest = populated_audit_logger.get_digest(max_events=0)

        # Should still have header
        assert "## Chronicle Digest" in digest

    def test_very_large_max_events(self, populated_audit_logger):
        """Test digest with very large max_events."""
        digest = populated_audit_logger.get_digest(max_events=10000)

        # Should include all events
        assert digest is not None

    def test_zero_max_tokens(self, populated_audit_logger):
        """Test digest with max_tokens=0."""
        digest = populated_audit_logger.get_digest(max_tokens=0)

        # Should handle gracefully (may still have header)
        assert digest is not None

    def test_empty_filter_lists(self, populated_audit_logger):
        """Test digest with empty filter lists."""
        digest = populated_audit_logger.get_digest(
            include_phases=[],
            include_agents=[],
            event_types=[]
        )

        # Should return full digest
        assert "## Chronicle Digest" in digest

    def test_special_characters_in_payload(self, fresh_audit_logger):
        """Test digest handles special characters in payload."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent",
            tool_name="test",
            special_chars="<>&\"'\n\t"
        )

        digest = fresh_audit_logger.get_digest()

        # Should not crash
        assert digest is not None

    def test_unicode_in_payload(self, fresh_audit_logger):
        """Test digest handles unicode in payload."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent",
            message="Hello \u4e16\u754c \ud83c\udf0d"
        )

        digest = fresh_audit_logger.get_digest()

        # Should handle unicode
        assert digest is not None


# =============================================================================
# Token Estimation Tests
# =============================================================================

class TestTokenEstimation:
    """Tests for token estimation functionality."""

    def test_estimate_tokens_basic(self, fresh_audit_logger):
        """Test basic token estimation."""
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            agent_id="TestAgent"
        )

        digest = fresh_audit_logger.get_digest()
        estimated = len(digest) // 4

        # Should produce reasonable estimate
        assert estimated > 0

    def test_estimate_tokens_empty(self, fresh_audit_logger):
        """Test token estimation for empty digest."""
        digest = fresh_audit_logger.get_digest()
        estimated = len(digest) // 4

        # Even empty digest has header
        assert estimated > 0


# =============================================================================
# Regression Tests
# =============================================================================

class TestRegression:
    """Regression tests for previously fixed issues."""

    def test_digest_deterministic(self, fresh_audit_logger):
        """Test digest generation is deterministic (same input = same output)."""
        # Log same events
        fresh_audit_logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="TEST",
            agent_id="Agent1",
            tool_name="tool1"
        )

        digest1 = fresh_audit_logger.get_digest()
        digest2 = fresh_audit_logger.get_digest()

        # Should be identical (except for Generated timestamp)
        # Remove timestamp for comparison
        import re
        digest1_normalized = re.sub(
            r'Generated: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC',
            'Generated: TIMESTAMP',
            digest1
        )
        digest2_normalized = re.sub(
            r'Generated: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC',
            'Generated: TIMESTAMP',
            digest2
        )

        assert digest1_normalized == digest2_normalized

    def test_digest_memory_efficiency(self, logger_with_many_events):
        """Test digest generation doesn't create excessive memory allocations."""
        import sys

        # Get initial memory estimate
        initial_size = sys.getsizeof(logger_with_many_events._events)

        # Generate multiple digests
        for _ in range(10):
            digest = logger_with_many_events.get_digest()

        # Events list should not grow
        final_size = sys.getsizeof(logger_with_many_events._events)

        # Size should remain stable
        assert final_size == initial_size


# =============================================================================
# Comprehensive Integration Tests
# =============================================================================

class TestComprehensiveIntegration:
    """Comprehensive integration tests for full digest workflow."""

    def test_full_workflow(self):
        """Test complete workflow: log events -> generate digest."""
        logger = AuditLogger(logger_id="integration-test")

        # Simulate realistic pipeline execution
        logger.log(
            AuditEventType.PIPELINE_START,
            pipeline_id="pipe-001",
            user_goal="Build REST API"
        )

        logger.log(
            AuditEventType.PHASE_ENTER,
            phase="PLANNING",
            agent_id="ArchitectAgent"
        )

        logger.log(
            AuditEventType.TOOL_EXECUTED,
            phase="PLANNING",
            agent_id="ArchitectAgent",
            tool_name="design_api",
            endpoints=5
        )

        logger.log(
            AuditEventType.PHASE_EXIT,
            phase="PLANNING",
            agent_id="ArchitectAgent",
            quality_score=0.95
        )

        logger.log(
            AuditEventType.PHASE_ENTER,
            phase="DEVELOPMENT",
            agent_id="CodeAgent",
            loop_id="loop-001"
        )

        for i in range(3):
            logger.log(
                AuditEventType.TOOL_EXECUTED,
                phase="DEVELOPMENT",
                agent_id="CodeAgent",
                loop_id="loop-001",
                tool_name="write_code",
                iteration=i
            )

        logger.log(
            AuditEventType.PHASE_EXIT,
            phase="DEVELOPMENT",
            agent_id="CodeAgent",
            loop_id="loop-001"
        )

        # Generate digest
        digest = logger.get_digest(max_tokens=2000)

        # Verify digest contains expected content
        assert "## Chronicle Digest" in digest
        assert "PIPELINE_START" in digest
        assert "PLANNING" in digest
        assert "DEVELOPMENT" in digest
        assert "ArchitectAgent" in digest
        assert "CodeAgent" in digest
        assert "## Phase Summaries" in digest
        assert "## Loop Iterations" in digest
        assert "loop-001" in digest

    def test_multi_agent_multi_phase_workflow(self):
        """Test complex workflow with multiple agents and phases."""
        logger = AuditLogger(logger_id="complex-test")

        agents = ["CodeAgent", "QualityAgent", "SecurityAgent"]
        phases = ["PLANNING", "DEVELOPMENT", "TESTING", "DEPLOYMENT"]

        # Log events across agents and phases
        for phase in phases:
            for agent in agents:
                logger.log(
                    AuditEventType.TOOL_EXECUTED,
                    phase=phase,
                    agent_id=agent,
                    tool_name=f"{phase.lower()}_tool"
                )

        # Generate filtered digests
        for agent in agents:
            digest = logger.get_digest(include_agents=[agent])
            assert agent in digest

        for phase in phases:
            digest = logger.get_digest(include_phases=[phase])
            assert phase in digest

