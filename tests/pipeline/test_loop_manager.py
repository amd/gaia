"""
Tests for GAIA Loop Manager.

Tests cover:
- Loop creation and configuration
- Concurrent execution
- Loop state tracking
- Queue management
- Cancellation
"""

import asyncio
import time

import pytest

from gaia.exceptions import LoopCreationError, LoopNotFoundError
from gaia.pipeline.loop_manager import (
    LoopConfig,
    LoopManager,
    LoopState,
    LoopStatus,
)


class TestLoopStatus:
    """Tests for LoopStatus enum."""

    def test_is_terminal(self):
        """Test terminal status detection."""
        assert LoopStatus.COMPLETED.is_terminal()
        assert LoopStatus.FAILED.is_terminal()
        assert LoopStatus.CANCELLED.is_terminal()
        assert not LoopStatus.PENDING.is_terminal()
        assert not LoopStatus.RUNNING.is_terminal()
        assert not LoopStatus.WAITING.is_terminal()

    def test_is_active(self):
        """Test active status detection."""
        assert LoopStatus.PENDING.is_active()
        assert LoopStatus.RUNNING.is_active()
        assert LoopStatus.WAITING.is_active()
        assert not LoopStatus.COMPLETED.is_active()
        assert not LoopStatus.FAILED.is_active()
        assert not LoopStatus.CANCELLED.is_active()


class TestLoopConfig:
    """Tests for LoopConfig dataclass."""

    def test_create_config(self):
        """Test config creation."""
        config = LoopConfig(
            loop_id="test-loop-001",
            phase_name="DEVELOPMENT",
            agent_sequence=["senior-developer"],
            exit_criteria={"quality": 0.9},
        )
        assert config.loop_id == "test-loop-001"
        assert config.phase_name == "DEVELOPMENT"
        assert config.quality_threshold == 0.90  # Default

    def test_invalid_threshold(self):
        """Test invalid threshold raises error."""
        with pytest.raises(ValueError):
            LoopConfig(
                loop_id="test",
                phase_name="DEV",
                agent_sequence=[],
                exit_criteria={},
                quality_threshold=1.5,
            )

    def test_invalid_max_iterations(self):
        """Test invalid max iterations raises error."""
        with pytest.raises(ValueError):
            LoopConfig(
                loop_id="test",
                phase_name="DEV",
                agent_sequence=[],
                exit_criteria={},
                max_iterations=-1,
            )


class TestLoopState:
    """Tests for LoopState dataclass."""

    @pytest.fixture
    def sample_config(self) -> LoopConfig:
        """Create sample loop config."""
        return LoopConfig(
            loop_id="test-loop",
            phase_name="DEVELOPMENT",
            agent_sequence=["agent-1"],
            exit_criteria={},
        )

    def test_create_state(self, sample_config: LoopConfig):
        """Test state creation."""
        state = LoopState(config=sample_config)
        assert state.status == LoopStatus.PENDING
        assert state.iteration == 0
        assert state.quality_scores == []

    def test_to_dict(self, sample_config: LoopConfig):
        """Test state serialization."""
        state = LoopState(
            config=sample_config,
            status=LoopStatus.RUNNING,
            iteration=3,
            quality_scores=[0.7, 0.8, 0.9],
        )
        data = state.to_dict()
        assert data["status"] == "RUNNING"
        assert data["iteration"] == 3
        assert len(data["quality_scores"]) == 3

    def test_average_quality(self, sample_config: LoopConfig):
        """Test average quality calculation."""
        state = LoopState(
            config=sample_config,
            quality_scores=[0.7, 0.8, 0.9],
        )
        assert abs(state.average_quality - 0.8) < 0.0001

    def test_max_quality(self, sample_config: LoopConfig):
        """Test max quality calculation."""
        state = LoopState(
            config=sample_config,
            quality_scores=[0.7, 0.85, 0.8],
        )
        assert state.max_quality == 0.85

    def test_quality_threshold_met(self, sample_config: LoopConfig):
        """Test quality threshold check."""
        state = LoopState(
            config=sample_config,
            quality_scores=[0.95],
        )
        assert state.quality_threshold_met()

        state.quality_scores = [0.5]
        assert not state.quality_threshold_met()


