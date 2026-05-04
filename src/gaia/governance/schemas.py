# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Data classes shared across the governance layer.

Ported from the gaia-acgs starter scaffold; these types are intentionally
framework-agnostic dataclasses so they can be exchanged with ACGS-lite
and constitutional-swarm without importing GAIA runtime symbols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

DecisionType = Literal["ALLOW", "REVIEW", "BLOCK"]
CheckpointStatus = Literal[
    "OPEN", "APPROVED", "REJECTED", "ESCALATED", "TIMEOUT_REJECTED"
]
TransitionStatus = Literal["CONTINUE", "CHECKPOINT_OPEN", "TERMINATED", "RESUMED"]
ResolutionType = Literal["APPROVE", "REJECT", "ESCALATE", "TIMEOUT_REJECT"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class ActionRequest:
    action_id: str
    actor_id: str
    tool_name: str
    action_type: str
    args: dict[str, Any]
    risk_tags: list[str] = field(default_factory=list)
    workflow_id: str | None = None
    step_id: str | None = None
    source: str = "gaia"


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    decision: DecisionType
    reason: str
    policy_version: str
    rule_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowTransition:
    workflow_id: str
    transition_id: str
    from_state: str
    to_state: str
    transition_type: str
    related_action_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    checkpoint_id: str
    workflow_id: str
    transition_id: str
    status: CheckpointStatus
    created_at: str
    decision_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CheckpointResolution:
    resolution: ResolutionType
    actor_id: str
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    status: TransitionStatus
    reason: str
    checkpoint_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    receipt_id: str
    workflow_id: str
    checkpoint_id: str | None
    decision: str
    policy_version: str
    actor_id: str | None
    validator_set_id: str | None
    created_at: str
    payload_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PolicyVersionRef:
    version: str
    constitution_hash: str
    activated_at: str
