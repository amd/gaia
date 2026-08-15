# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for GaiaGovernanceAdapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import inf, nan
from pathlib import PurePosixPath
from uuid import UUID

import pytest

from gaia.governance import (
    ActionRequest,
    CheckpointResolution,
    GaiaGovernanceAdapter,
    GaiaGovernanceError,
    GovernanceDecision,
    WorkflowTransition,
)
from gaia.governance.adapter import GaiaRiskTagFloorEngine
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


def test_handle_transition_rejects_unknown_decision_type():
    """``GovernanceDecision.decision`` is ``Literal[...]`` but Python does
    not enforce literal types at runtime. A custom PolicyEngine that
    returns a decision string the adapter doesn't recognize must raise
    rather than silently allow the call.
    """
    adapter = _adapter()
    bogus = GovernanceDecision(decision="WAT", reason="x", policy_version="v0")
    with pytest.raises(GaiaGovernanceError):
        adapter.handle_transition(_transition(), bogus)


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
        "__type__": f"{SlotOnlyEvidence.__module__}.SlotOnlyEvidence",
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
        "__type__": f"{SelfReferentialEvidence.__module__}.SelfReferentialEvidence",
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


class _PermissiveEngine:
    """Inner engine that always ALLOWs — used to prove the GAIA floor."""

    def evaluate_action(self, action_request):
        return GovernanceDecision(
            decision="ALLOW",
            reason="inner allow",
            policy_version="v0",
            rule_ids=["inner:allow"],
        )


class _BlockingEngine:
    def evaluate_action(self, action_request):
        return GovernanceDecision(
            decision="BLOCK",
            reason="inner block",
            policy_version="v0",
            rule_ids=["inner:block"],
        )


def test_risk_tag_floor_forces_block_over_inner_allow():
    adapter = GaiaGovernanceAdapter(
        policy_engine=GaiaRiskTagFloorEngine(_PermissiveEngine()),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )
    decision = adapter.govern_action(_action("wipe_disk", ["blocked"]))
    assert decision.decision == "BLOCK"
    assert "gaia:risk-tag:blocked" in decision.rule_ids
    assert decision.metadata["risk_tag_floor"] == "blocked"


def test_risk_tag_floor_forces_review_over_inner_allow():
    adapter = GaiaGovernanceAdapter(
        policy_engine=GaiaRiskTagFloorEngine(_PermissiveEngine()),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )
    decision = adapter.govern_action(_action("publish_post", ["review"]))
    assert decision.decision == "REVIEW"
    assert "gaia:risk-tag:review" in decision.rule_ids


def test_risk_tag_floor_does_not_loosen_inner_block():
    adapter = GaiaGovernanceAdapter(
        policy_engine=GaiaRiskTagFloorEngine(_BlockingEngine()),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )
    decision = adapter.govern_action(_action("search", ["review"]))
    assert decision.decision == "BLOCK"
    assert decision.rule_ids == ["inner:block"]


def test_risk_tag_floor_ignores_auto_approve_env(monkeypatch):
    monkeypatch.setenv("GAIA_AUTO_APPROVE_TOOLS", "1")
    adapter = GaiaGovernanceAdapter(
        policy_engine=GaiaRiskTagFloorEngine(_PermissiveEngine()),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )
    decision = adapter.govern_action(_action("wipe_disk", ["blocked"]))
    assert decision.decision == "BLOCK"


def test_risk_tag_floor_normalizes_tag_case_and_whitespace():
    adapter = GaiaGovernanceAdapter(
        policy_engine=GaiaRiskTagFloorEngine(_PermissiveEngine()),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )
    decision = adapter.govern_action(_action("wipe_disk", [" BLOCKED "]))
    assert decision.decision == "BLOCK"
    assert "gaia:risk-tag:blocked" in decision.rule_ids


def test_risk_tag_floor_fail_closes_unknown_inner_decision():
    class _WeirdEngine:
        def evaluate_action(self, action_request):
            return GovernanceDecision(decision="WAT", reason="x", policy_version="v0")

    adapter = GaiaGovernanceAdapter(
        policy_engine=GaiaRiskTagFloorEngine(_WeirdEngine()),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )
    decision = adapter.govern_action(_action("search", []))
    assert decision.decision == "BLOCK"
    assert "gaia:unknown-decision" in decision.rule_ids


def _purge_acgs_lite(monkeypatch):
    import sys

    for key in list(sys.modules):
        if key == "acgs_lite" or key.startswith("acgs_lite."):
            monkeypatch.delitem(sys.modules, key, raising=False)