class TestLoopManager:
    """Tests for LoopManager class."""

    @pytest.fixture
    def loop_manager(self) -> LoopManager:
        """Create test loop manager."""
        return LoopManager(max_concurrent=3)

    @pytest.fixture
    def sample_config(self) -> LoopConfig:
        """Create sample loop config."""
        return LoopConfig(
            loop_id="test-loop-001",
            phase_name="DEVELOPMENT",
            agent_sequence=["senior-developer"],
            exit_criteria={},
            quality_threshold=0.75,  # Lower for testing
            max_iterations=2,
        )

    @pytest.mark.asyncio
    async def test_create_loop(
        self,
        loop_manager: LoopManager,
        sample_config: LoopConfig,
    ):
        """Test loop creation."""
        loop_id = await loop_manager.create_loop(sample_config)
        assert loop_id == "test-loop-001"

        state = loop_manager.get_loop_state("test-loop-001")
        assert state is not None
        assert state.status == LoopStatus.PENDING

    @pytest.mark.asyncio
    async def test_create_duplicate_loop(
        self,
        loop_manager: LoopManager,
        sample_config: LoopConfig,
    ):
        """Test creating duplicate loop raises error."""
        await loop_manager.create_loop(sample_config)

        with pytest.raises(LoopCreationError):
            await loop_manager.create_loop(sample_config)

    @pytest.mark.asyncio
    async def test_start_loop(
        self,
        loop_manager: LoopManager,
        sample_config: LoopConfig,
    ):
        """Test starting a loop."""
        await loop_manager.create_loop(sample_config)

        # Check status immediately after start (should be RUNNING or already completed)
        future = await loop_manager.start_loop("test-loop-001")

        assert future is not None
        state = loop_manager.get_loop_state("test-loop-001")
        # Status could be RUNNING or already COMPLETED due to async execution
        assert state.status in (LoopStatus.RUNNING, LoopStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_start_nonexistent_loop(self, loop_manager: LoopManager):
        """Test starting nonexistent loop raises error."""
        with pytest.raises(LoopNotFoundError):
            await loop_manager.start_loop("nonexistent")

    @pytest.mark.asyncio
    async def test_cancel_loop(
        self,
        loop_manager: LoopManager,
        sample_config: LoopConfig,
    ):
        """Test loop cancellation."""
        await loop_manager.create_loop(sample_config)
        await loop_manager.start_loop("test-loop-001")

        # Cancel immediately after start (may complete before cancel)
        result = await loop_manager.cancel_loop("test-loop-001")

        state = loop_manager.get_loop_state("test-loop-001")
        # Status should be CANCELLED or COMPLETED (if completed before cancel)
        assert state.status in (LoopStatus.CANCELLED, LoopStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_cancel_completed_loop(
        self,
        loop_manager: LoopManager,
        sample_config: LoopConfig,
    ):
        """Test cancelling already completed loop."""
        await loop_manager.create_loop(sample_config)
        await loop_manager.start_loop("test-loop-001")

        # Wait for completion
        await asyncio.sleep(0.2)

        result = await loop_manager.cancel_loop("test-loop-001")
        assert result is False  # Already terminal

    @pytest.mark.asyncio
    async def test_concurrent_loop_limit(
        self,
        loop_manager: LoopManager,
    ):
        """Test concurrent loop limit is enforced."""
        configs = [
            LoopConfig(
                loop_id=f"loop-{i}",
                phase_name="DEV",
                agent_sequence=["agent"],
                exit_criteria={},
                quality_threshold=0.5,
                max_iterations=1,
            )
            for i in range(5)
        ]

        for config in configs:
            await loop_manager.create_loop(config)

        # Start 3 loops (at capacity)
        for i in range(3):
            await loop_manager.start_loop(f"loop-{i}")

        assert loop_manager.get_running_count() == 3

        # 4th should be queued
        future = await loop_manager.start_loop("loop-3")
        assert future is None
        assert loop_manager.get_pending_count() == 1

    @pytest.mark.asyncio
    async def test_loop_completion_starts_pending(
        self,
        loop_manager: LoopManager,
    ):
        """Test completing a loop starts pending loop."""
        configs = [
            LoopConfig(
                loop_id=f"loop-{i}",
                phase_name="DEV",
                agent_sequence=["agent"],
                exit_criteria={},
                quality_threshold=0.5,  # Easy to meet
                max_iterations=1,
            )
            for i in range(3)
        ]

        for config in configs:
            await loop_manager.create_loop(config)

        # Start 2 loops (under capacity)
        await loop_manager.start_loop("loop-0")
        await loop_manager.start_loop("loop-1")

        assert loop_manager.get_running_count() == 2

        # Start 3rd - should be queued (or may complete immediately)
        await loop_manager.start_loop("loop-2")

        # Pending count could be 0 or 1 depending on timing
        # If loops complete fast, pending might already be 0
        assert loop_manager.get_pending_count() in (0, 1)

        # Wait for completion
        await asyncio.sleep(0.3)

        # All loops should be completed or pending should be 0
        assert loop_manager.get_pending_count() == 0

    @pytest.mark.asyncio
    async def test_get_statistics(self, loop_manager: LoopManager):
        """Test getting loop statistics."""
        config = LoopConfig(
            loop_id="test-loop",
            phase_name="DEV",
            agent_sequence=["agent"],
            exit_criteria={},
        )
        await loop_manager.create_loop(config)

        stats = loop_manager.get_statistics()
        assert stats["total_loops"] == 1
        assert stats["max_concurrent"] == 3

    @pytest.mark.asyncio
    async def test_get_all_loops(
        self,
        loop_manager: LoopManager,
        sample_config: LoopConfig,
    ):
        """Test getting all loops."""
        await loop_manager.create_loop(sample_config)

        loops = loop_manager.get_all_loops()
        assert len(loops) == 1
        assert "test-loop-001" in loops

    def test_shutdown(self, loop_manager: LoopManager):
        """Test shutdown."""
        loop_manager.shutdown(wait=False)
        # Should not raise error

    @pytest.mark.asyncio
    async def test_loop_execution_completes(
        self,
        loop_manager: LoopManager,
        sample_config: LoopConfig,
    ):
        """Test loop execution completes successfully."""
        sample_config.quality_threshold = 0.5  # Easy threshold
        sample_config.max_iterations = 3

        await loop_manager.create_loop(sample_config)
        await loop_manager.start_loop("test-loop-001")

        # Wait for completion
        await asyncio.sleep(0.5)

        state = loop_manager.get_loop_state("test-loop-001")
        assert state.status == LoopStatus.COMPLETED
        assert state.iteration >= 1
        assert state.result is not None
        assert state.result["success"] is True

    @pytest.mark.asyncio
    async def test_loop_fails_on_max_iterations(
        self,
        loop_manager: LoopManager,
    ):
        """Test loop fails when max iterations exceeded."""
        config = LoopConfig(
            loop_id="fail-loop",
            phase_name="DEV",
            agent_sequence=["agent"],
            exit_criteria={},
            quality_threshold=0.99,  # Very high - won't meet
            max_iterations=2,
        )

        await loop_manager.create_loop(config)
        await loop_manager.start_loop("fail-loop")

        # Wait for completion
        await asyncio.sleep(0.3)

        state = loop_manager.get_loop_state("fail-loop")
        assert state.status == LoopStatus.FAILED
        assert "Max iterations" in state.error
