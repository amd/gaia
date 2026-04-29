# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for GaiaGovernanceAdapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import inf, nan
from pathlib import PurePosixPath
from uuid import UUID

from gaia.governance import (
    ActionRequest,
    CheckpointResolution,
    GaiaGovernanceAdapter,
    WorkflowTransition,
)
from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.policy_binding import StaticPolicyBindingService
from gaia.governance.receipt_service import InMemoryReceiptService, JsonlReceiptService
from gaia.governance.stubs import RuleBasedPolicyEngine


def _adapter() -> GaiaGovernanceAdapter:
    return GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )


def _action(tool_name: str, risk_tags: list[str]) -> ActionRequest:
    return ActionRequest(
        action_id="a1",
        actor_id="actor",
        tool_name=tool_name,
        action_type=tool_name,
        args={},
        risk_tags=risk_tags,
        workflow_id="wf_test",
    )


def _transition() -> WorkflowTransition:
    return WorkflowTransition(
        workflow_id="wf_test",
        transition_id="t1",
        from_state="START",
        to_state="RUN",
        transition_type="tool_call",
        related_action_id="a1",
    )


def test_allow_decision_is_pass_through():
    adapter = _adapter()
    decision = adapter.govern_action(_action("get_weather", []))
    assert decision.decision == "ALLOW"


def test_block_decision_for_blocked_tag():
    adapter = _adapter()
    decision = adapter.govern_action(_action("drop_table", ["blocked"]))
    assert decision.decision == "BLOCK"
    assert decision.policy_version == "v0"


def test_review_decision_for_review_tag():
    adapter = _adapter()
    decision = adapter.govern_action(_action("publish_post", ["review"]))
    assert decision.decision == "REVIEW"


def test_handle_transition_allow_continues():
    adapter = _adapter()
    decision = adapter.govern_action(_action("get_weather", []))
    outcome = adapter.handle_transition(_transition(), decision)
    assert outcome.status == "CONTINUE"


def test_handle_transition_block_issues_receipt():
    adapter = _adapter()
    decision = adapter.govern_action(_action("delete_all", ["blocked"]))
    outcome = adapter.handle_transition(_transition(), decision)
    assert outcome.status == "TERMINATED"
    assert "receipt_id" in outcome.metadata


def test_handle_transition_review_opens_checkpoint():
    adapter = _adapter()
    decision = adapter.govern_action(_action("publish_post", ["review"]))
    outcome = adapter.handle_transition(_transition(), decision)
    assert outcome.status == "CHECKPOINT_OPEN"
    assert outcome.checkpoint_id is not None


def test_block_receipt_handles_non_json_tool_args():
    adapter = _adapter()
    decision = adapter.govern_action(_action("delete_file", ["blocked"]))
    transition = WorkflowTransition(
        workflow_id="wf_test",
        transition_id="t1",
        from_state="START",
        to_state="RUN",
        transition_type="tool_call",
        related_action_id="a1",
        payload={"tool_args": {"path": PurePosixPath("/tmp/example")}},
    )

    outcome = adapter.handle_transition(transition, decision)

    assert outcome.status == "TERMINATED"
    receipt = adapter.receipt_service.get_receipt(outcome.metadata["receipt_id"])
    assert receipt is not None
    path_evidence = receipt.metadata["evidence"]["transition"]["payload"]["tool_args"][
        "path"
    ]
    assert path_evidence == {"__type__": "PurePosixPath", "value": "/tmp/example"}


def test_block_receipt_with_non_json_args_writes_strict_jsonl(tmp_path):
    adapter = GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=JsonlReceiptService(tmp_path / "receipts.jsonl"),
        policy_binding=StaticPolicyBindingService(),
    )
    decision = adapter.govern_action(_action("delete_file", ["blocked"]))
    transition = WorkflowTransition(
        workflow_id="wf_test",
        transition_id="t1",
        from_state="START",
        to_state="RUN",
        transition_type="tool_call",
        related_action_id="a1",
        payload={"tool_args": {"path": PurePosixPath("/tmp/example")}},
    )

    outcome = adapter.handle_transition(transition, decision)

    receipt = adapter.receipt_service.get_receipt(outcome.metadata["receipt_id"])
    path_evidence = receipt.metadata["evidence"]["transition"]["payload"]["tool_args"][
        "path"
    ]
    assert path_evidence == {"__type__": "PurePosixPath", "value": "/tmp/example"}