def test_from_acgs_lite_fail_closes_when_package_missing(monkeypatch):
    import builtins

    _purge_acgs_lite(monkeypatch)
    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name.startswith("acgs_lite"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(GaiaGovernanceError, match="ACGS-lite is not installed"):
        GaiaGovernanceAdapter.from_acgs_lite(audit_log=None)


def test_from_acgs_lite_reports_broken_inner_dependency(monkeypatch):
    import builtins

    _purge_acgs_lite(monkeypatch)
    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name.startswith("acgs_lite"):
            raise ModuleNotFoundError(
                "No module named 'cryptography'", name="cryptography"
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(GaiaGovernanceError, match="import failed on dependency"):
        GaiaGovernanceAdapter.from_acgs_lite(audit_log=None)


def test_from_acgs_lite_fail_closes_when_adapter_module_missing(monkeypatch):
    """Published acgs-lite 2.11.0 has no integrations.gaia — fail closed."""
    import sys
    import types

    _purge_acgs_lite(monkeypatch)

    constitution_mod = types.ModuleType("acgs_lite.constitution")

    class Constitution:
        @classmethod
        def default(cls):
            return cls()

    constitution_mod.Constitution = Constitution
    monkeypatch.setitem(sys.modules, "acgs_lite", types.ModuleType("acgs_lite"))
    monkeypatch.setitem(sys.modules, "acgs_lite.constitution", constitution_mod)
    monkeypatch.setitem(
        sys.modules,
        "acgs_lite.integrations",
        types.ModuleType("acgs_lite.integrations"),
    )

    with pytest.raises(GaiaGovernanceError, match="does not ship"):
        GaiaGovernanceAdapter.from_acgs_lite(audit_log=None)


def test_from_acgs_lite_wires_binding_and_gaia_owned_floor(monkeypatch):
    """Always-on wiring test: stub acgs-lite via sys.modules (no live wheel)."""
    import sys
    import types

    from gaia.governance.schemas import PolicyVersionRef

    _purge_acgs_lite(monkeypatch)

    constitution_mod = types.ModuleType("acgs_lite.constitution")

    class Constitution:
        def __init__(self, digest="hash-test", version="1.0.0"):
            self.hash = digest
            self.version = version

        @classmethod
        def default(cls):
            return cls()

    constitution_mod.Constitution = Constitution

    gaia_mod = types.ModuleType("acgs_lite.integrations.gaia")

    class AcgsLitePolicyEngine:
        def __init__(self, constitution, *, agent_id="gaia-agent"):
            self.constitution = constitution
            self.agent_id = agent_id

        def evaluate_action(self, action_request):
            return GovernanceDecision(
                decision="ALLOW",
                reason="stub inner allow",
                policy_version=str(self.constitution.version),
                rule_ids=["stub:allow"],
            )

    class AcgsLitePolicyBinding:
        def __init__(self, constitution):
            self._constitution = constitution

        def current_version(self):
            return PolicyVersionRef(
                version=str(self._constitution.version),
                constitution_hash=str(self._constitution.hash),
                activated_at="2026-01-01T00:00:00+00:00",
            )

    gaia_mod.AcgsLitePolicyEngine = AcgsLitePolicyEngine
    gaia_mod.AcgsLitePolicyBinding = AcgsLitePolicyBinding

    monkeypatch.setitem(sys.modules, "acgs_lite", types.ModuleType("acgs_lite"))
    monkeypatch.setitem(sys.modules, "acgs_lite.constitution", constitution_mod)
    monkeypatch.setitem(
        sys.modules,
        "acgs_lite.integrations",
        types.ModuleType("acgs_lite.integrations"),
    )
    monkeypatch.setitem(sys.modules, "acgs_lite.integrations.gaia", gaia_mod)

    resolved = Constitution(digest="hash-live", version="9.9.9")
    adapter = GaiaGovernanceAdapter.from_acgs_lite(
        resolved, audit_log=None, agent_id="test"
    )
    assert isinstance(adapter.policy_engine, GaiaRiskTagFloorEngine)
    assert adapter.policy_binding.current_version().constitution_hash == "hash-live"
    assert adapter.policy_binding.current_version().version == "9.9.9"

    allowed = adapter.govern_action(_action("search", []))
    assert allowed.decision == "ALLOW"

    tagged = adapter.govern_action(_action("publish_post", ["blocked"]))
    assert tagged.decision == "BLOCK"
    assert "gaia:risk-tag:blocked" in tagged.rule_ids


def test_from_acgs_lite_honors_constitution_when_installed():
    """Opt-in live path. Default CI has no [acgs] extra; this is extra coverage."""
    pytest.importorskip("acgs_lite.integrations.gaia")
    from acgs_lite import Constitution, Rule, Severity, ViolationAction

    constitution = Constitution.from_rules(
        [
            Rule(
                id="GAIA-SHELL-1",
                text="Destructive shell is blocked",
                keywords=["wipe-disk"],
                severity=Severity.CRITICAL,
                workflow_action=ViolationAction.BLOCK,
            )
        ]
    )
    adapter = GaiaGovernanceAdapter.from_acgs_lite(
        constitution, audit_log=None, agent_id="test"
    )
    allowed = adapter.govern_action(_action("search", []))
    assert allowed.decision == "ALLOW"

    blocked = adapter.govern_action(
        ActionRequest(
            action_id="a2",
            actor_id="actor",
            tool_name="shell",
            action_type="shell",
            args={"cmd": "wipe-disk /"},
            risk_tags=[],
            workflow_id="wf_test",
        )
    )
    assert blocked.decision == "BLOCK"

    tagged = adapter.govern_action(_action("publish_post", ["blocked"]))
    assert tagged.decision == "BLOCK"
    assert (
        adapter.policy_binding.current_version().constitution_hash == constitution.hash
    )
