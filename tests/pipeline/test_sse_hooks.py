# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for SSE hooks in gaia.pipeline.sse_hooks.

Tests cover:
- PhaseTransitionSSEHook emits on PHASE_ENTER
- QualityEvalSSEHook emits quality_score on QUALITY phase exit
- DecisionSSEHook emits loop_back on LOOP_BACK decision
- DefectSSEHook emits defect_found when defects exist
- LoopSSEHook emits iteration_start when iteration increases
- Hooks don't crash when handler is unavailable (error handling)
"""

import asyncio
import pytest
from unittest.mock import Mock, patch, MagicMock

from gaia.hooks.base import HookContext, HookResult
from gaia.pipeline.sse_hooks import (
    PhaseTransitionSSEHook,
    QualityEvalSSEHook,
    DecisionSSEHook,
    DefectSSEHook,
    LoopSSEHook,
    create_sse_hook_group,
    _emit_sse_event,
)


def _make_handler():
    """Create a mock SSE handler with an event queue."""
    import queue
    handler = Mock()
    handler.event_queue = queue.Queue()
    return handler


def _make_context(event: str, phase: str = None, state: dict = None, data: dict = None, pipeline_id: str = "test-001"):
    """Create a HookContext with the given parameters."""
    return HookContext(
        event=event,
        pipeline_id=pipeline_id,
        phase=phase,
        state=state or {},
        data=data or {},
    )


class TestEmitSseEventHelper:
    """Tests for the _emit_sse_event helper function."""

    def test_emit_sse_event_puts_event_in_queue(self):
        """_emit_sse_event should put a formatted event dict into the handler's queue."""
        import queue
        handler = Mock()
        handler.event_queue = queue.Queue()

        _emit_sse_event(handler, "test_type", {"key": "value"})

        assert not handler.event_queue.empty()
        event = handler.event_queue.get_nowait()
        assert event == {"type": "test_type", "key": "value"}

    def test_emit_sse_event_handles_queue_failure(self):
        """_emit_sse_event should not crash if queue.put raises."""
        handler = Mock()
        handler.event_queue.put = Mock(side_effect=Exception("queue full"))

        # Should not raise
        _emit_sse_event(handler, "test_type", {"key": "value"})


class TestPhaseTransitionSSEHook:
    """Tests for PhaseTransitionSSEHook."""

    @pytest.mark.asyncio
    async def test_emits_on_phase_enter(self):
        """PhaseTransitionSSEHook should emit status event on PHASE_ENTER."""
        handler = _make_handler()
        hook = PhaseTransitionSSEHook(config={"sse_handler": handler})

        context = _make_context(event="PHASE_ENTER", phase="PLANNING")
        result = await hook.execute(context)

        assert result.success is True
        assert not handler.event_queue.empty()

        event = handler.event_queue.get_nowait()
        assert event["type"] == "status"
        assert event["status"] == "running"
        assert "PLANNING" in event["message"]
        assert event["current_phase"] == "PLANNING"

    @pytest.mark.asyncio
    async def test_emits_on_phase_exit(self):
        """PhaseTransitionSSEHook should emit status event on PHASE_EXIT."""
        handler = _make_handler()
        hook = PhaseTransitionSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="PLANNING",
            data={"success": True},
        )
        result = await hook.execute(context)

        assert result.success is True
        assert not handler.event_queue.empty()

        event = handler.event_queue.get_nowait()
        assert event["type"] == "status"
        assert "Completed PLANNING phase" in event["message"]
        assert "successfully" in event["message"]

    @pytest.mark.asyncio
    async def test_phase_exit_with_issues(self):
        """Phase exit with success=False should indicate issues."""
        handler = _make_handler()
        hook = PhaseTransitionSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="PLANNING",
            data={"success": False},
        )
        await hook.execute(context)

        event = handler.event_queue.get_nowait()
        assert "with issues" in event["message"]

    @pytest.mark.asyncio
    async def test_ignores_non_phase_events(self):
        """PhaseTransitionSSEHook should not emit for non-phase events."""
        handler = _make_handler()
        hook = PhaseTransitionSSEHook(config={"sse_handler": handler})

        context = _make_context(event="PIPELINE_START")
        result = await hook.execute(context)

        assert result.success is True
        assert handler.event_queue.empty()

    @pytest.mark.asyncio
    async def test_no_emit_when_phase_is_none(self):
        """PhaseTransitionSSEHook should not emit when phase is None."""
        handler = _make_handler()
        hook = PhaseTransitionSSEHook(config={"sse_handler": handler})

        context = _make_context(event="PHASE_ENTER", phase=None)
        await hook.execute(context)

        assert handler.event_queue.empty()


