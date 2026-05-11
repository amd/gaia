# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Runtime-checkable protocol contracts for governance services.

Keeping these as Protocols (not ABCs) lets downstream implementations
live in ACGS-lite, constitutional-swarm, or GAIA itself without forcing
an inheritance relationship.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import (
    ActionRequest,
    CheckpointRecord,
    CheckpointResolution,
    GovernanceDecision,
    PolicyVersionRef,
    ReceiptRecord,
    TransitionOutcome,
    WorkflowTransition,
)


@runtime_checkable
class PolicyEngine(Protocol):
    def evaluate_action(self, action_request: ActionRequest) -> GovernanceDecision: ...


@runtime_checkable
class CheckpointRuntime(Protocol):
    def create_checkpoint(
        self, transition: WorkflowTransition, decision: GovernanceDecision
    ) -> CheckpointRecord: ...

    def resolve_checkpoint(
        self, checkpoint_id: str, resolution: CheckpointResolution
    ) -> TransitionOutcome: ...


@runtime_checkable
class ReceiptServiceProtocol(Protocol):
    def issue_receipt(self, record: ReceiptRecord) -> str: ...

    def get_receipt(self, receipt_id: str) -> ReceiptRecord: ...


@runtime_checkable
class PolicyBindingProtocol(Protocol):
    def current_version(self) -> PolicyVersionRef: ...
