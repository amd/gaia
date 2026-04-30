# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access,attribute-defined-outside-init
"""Integration test for the REVIEW checkpoint flow.

Proves that when a policy returns REVIEW, the mixin opens a checkpoint,
asks a reviewer, records a receipt for the resolution, and either runs
or denies the tool based on the reviewer's response.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from gaia.governance import (
    GaiaGovernanceAdapter,
    GovernedAgentMixin,
)
from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.policy_binding import StaticPolicyBindingService
from gaia.governance.receipt_service import InMemoryReceiptService
from gaia.governance.stubs import RuleBasedPolicyEngine
from gaia.ui.sse_handler import SSEOutputHandler


class _FakeAgent:
    def __init__(self, **_: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        self.calls.append((tool_name, dict(tool_args)))
        return {"status": "ok", "tool": tool_name}


class _GovernedFakeAgent(GovernedAgentMixin, _FakeAgent):
    pass


class _StubConsoleAccept:
    """Represents a console that WOULD approve — but must not be used
    as an implicit reviewer. Kept to prove the console is now ignored."""

    def confirm_tool_execution(self, _tn, _args):
        return True


class _BlockingConsoleAccept:
    blocking_confirmation = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.calls.append((tool_name, dict(tool_args)))
        return True


def _build_adapter():
    receipts = InMemoryReceiptService()
    adapter = GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=receipts,
        policy_binding=StaticPolicyBindingService(),
    )
    return adapter, receipts


def test_review_with_explicit_approver_runs_tool_and_records_receipt():
    adapter, receipts = _build_adapter()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
        governance_reviewer=lambda *_a, **_kw: True,
    )
    result = agent._execute_tool("publish_post", {"body": "hi"})
    assert result["status"] == "ok"
    assert agent.calls == [("publish_post", {"body": "hi"})]
    # one receipt (APPROVE) recorded
    receipts_list = list(receipts)
    assert len(receipts_list) == 1
    assert receipts_list[0].decision == "APPROVE"


def test_review_with_explicit_rejecter_denies_and_records_receipt():
    adapter, receipts = _build_adapter()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
        governance_reviewer=lambda *_a, **_kw: False,
    )
    result = agent._execute_tool("publish_post", {"body": "hi"})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "REVIEW_REJECTED"
    assert result["receipt_id"].startswith("rcpt_")
    assert agent.calls == []
    receipts_list = list(receipts)
    assert len(receipts_list) == 1
    assert receipts_list[0].decision == "REJECT"


def test_review_ignores_default_console_and_fails_closed():
    # HIGH-1 regression: GAIA's default AgentConsole.confirm_tool_execution
    # returns True, so treating it as an implicit reviewer would
    # auto-approve. The mixin must NOT use the console unless the caller
    # explicitly opts in via governance_reviewer.
    adapter, _ = _build_adapter()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
    )
    agent.console = _StubConsoleAccept()  # would approve if consulted
    result = agent._execute_tool("publish_post", {"body": "hi"})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "REVIEW_REJECTED"
    assert agent.calls == []


def test_review_honors_explicit_reviewer_that_delegates_to_console():
    # Opt-in path: caller wraps the console explicitly, which is safe
    # because they've verified their console actually blocks.
    adapter, _ = _build_adapter()
    console = _StubConsoleAccept()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
        governance_reviewer=lambda tn, args, _d: console.confirm_tool_execution(
            tn, args
        ),
    )
    result = agent._execute_tool("publish_post", {"body": "hi"})
    assert result["status"] == "ok"


def test_review_uses_blocking_console_when_no_explicit_reviewer():
    adapter, _ = _build_adapter()
    console = _BlockingConsoleAccept()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
    )
    agent.console = console

    result = agent._execute_tool("publish_post", {"body": "hi"})

    assert result["status"] == "ok"
    assert console.calls == [("publish_post", {"body": "hi"})]


def test_review_with_sse_console_emits_permission_request_and_runs_on_approve():
    adapter, _ = _build_adapter()
    console = SSEOutputHandler()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
    )
    agent.console = console
    result_holder: dict[str, Any] = {}

    def run_tool():
        result_holder["result"] = agent._execute_tool("publish_post", {"body": "hi"})

    thread = threading.Thread(target=run_tool)
    thread.start()

    permission_event = None
    deadline = time.time() + 3.0
    while time.time() < deadline:
        while not console.event_queue.empty():
            event = console.event_queue.get_nowait()
            if event.get("type") == "permission_request":
                permission_event = event
                break
        if permission_event is not None:
            break
        time.sleep(0.05)

    assert permission_event is not None
    assert permission_event["tool"] == "publish_post"
    assert permission_event["args"] == {"body": "hi"}

    console.resolve_tool_confirmation(approved=True)
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    assert result_holder["result"]["status"] == "ok"
    assert agent.calls == [("publish_post", {"body": "hi"})]


def test_review_fails_closed_when_no_reviewer():
    adapter, _ = _build_adapter()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
    )
    # no console, no reviewer -> deny
    result = agent._execute_tool("publish_post", {"body": "hi"})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "REVIEW_REJECTED"
    assert agent.calls == []


def test_block_decision_records_receipt_and_returns_receipt_id():
    adapter, receipts = _build_adapter()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"drop_table": ["blocked"]},
    )
    result = agent._execute_tool("drop_table", {"name": "users"})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"
    assert result["receipt_id"].startswith("rcpt_")
    receipts_list = list(receipts)
    assert len(receipts_list) == 1
    assert receipts_list[0].decision == "BLOCK"


def test_block_decision_with_sse_console_emits_policy_alert():
    adapter, receipts = _build_adapter()
    console = SSEOutputHandler()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"drop_table": ["blocked"]},
    )
    agent.console = console

    result = agent._execute_tool("drop_table", {"name": "users"})

    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"
    assert agent.calls == []

    events = []
    while not console.event_queue.empty():
        events.append(console.event_queue.get_nowait())
    policy_alerts = [event for event in events if event.get("type") == "policy_alert"]
    assert policy_alerts == [
        {
            "type": "policy_alert",
            "tool": "drop_table",
            "decision": "BLOCK",
            "reason": "blocked by policy",
            "rule_ids": ["rule:block"],
            "policy_version": "v0",
            "receipt_id": result["receipt_id"],
        }
    ]
    receipts_list = list(receipts)
    assert len(receipts_list) == 1
    assert receipts_list[0].decision == "BLOCK"


def test_reviewer_exception_is_treated_as_reject(caplog):
    adapter, receipts = _build_adapter()

    def boom(*_a, **_kw):
        raise RuntimeError("bad reviewer")

    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
        governance_reviewer=boom,
    )
    caplog.set_level(logging.WARNING, "gaia.governance.mixin")
    result = agent._execute_tool("publish_post", {"body": "hi"})
    assert result["status"] == "denied"
    assert agent.calls == []
    # A misbehaving reviewer must be visible to operators, not just silently
    # treated as REJECT. Match on logger name, not message string.
    assert any(
        record.levelname == "WARNING" and record.name == "gaia.governance.mixin"
        for record in caplog.records
    )
    # Audit-trail fidelity: the receipt's reason must distinguish "reviewer
    # raised" from "reviewer chose no", carrying the exception type/message
    # so an auditor reading the JSONL log knows the REJECT was due to a
    # crash, not a deliberate "no".
    receipts_list = list(receipts)
    reject_receipts = [r for r in receipts_list if r.decision == "REJECT"]
    assert len(reject_receipts) == 1
    resolution_reason = reject_receipts[0].metadata["evidence"]["resolution"]["reason"]
    assert "RuntimeError" in resolution_reason
    assert "bad reviewer" in resolution_reason


def test_reviewer_explicit_no_keeps_plain_reason():
    """Counterpart to the exception test: a reviewer that returns False
    (a deliberate "no") produces a plain "reviewer rejected" reason in the
    receipt, NOT an exception-flavored one.
    """
    adapter, receipts = _build_adapter()
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"publish_post": ["review"]},
        governance_reviewer=lambda *_a, **_kw: False,
    )
    result = agent._execute_tool("publish_post", {"body": "hi"})
    assert result["status"] == "denied"
    receipts_list = list(receipts)
    reject_receipts = [r for r in receipts_list if r.decision == "REJECT"]
    assert len(reject_receipts) == 1
    resolution_reason = reject_receipts[0].metadata["evidence"]["resolution"]["reason"]
    assert resolution_reason == "reviewer rejected"