class TestQualityEvalSSEHook:
    """Tests for QualityEvalSSEHook."""

    @pytest.mark.asyncio
    async def test_emits_quality_score_on_quality_phase_exit(self):
        """QualityEvalSSEHook should emit quality_score on QUALITY phase exit."""
        handler = _make_handler()
        hook = QualityEvalSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="QUALITY",
            state={"quality_score": 0.87},
        )
        result = await hook.execute(context)

        assert result.success is True
        assert not handler.event_queue.empty()

        event = handler.event_queue.get_nowait()
        assert event["type"] == "quality_score"
        assert event["quality_score"] == 0.87
        assert "0.87" in event["message"]

    @pytest.mark.asyncio
    async def test_no_emit_when_quality_score_is_none(self):
        """QualityEvalSSEHook should not emit when quality_score is None."""
        handler = _make_handler()
        hook = QualityEvalSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="QUALITY",
            state={"quality_score": None},
        )
        await hook.execute(context)

        assert handler.event_queue.empty()

    @pytest.mark.asyncio
    async def test_no_emit_for_non_quality_phase(self):
        """QualityEvalSSEHook should not emit for non-QUALITY phase exits."""
        handler = _make_handler()
        hook = QualityEvalSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="PLANNING",
            state={"quality_score": 0.95},
        )
        await hook.execute(context)

        assert handler.event_queue.empty()

    @pytest.mark.asyncio
    async def test_no_emit_for_non_exit_events(self):
        """QualityEvalSSEHook should not emit for PHASE_ENTER events."""
        handler = _make_handler()
        hook = QualityEvalSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_ENTER",
            phase="QUALITY",
            state={"quality_score": 0.95},
        )
        await hook.execute(context)

        assert handler.event_queue.empty()


class TestDecisionSSEHook:
    """Tests for DecisionSSEHook."""

    @pytest.mark.asyncio
    async def test_emits_decision_status_on_decision_exit(self):
        """DecisionSSEHook should emit status on DECISION phase exit."""
        handler = _make_handler()
        hook = DecisionSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={
                "artifacts": {
                    "decision": {
                        "decision_type": "PROCEED",
                        "reason": "Quality threshold met",
                    }
                }
            },
        )
        result = await hook.execute(context)

        assert result.success is True
        assert not handler.event_queue.empty()

        event = handler.event_queue.get_nowait()
        assert event["type"] == "status"
        assert event["decision_type"] == "PROCEED"

    @pytest.mark.asyncio
    async def test_emits_loop_back_on_loop_back_decision(self):
        """DecisionSSEHook should emit loop_back event for LOOP_BACK decisions."""
        handler = _make_handler()
        hook = DecisionSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={
                "artifacts": {
                    "decision": {
                        "decision_type": "LOOP_BACK",
                        "reason": "Quality below threshold",
                        "target_phase": "PLANNING",
                    }
                },
                "iteration_count": 2,
            },
        )
        await hook.execute(context)

        # Should have two events: status + loop_back
        assert handler.event_queue.qsize() == 2

        status_event = handler.event_queue.get_nowait()
        assert status_event["type"] == "status"
        assert status_event["decision_type"] == "LOOP_BACK"

        loop_event = handler.event_queue.get_nowait()
        assert loop_event["type"] == "loop_back"
        assert loop_event["target_phase"] == "PLANNING"
        assert loop_event["iteration"] == 2

    @pytest.mark.asyncio
    async def test_no_emit_when_no_decision_artifact(self):
        """DecisionSSEHook should not emit when no decision artifact exists."""
        handler = _make_handler()
        hook = DecisionSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={"artifacts": {}},
        )
        await hook.execute(context)

        assert handler.event_queue.empty()

    @pytest.mark.asyncio
    async def test_no_emit_when_artifacts_not_dict(self):
        """DecisionSSEHook should handle non-dict artifacts gracefully."""
        handler = _make_handler()
        hook = DecisionSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={"artifacts": "not_a_dict"},
        )
        await hook.execute(context)

        assert handler.event_queue.empty()


