# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.governance.schemas and gaia.governance.action_mapper."""

from __future__ import annotations

import pytest

from gaia.governance import (
    ActionRequest,
    GovernanceDecision,
    map_gaia_tool_call_to_action_request,
    new_id,
    utc_now_iso,
)


def test_new_id_has_prefix_and_is_unique():
    a = new_id("action")
    b = new_id("action")
    assert a.startswith("action_") and b.startswith("action_")
    assert a != b


def test_utc_now_iso_is_iso_formatted():
    value = utc_now_iso()
    # crude but sufficient: must include T and either +00:00 or Z
    assert "T" in value
    assert value.endswith("+00:00") or value.endswith("Z")


def test_action_request_defaults_and_frozen():
    req = ActionRequest(
        action_id="a1",
        actor_id="actor",
        tool_name="tool",
        action_type="tool",
        args={"x": 1},
    )
    assert req.risk_tags == []
    assert req.source == "gaia"
    assert req.workflow_id is None
    with pytest.raises(AttributeError):
        req.actor_id = "other"  # frozen


def test_governance_decision_frozen_and_metadata_default():
    d = GovernanceDecision(
        decision="ALLOW",
        reason="ok",
        policy_version="v0",
    )
    assert d.metadata == {}
    assert d.rule_ids == []


def test_action_mapper_applies_context_and_defaults():
    req = map_gaia_tool_call_to_action_request(
        "get_weather",
        {"city": "Austin"},
        {
            "actor_id": "alice",
            "workflow_id": "wf_1",
            "risk_tags": ["read-only"],
        },
    )
    assert req.tool_name == "get_weather"
    assert req.actor_id == "alice"
    assert req.workflow_id == "wf_1"
    assert req.risk_tags == ["read-only"]
    assert req.source == "gaia"
    assert req.args == {"city": "Austin"}


def test_action_mapper_defaults_when_context_missing():
    req = map_gaia_tool_call_to_action_request("t", {})
    assert req.actor_id == "unknown-actor"
    assert req.workflow_id is None
    assert req.risk_tags == []
    assert req.action_id.startswith("action_")
