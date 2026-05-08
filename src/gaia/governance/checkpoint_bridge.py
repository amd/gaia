# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""In-memory CheckpointRuntime reference implementation.

Production deployments will swap this for a persistent bridge backed by
constitutional-swarm. Kept tiny so unit tests and the governed example
can run with no external dependencies.
"""

from __future__ import annotations

from threading import Lock

from .exceptions import CheckpointNotFoundError, InvalidResolutionError
from .schemas import (
    CheckpointRecord,
    CheckpointResolution,
    GovernanceDecision,
    TransitionOutcome,
    WorkflowTransition,
    new_id,
    utc_now_iso,
)


class InMemoryCheckpointBridge:
    def __init__(self) -> None:
        self._records: dict[str, CheckpointRecord] = {}
        self._lock = Lock()

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Return the stored checkpoint or ``None`` — used by the adapter
        to validate workflow ownership before resolution."""
        with self._lock:
            return self._records.get(checkpoint_id)

    def create_checkpoint(
        self, transition: WorkflowTransition, decision: GovernanceDecision
    ) -> CheckpointRecord:
        record = CheckpointRecord(
            checkpoint_id=new_id("chk"),
            workflow_id=transition.workflow_id,
            transition_id=transition.transition_id,
            status="OPEN",
            created_at=utc_now_iso(),
            decision_context={
                "transition_type": transition.transition_type,
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "decision_reason": decision.reason,
                "policy_version": decision.policy_version,
                "rule_ids": list(decision.rule_ids),
            },
        )
        with self._lock:
            self._records[record.checkpoint_id] = record
        return record

    def resolve_checkpoint(
        self, checkpoint_id: str, resolution: CheckpointResolution
    ) -> TransitionOutcome:
        # MED-5 fix: check-and-set must be atomic so a concurrent second
        # caller sees the terminal status and raises InvalidResolutionError
        # instead of also succeeding.
        with self._lock:
            if checkpoint_id not in self._records:
                raise CheckpointNotFoundError(checkpoint_id)

            current = self._records[checkpoint_id]
            if current.status != "OPEN":
                raise InvalidResolutionError(f"checkpoint is not open: {checkpoint_id}")

            mapping = {
                "APPROVE": ("APPROVED", "RESUMED", "checkpoint approved"),
                "REJECT": ("REJECTED", "TERMINATED", "checkpoint rejected"),
                "ESCALATE": ("ESCALATED", "CHECKPOINT_OPEN", "checkpoint escalated"),
                "TIMEOUT_REJECT": (
                    "TIMEOUT_REJECTED",
                    "TERMINATED",
                    "checkpoint timed out",
                ),
            }
            entry = mapping.get(resolution.resolution)
            if entry is None:
                raise InvalidResolutionError(
                    f"unknown resolution type: {resolution.resolution!r}"
                )
            status, outcome_status, reason = entry
            self._records[checkpoint_id] = CheckpointRecord(
                checkpoint_id=current.checkpoint_id,
                workflow_id=current.workflow_id,
                transition_id=current.transition_id,
                status=status,
                created_at=current.created_at,
                decision_context={
                    **current.decision_context,
                    "resolved_by": resolution.actor_id,
                    "resolution_reason": resolution.reason,
                    "resolution_metadata": resolution.metadata,
                },
            )
            return TransitionOutcome(
                status=outcome_status,
                reason=reason,
                checkpoint_id=checkpoint_id,
                metadata={"resolution": resolution.resolution},
            )
