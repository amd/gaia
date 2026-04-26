# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SSE Hooks for PipelineEngine

Hook classes that emit pipeline events (phase transitions, quality evaluations,
decisions, defects, loop boundaries) to an SSE handler's event queue.

These hooks are registered in PipelineEngine.initialize() when an sse_handler
is present in the config dict (e.g., from the UI router).
"""

import logging
from typing import Any, Dict

from gaia.hooks.base import BaseHook, HookContext, HookResult, HookPriority

logger = logging.getLogger(__name__)


def _emit_sse_event(handler, event_type: str, data: Dict[str, Any]) -> None:
    """Emit an SSE event through the handler's event queue."""
    try:
        event = {"type": event_type, **data}
        handler.event_queue.put(event)
    except Exception as e:
        logger.warning(f"Failed to emit SSE event {event_type}: {e}")


class PhaseTransitionSSEHook(BaseHook):
    """
    Emit SSE events on PHASE_ENTER and PHASE_EXIT.

    Produces 'status' events with phase info so the frontend can show
    which pipeline phase is currently executing.
    """

    name = "sse_phase_transition"
    event = "*"  # Listen to all events, filter internally
    priority = HookPriority.LOW
    blocking = False
    description = "Emit phase transition events to SSE stream"

    async def execute(self, context: HookContext) -> HookResult:
        handler = self.config.get("sse_handler")
        if not handler:
            return HookResult(success=True)

        event_name = context.event
        phase = context.phase

        if event_name == "PHASE_ENTER" and phase:
            _emit_sse_event(handler, "status", {
                "status": "running",
                "message": f"Entering {phase} phase",
                "current_phase": phase,
            })
        elif event_name == "PHASE_EXIT" and phase:
            success = context.data.get("success", True)
            _emit_sse_event(handler, "status", {
                "status": "running",
                "message": f"Completed {phase} phase" + (" successfully" if success else " with issues"),
                "current_phase": phase,
            })

        return HookResult(success=True)


class QualityEvalSSEHook(BaseHook):
    """
    Emit SSE events when quality is evaluated.

    Produces 'quality_score' events with the score and threshold info.
    Triggered on PHASE_EXIT from the QUALITY phase where the quality score
    is stored in the state dict.
    """

    name = "sse_quality_eval"
    event = "*"
    priority = HookPriority.LOW
    blocking = False
    description = "Emit quality evaluation events to SSE stream"

    async def execute(self, context: HookContext) -> HookResult:
        handler = self.config.get("sse_handler")
        if not handler:
            return HookResult(success=True)

        if context.event == "PHASE_EXIT" and context.phase == "QUALITY":
            state = context.state
            quality_score = state.get("quality_score")
            if quality_score is not None:
                _emit_sse_event(handler, "quality_score", {
                    "quality_score": quality_score,
                    "message": f"Quality score: {quality_score:.2f}",
                })

        return HookResult(success=True)


class DecisionSSEHook(BaseHook):
    """
    Emit SSE events when a decision is made.

    Produces 'status' events with decision type and reason so the frontend
    can show whether the pipeline is continuing, looping back, or failing.
    """

    name = "sse_decision"
    event = "*"
    priority = HookPriority.LOW
    blocking = False
    description = "Emit decision events to SSE stream"

    async def execute(self, context: HookContext) -> HookResult:
        handler = self.config.get("sse_handler")
        if not handler:
            return HookResult(success=True)

        if context.event == "PHASE_EXIT" and context.phase == "DECISION":
            artifacts = context.state.get("artifacts", {})
            decision_artifact = artifacts.get("decision") if isinstance(artifacts, dict) else None
            if isinstance(decision_artifact, dict):
                decision_type = decision_artifact.get("decision_type", "UNKNOWN")
                reason = decision_artifact.get("reason", "")
                _emit_sse_event(handler, "status", {
                    "status": "running",
                    "message": f"Decision: {decision_type} - {reason}",
                    "decision_type": decision_type,
                    "reason": reason,
                })
                # Also emit loop_back event if applicable
                if decision_type == "LOOP_BACK":
                    target_phase = decision_artifact.get("target_phase", "PLANNING")
                    iteration = context.state.get("iteration_count", 1)
                    _emit_sse_event(handler, "loop_back", {
                        "target_phase": target_phase,
                        "iteration": iteration,
                        "message": f"Looping back to {target_phase} (iteration {iteration})",
                    })

        return HookResult(success=True)


class DefectSSEHook(BaseHook):
    """
    Emit SSE events when defects are detected.

    Produces 'defect_found' events with defect details from the
    state machine snapshot.
    """

    name = "sse_defect"
    event = "*"
    priority = HookPriority.LOW
    blocking = False
    description = "Emit defect events to SSE stream"

    async def execute(self, context: HookContext) -> HookResult:
        handler = self.config.get("sse_handler")
        if not handler:
            return HookResult(success=True)

        if context.event == "PHASE_EXIT" and context.phase == "DECISION":
            defects = context.state.get("defects", [])
            if defects:
                for defect in defects:
                    if isinstance(defect, dict):
                        _emit_sse_event(handler, "defect_found", {
                            "defects": [{
                                "type": defect.get("type", defect.get("category", "unknown")),
                                "severity": defect.get("severity", "medium"),
                                "description": defect.get("description", str(defect)),
                            }],
                            "message": f"Defect found: {defect.get('description', 'unknown')}",
                        })

        return HookResult(success=True)


class LoopSSEHook(BaseHook):
    """
    Emit SSE events for loop/iteration boundaries.

    Produces 'iteration_start' and 'iteration_end' events based on
    iteration count changes in the hook context.
    """

    name = "sse_loop"
    event = "*"
    priority = HookPriority.LOW
    blocking = False
    description = "Emit loop/iteration events to SSE stream"

    async def execute(self, context: HookContext) -> HookResult:
        handler = self.config.get("sse_handler")
        if not handler:
            return HookResult(success=True)

        if context.event == "PHASE_ENTER" and context.phase == "PLANNING":
            # Emit iteration start at the beginning of each planning phase entry
            iteration = context.state.get("iteration_count", 1) + 1
            _emit_sse_event(handler, "iteration_start", {
                "iteration": iteration,
                "message": f"Starting iteration {iteration}",
            })

        return HookResult(success=True)


def create_sse_hook_group(sse_handler) -> list:
    """
    Create a list of SSE hooks configured with the given handler.

    Args:
        sse_handler: An _PipelineSSEHandler instance with event_queue attribute

    Returns:
        List of hook instances ready for registration
    """
    config = {"sse_handler": sse_handler}
    return [
        PhaseTransitionSSEHook(config=config),
        QualityEvalSSEHook(config=config),
        DecisionSSEHook(config=config),
        DefectSSEHook(config=config),
        LoopSSEHook(config=config),
    ]