@dataclass
class CustomEvidence:
    name: str
    score: Decimal


class SlotOnlyEvidence:
    __slots__ = ()


class SelfReferentialEvidence:
    def __init__(self):
        self.self = self


def test_block_receipt_canonicalizes_complex_evidence_without_repr_fallback():
    adapter = _adapter()
    decision = adapter.govern_action(_action("delete_file", ["blocked"]))
    transition = WorkflowTransition(
        workflow_id="wf_test",
        transition_id="t1",
        from_state="START",
        to_state="RUN",
        transition_type="tool_call",
        related_action_id="a1",
        payload={
            "tool_args": {
                "non_finite": [nan, inf, -inf],
                "bytes": b"\x00\xff",
                "tuple": ("a", 1),
                "set": {"b", "a"},
                "mapping": {1: "integer", "1": "string"},
                "uuid": UUID("00000000-0000-0000-0000-000000000001"),
                "custom": CustomEvidence(name="alpha", score=Decimal("1.20")),
                "opaque": SlotOnlyEvidence(),
            }
        },
    )

    outcome = adapter.handle_transition(transition, decision)

    receipt = adapter.receipt_service.get_receipt(outcome.metadata["receipt_id"])
    args = receipt.metadata["evidence"]["transition"]["payload"]["tool_args"]
    assert args["non_finite"] == [
        {"__type__": "float", "value": "nan"},
        {"__type__": "float", "value": "inf"},
        {"__type__": "float", "value": "-inf"},
    ]
    assert args["bytes"] == {"__type__": "bytes", "value": "00ff"}
    assert args["tuple"] == {"__type__": "tuple", "items": ["a", 1]}
    assert args["set"] == {"__type__": "set", "items": ["a", "b"]}
    assert args["mapping"] == {
        "__type__": "mapping",
        "entries": [["1", "string"], [1, "integer"]],
    }
    assert args["uuid"] == {
        "__type__": "UUID",
        "value": "00000000-0000-0000-0000-000000000001",
    }
    assert args["custom"]["fields"] == {
        "name": "alpha",
        "score": {"__type__": "Decimal", "value": "1.20"},
    }
    assert args["opaque"] == {
        "__type__": "test_governance_adapter.SlotOnlyEvidence",
        "unserializable": True,
    }


def test_block_receipt_canonicalizes_cycles_without_recursing():
    adapter = _adapter()
    decision = adapter.govern_action(_action("delete_file", ["blocked"]))
    cyclic_dict = {}
    cyclic_dict["self"] = cyclic_dict
    cyclic_list = []
    cyclic_list.append(cyclic_list)
    cyclic_object = SelfReferentialEvidence()
    transition = WorkflowTransition(
        workflow_id="wf_test",
        transition_id="t1",
        from_state="START",
        to_state="RUN",
        transition_type="tool_call",
        related_action_id="a1",
        payload={
            "tool_args": {
                "dict": cyclic_dict,
                "list": cyclic_list,
                "object": cyclic_object,
            }
        },
    )

    outcome = adapter.handle_transition(transition, decision)

    receipt = adapter.receipt_service.get_receipt(outcome.metadata["receipt_id"])
    args = receipt.metadata["evidence"]["transition"]["payload"]["tool_args"]
    assert args["dict"]["self"] == {"__type__": "builtins.dict", "cycle": True}
    assert args["list"] == [{"__type__": "builtins.list", "cycle": True}]
    assert args["object"]["fields"]["self"] == {
        "__type__": "test_governance_adapter.SelfReferentialEvidence",
        "cycle": True,
    }


def test_resolve_checkpoint_approve_resumes_and_records_receipt():
    adapter = _adapter()
    decision = adapter.govern_action(_action("publish_post", ["review"]))
    opened = adapter.handle_transition(_transition(), decision)
    outcome = adapter.resolve_checkpoint(
        opened.checkpoint_id,
        CheckpointResolution(resolution="APPROVE", actor_id="reviewer", reason="ok"),
        workflow_id="wf_test",
    )
    assert outcome.status == "RESUMED"
    assert "receipt_id" in outcome.metadata
