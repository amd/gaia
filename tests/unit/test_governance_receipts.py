# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for receipt issuance and policy-version binding."""

from __future__ import annotations

import pytest

from gaia.governance import (
    CheckpointResolution,
    GaiaGovernanceAdapter,
    GaiaGovernanceError,
    WorkflowTransition,
)
from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.policy_binding import StaticPolicyBindingService
from gaia.governance.receipt_service import InMemoryReceiptService
from gaia.governance.schemas import ActionRequest, ReceiptRecord
from gaia.governance.stubs import RuleBasedPolicyEngine


def _make_adapter(policy_version: str = "v0", constitution_hash: str = "c1"):
    receipts = InMemoryReceiptService()
    binding = StaticPolicyBindingService(
        version=policy_version, constitution_hash=constitution_hash
    )
    adapter = GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(policy_version=policy_version),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=receipts,
        policy_binding=binding,
    )
    return adapter, receipts


def _action(tool: str, tags: list[str]) -> ActionRequest:
    return ActionRequest(
        action_id="a1",
        actor_id="actor",
        tool_name=tool,
        action_type=tool,
        args={},
        risk_tags=tags,
        workflow_id="wf_rcpt",
    )


def _transition() -> WorkflowTransition:
    return WorkflowTransition(
        workflow_id="wf_rcpt",
        transition_id="t1",
        from_state="S",
        to_state="R",
        transition_type="tool_call",
        related_action_id="a1",
    )


def test_block_decision_persists_receipt_with_policy_binding():
    adapter, receipts = _make_adapter(policy_version="v0", constitution_hash="c_hash")
    decision = adapter.govern_action(_action("bad", ["blocked"]))
    outcome = adapter.handle_transition(_transition(), decision)
    receipt_id = outcome.metadata["receipt_id"]
    record = receipts.get_receipt(receipt_id)
    assert isinstance(record, ReceiptRecord)
    assert record.policy_version == "v0"
    assert record.metadata["constitution_hash"] == "c_hash"
    assert record.decision == "BLOCK"


def test_resolve_checkpoint_records_receipt_for_approved_review():
    adapter, receipts = _make_adapter()
    opened = adapter.handle_transition(
        _transition(),
        adapter.govern_action(_action("needs_review", ["review"])),
    )
    outcome = adapter.resolve_checkpoint(
        opened.checkpoint_id,
        CheckpointResolution(
            resolution="APPROVE", actor_id="reviewer", reason="looks good"
        ),
        workflow_id="wf_rcpt",
    )
    record = receipts.get_receipt(outcome.metadata["receipt_id"])
    assert record.decision == "APPROVE"
    assert record.checkpoint_id == opened.checkpoint_id
    assert record.actor_id == "reviewer"


def test_payload_hash_differs_per_receipt_because_envelope_is_unique():
    # The payload_hash covers the full evidence envelope including
    # receipt_id and created_at, so two logically-identical decisions
    # produce distinct hashes. Tamper-evidence, not de-duplication.
    adapter, receipts = _make_adapter()
    a = adapter.handle_transition(
        _transition(),
        adapter.govern_action(_action("bad", ["blocked"])),
    )
    b = adapter.handle_transition(
        _transition(),
        adapter.govern_action(_action("bad", ["blocked"])),
    )
    ra = receipts.get_receipt(a.metadata["receipt_id"])
    rb = receipts.get_receipt(b.metadata["receipt_id"])
    assert ra.payload_hash != rb.payload_hash
    assert ra.receipt_id != rb.receipt_id


def test_payload_hash_changes_when_policy_version_changes():
    adapter_v0, receipts_v0 = _make_adapter(policy_version="v0")
    adapter_v1, receipts_v1 = _make_adapter(policy_version="v1")
    outcome_v0 = adapter_v0.handle_transition(
        _transition(),
        adapter_v0.govern_action(_action("bad", ["blocked"])),
    )
    outcome_v1 = adapter_v1.handle_transition(
        _transition(),
        adapter_v1.govern_action(_action("bad", ["blocked"])),
    )
    r0 = receipts_v0.get_receipt(outcome_v0.metadata["receipt_id"])
    r1 = receipts_v1.get_receipt(outcome_v1.metadata["receipt_id"])
    assert r0.policy_version != r1.policy_version
    assert r0.payload_hash != r1.payload_hash


def test_payload_hash_changes_when_constitution_hash_changes():
    a0, r0 = _make_adapter(constitution_hash="c_a")
    a1, r1 = _make_adapter(constitution_hash="c_b")
    o0 = a0.handle_transition(
        _transition(), a0.govern_action(_action("bad", ["blocked"]))
    )
    o1 = a1.handle_transition(
        _transition(), a1.govern_action(_action("bad", ["blocked"]))
    )
    rec0 = r0.get_receipt(o0.metadata["receipt_id"])
    rec1 = r1.get_receipt(o1.metadata["receipt_id"])
    assert rec0.payload_hash != rec1.payload_hash


def test_missing_receipt_raises():
    receipts = InMemoryReceiptService()
    with pytest.raises(GaiaGovernanceError):
        receipts.get_receipt("rcpt_does_not_exist")
