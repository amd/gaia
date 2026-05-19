import pytest

from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.schemas import (
    CheckpointResolution,
    GovernanceDecision,
    TransitionOutcome,
    WorkflowTransition,
)


def make_transition():
    return WorkflowTransition(
        workflow_id="wf_1",
        transition_id="tx_1",
        from_state="READY",
        to_state="TOOL:foo",
        transition_type="tool_call",
        related_action_id="act_1",
        payload={"tool_args": {}},
    )


def make_decision():
    return GovernanceDecision(
        decision="REVIEW", reason="r", policy_version="v1", rule_ids=["r1"]
    )


def test_create_and_get_checkpoint():
    b = InMemoryCheckpointBridge()
    t = make_transition()
    d = make_decision()
    record = b.create_checkpoint(t, d)
    assert record.checkpoint_id is not None
    got = b.get_checkpoint(record.checkpoint_id)
    assert got is not None
    assert got.checkpoint_id == record.checkpoint_id


def test_resolve_checkpoint_approve_and_reject():
    b = InMemoryCheckpointBridge()
    t = make_transition()
    d = make_decision()
    rec = b.create_checkpoint(t, d)

    # Approve
    resolution = CheckpointResolution(
        resolution="APPROVE", actor_id="alice", reason="ok"
    )
    outcome = b.resolve_checkpoint(rec.checkpoint_id, resolution)
    assert outcome.status == "RESUMED"

    # Create new and reject
    rec2 = b.create_checkpoint(t, d)
    resolution2 = CheckpointResolution(resolution="REJECT", actor_id="bob", reason="no")
    outcome2 = b.resolve_checkpoint(rec2.checkpoint_id, resolution2)
    assert outcome2.status == "TERMINATED"


def test_resolve_unknown_checkpoint_raises():
    b = InMemoryCheckpointBridge()
    with pytest.raises(Exception):
        b.resolve_checkpoint(
            "nonexistent", CheckpointResolution(resolution="APPROVE", actor_id="x")
        )


def test_resolve_twice_raises_invalid_resolution():
    b = InMemoryCheckpointBridge()
    t = make_transition()
    d = make_decision()
    rec = b.create_checkpoint(t, d)
    res = CheckpointResolution(resolution="APPROVE", actor_id="a")
    out = b.resolve_checkpoint(rec.checkpoint_id, res)
    assert out.status == "RESUMED"
    # Second resolution should raise
    with pytest.raises(Exception):
        b.resolve_checkpoint(rec.checkpoint_id, res)