class TestDefectSSEHook:
    """Tests for DefectSSEHook."""

    @pytest.mark.asyncio
    async def test_emits_defect_found_when_defects_exist(self):
        """DefectSSEHook should emit defect_found for each defect."""
        handler = _make_handler()
        hook = DefectSSEHook(config={"sse_handler": handler})

        defects = [
            {"type": "syntax_error", "severity": "high", "description": "Missing semicolon"},
            {"type": "logic_error", "severity": "medium", "description": "Off-by-one"},
        ]
        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={"defects": defects},
        )
        result = await hook.execute(context)

        assert result.success is True
        assert handler.event_queue.qsize() == 2

        event1 = handler.event_queue.get_nowait()
        assert event1["type"] == "defect_found"
        assert event1["defects"][0]["type"] == "syntax_error"
        assert event1["defects"][0]["severity"] == "high"

        event2 = handler.event_queue.get_nowait()
        assert event2["type"] == "defect_found"
        assert event2["defects"][0]["type"] == "logic_error"

    @pytest.mark.asyncio
    async def test_no_emit_when_no_defects(self):
        """DefectSSEHook should not emit when defects list is empty."""
        handler = _make_handler()
        hook = DefectSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={"defects": []},
        )
        await hook.execute(context)

        assert handler.event_queue.empty()

    @pytest.mark.asyncio
    async def test_uses_category_fallback_when_type_missing(self):
        """DefectSSEHook should use 'category' field when 'type' is missing."""
        handler = _make_handler()
        hook = DefectSSEHook(config={"sse_handler": handler})

        defects = [{"category": "style_violation", "severity": "low", "description": "Bad formatting"}]
        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={"defects": defects},
        )
        await hook.execute(context)

        event = handler.event_queue.get_nowait()
        assert event["defects"][0]["type"] == "style_violation"

    @pytest.mark.asyncio
    async def test_uses_string_description_fallback(self):
        """DefectSSEHook should handle non-dict defects gracefully."""
        handler = _make_handler()
        hook = DefectSSEHook(config={"sse_handler": handler})

        # Non-dict defects are skipped by the isinstance check
        defects = ["string_defect", {"type": "real", "severity": "high", "description": "real"}]
        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={"defects": defects},
        )
        await hook.execute(context)

        # Only the dict defect should be emitted
        assert handler.event_queue.qsize() == 1


class TestLoopSSEHook:
    """Tests for LoopSSEHook."""

    @pytest.mark.asyncio
    async def test_emits_iteration_start_on_planning_enter(self):
        """LoopSSEHook should emit iteration_start on PLANNING phase enter."""
        handler = _make_handler()
        hook = LoopSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_ENTER",
            phase="PLANNING",
            state={"iteration_count": 1},
        )
        result = await hook.execute(context)

        assert result.success is True
        assert not handler.event_queue.empty()

        event = handler.event_queue.get_nowait()
        assert event["type"] == "iteration_start"
        assert event["iteration"] == 2  # iteration_count + 1

    @pytest.mark.asyncio
    async def test_no_emit_for_non_planning_enter(self):
        """LoopSSEHook should not emit for non-PLANNING phase enters."""
        handler = _make_handler()
        hook = LoopSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_ENTER",
            phase="QUALITY",
            state={"iteration_count": 1},
        )
        await hook.execute(context)

        assert handler.event_queue.empty()

    @pytest.mark.asyncio
    async def test_no_emit_for_phase_exit(self):
        """LoopSSEHook should not emit for phase exit events."""
        handler = _make_handler()
        hook = LoopSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_EXIT",
            phase="PLANNING",
            state={"iteration_count": 1},
        )
        await hook.execute(context)

        assert handler.event_queue.empty()

    @pytest.mark.asyncio
    async def test_default_iteration_when_missing(self):
        """LoopSSEHook should use default iteration_count of 1 when missing."""
        handler = _make_handler()
        hook = LoopSSEHook(config={"sse_handler": handler})

        context = _make_context(
            event="PHASE_ENTER",
            phase="PLANNING",
            state={},  # no iteration_count
        )
        await hook.execute(context)

        event = handler.event_queue.get_nowait()
        assert event["iteration"] == 2  # default 1 + 1


