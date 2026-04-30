# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MED-4 regression: checkpoint resolution is workflow-bound.

A caller must not be able to resolve checkpoint ``A`` under an arbitrary
workflow_id ``B`` and have a receipt issued under workflow B. The
adapter validates the checkpoint's stored workflow against the
caller-supplied workflow_id before resolving.
"""

from __future__ import annotations

import threading
import time

import pytest

from gaia.governance import (
    CheckpointResolution,
    GaiaGovernanceAdapter,
    InvalidResolutionError,
    WorkflowTransition,
)
from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.policy_binding import StaticPolicyBindingService
from gaia.governance.receipt_service import InMemoryReceiptService
from gaia.governance.schemas import ActionRequest
from gaia.governance.stubs import RuleBasedPolicyEngine


def _make():
    receipts = InMemoryReceiptService()
    bridge = InMemoryCheckpointBridge()
    adapter = GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(),
        checkpoint_runtime=bridge,
        receipt_service=receipts,
        policy_binding=StaticPolicyBindingService(),
    )
    return adapter, receipts, bridge


def _review_action(workflow_id: str) -> ActionRequest:
    return ActionRequest(
        action_id="a1",
        actor_id="actor",
        tool_name="t",
        action_type="t",
        args={},
        risk_tags=["review"],
        workflow_id=workflow_id,
    )


def _transition(workflow_id: str) -> WorkflowTransition:
    return WorkflowTransition(
        workflow_id=workflow_id,
        transition_id="tx1",
        from_state="S",
        to_state="R",
        transition_type="tool_call",
        related_action_id="a1",
    )


def test_resolve_with_mismatched_workflow_id_is_rejected():
    adapter, _, _ = _make()
    opened = adapter.handle_transition(
        _transition("wf_A"),
        adapter.govern_action(_review_action("wf_A")),
    )
    with pytest.raises(InvalidResolutionError):
        adapter.resolve_checkpoint(
            opened.checkpoint_id,
            CheckpointResolution(resolution="APPROVE", actor_id="mallory"),
            workflow_id="wf_B",
        )


def test_resolve_with_correct_workflow_id_succeeds():
    adapter, _, _ = _make()
    opened = adapter.handle_transition(
        _transition("wf_A"),
        adapter.govern_action(_review_action("wf_A")),
    )
    outcome = adapter.resolve_checkpoint(
        opened.checkpoint_id,
        CheckpointResolution(resolution="APPROVE", actor_id="alice"),
        workflow_id="wf_A",
    )
    assert outcome.status == "RESUMED"


def test_concurrent_double_resolution_only_one_wins():
    # MED-5 regression: the checkpoint bridge uses a lock so only one
    # of two concurrent resolutions produces a terminal outcome; the
    # other raises InvalidResolutionError.
    adapter, _, _ = _make()
    opened = adapter.handle_transition(
        _transition("wf_race"),
        adapter.govern_action(_review_action("wf_race")),
    )

    outcomes: list = []
    errors: list = []

    def attempt(tag: str):
        try:
            outcomes.append(
                adapter.resolve_checkpoint(
                    opened.checkpoint_id,
                    CheckpointResolution(resolution="APPROVE", actor_id=tag),
                    workflow_id="wf_race",
                )
            )
        except InvalidResolutionError as exc:  # expected for loser
            errors.append(exc)

    t1 = threading.Thread(target=attempt, args=("t1",))
    t2 = threading.Thread(target=attempt, args=("t2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # exactly one success, one InvalidResolutionError
    assert len(outcomes) == 1
    assert len(errors) == 1
    # keep timing-sensitive assertions robust on slow machines
    _ = time  # silence unused-import when we don't need a sleep path