class TestErrorHandlerHandling:
    """Tests that hooks don't crash when handler is unavailable."""

    @pytest.mark.asyncio
    async def test_phase_transition_hook_no_handler(self):
        """PhaseTransitionSSEHook should succeed when no handler configured."""
        hook = PhaseTransitionSSEHook(config={})
        context = _make_context(event="PHASE_ENTER", phase="PLANNING")

        result = await hook.execute(context)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_quality_eval_hook_no_handler(self):
        """QualityEvalSSEHook should succeed when no handler configured."""
        hook = QualityEvalSSEHook(config={})
        context = _make_context(
            event="PHASE_EXIT",
            phase="QUALITY",
            state={"quality_score": 0.95},
        )

        result = await hook.execute(context)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_decision_hook_no_handler(self):
        """DecisionSSEHook should succeed when no handler configured."""
        hook = DecisionSSEHook(config={})
        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={
                "artifacts": {
                    "decision": {"decision_type": "PROCEED", "reason": "ok"}
                }
            },
        )

        result = await hook.execute(context)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_defect_hook_no_handler(self):
        """DefectSSEHook should succeed when no handler configured."""
        hook = DefectSSEHook(config={})
        context = _make_context(
            event="PHASE_EXIT",
            phase="DECISION",
            state={"defects": [{"type": "error", "severity": "high", "description": "test"}]},
        )

        result = await hook.execute(context)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_loop_hook_no_handler(self):
        """LoopSSEHook should succeed when no handler configured."""
        hook = LoopSSEHook(config={})
        context = _make_context(
            event="PHASE_ENTER",
            phase="PLANNING",
            state={"iteration_count": 1},
        )

        result = await hook.execute(context)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_all_hooks_handle_sse_handler_none(self):
        """All hooks should succeed when sse_handler is explicitly None."""
        hooks = [
            PhaseTransitionSSEHook(config={"sse_handler": None}),
            QualityEvalSSEHook(config={"sse_handler": None}),
            DecisionSSEHook(config={"sse_handler": None}),
            DefectSSEHook(config={"sse_handler": None}),
            LoopSSEHook(config={"sse_handler": None}),
        ]
        context = _make_context(event="PHASE_ENTER", phase="PLANNING")

        for hook in hooks:
            result = await hook.execute(context)
            assert result.success is True, f"{hook.name} failed with None handler"


class TestCreateSseHookGroup:
    """Tests for the create_sse_hook_group factory function."""

    def test_creates_all_five_hooks(self):
        """create_sse_hook_group should return 5 hooks."""
        handler = _make_handler()
        hooks = create_sse_hook_group(handler)

        assert len(hooks) == 5

    def test_all_hooks_configured_with_handler(self):
        """All hooks should have the sse_handler in their config."""
        handler = _make_handler()
        hooks = create_sse_hook_group(handler)

        for hook in hooks:
            assert hook.config.get("sse_handler") is handler

    def test_hook_types(self):
        """Verify each hook type is present."""
        handler = _make_handler()
        hooks = create_sse_hook_group(handler)

        hook_names = {h.name for h in hooks}
        expected = {
            "sse_phase_transition",
            "sse_quality_eval",
            "sse_decision",
            "sse_defect",
            "sse_loop",
        }
        assert hook_names == expected
