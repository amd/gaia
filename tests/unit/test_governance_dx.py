# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access
"""Tests for the DX ergonomic surfaces: `.default()`, GovernanceConfig, @govern."""

from __future__ import annotations

from typing import Any

import pytest

from gaia import tool
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.governance import (
    GaiaGovernanceAdapter,
    GovernanceConfig,
    GovernedAgentMixin,
    govern,
    read_risk_tags,
)


@tool
@govern(risk="blocked", reason="dx test blocked")
def _dx_decorated_blocked(x: int = 1) -> dict:
    return {"x": x}


@tool
@govern(risk=["review", "slow"])
def _dx_decorated_review(x: int = 1) -> dict:
    return {"x": x}


class _FakeAgent:
    def __init__(self, **_: Any) -> None:
        self.calls: list[tuple[str, dict]] = []

    def _execute_tool(self, tool_name, tool_args):
        self.calls.append((tool_name, dict(tool_args)))
        return {"status": "ok"}


class _GovernedFakeAgent(GovernedAgentMixin, _FakeAgent):
    pass


@pytest.fixture(autouse=True)
def _ensure_dx_tools_registered():
    """Re-register test tools if _TOOL_REGISTRY was cleared by another test suite."""
    for fn in (_dx_decorated_blocked, _dx_decorated_review):
        if fn.__name__ not in _TOOL_REGISTRY:
            tool(fn)
    yield


# ---- GaiaGovernanceAdapter.default() ------------------------------------


def test_default_adapter_uses_inmemory_when_audit_log_is_none():
    adapter = GaiaGovernanceAdapter.default(audit_log=None)
    # Satisfies all four protocols with ready instances.
    assert adapter.policy_engine is not None
    assert adapter.checkpoint_runtime is not None
    assert adapter.receipt_service is not None
    assert adapter.policy_binding is not None


def test_default_adapter_writes_jsonl_when_path_given(tmp_path):
    path = tmp_path / "r.jsonl"
    adapter = GaiaGovernanceAdapter.default(audit_log=str(path))
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"t": ["blocked"]},
    )
    agent._execute_tool("t", {})
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() != ""


# ---- GovernanceConfig ----------------------------------------------------


def test_governance_config_drives_mixin_the_same_as_kwargs():
    adapter = GaiaGovernanceAdapter.default(audit_log=None)
    config = GovernanceConfig(
        adapter=adapter,
        actor_id="alice",
        risk_tags={"drop_table": ["blocked"]},
    )
    agent = _GovernedFakeAgent(governance=config)
    assert agent.governance_adapter is adapter
    assert agent._governance_actor_id == "alice"
    assert agent._governance_risk_tags == {"drop_table": ["blocked"]}

    result = agent._execute_tool("drop_table", {})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"


def test_kwargs_style_still_works_for_backward_compat():
    adapter = GaiaGovernanceAdapter.default(audit_log=None)
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_actor_id="bob",
        governance_risk_tags={"x": ["blocked"]},
    )
    assert agent._governance_actor_id == "bob"
    assert agent._governance_risk_tags == {"x": ["blocked"]}


# ---- @govern decorator ---------------------------------------------------


def test_govern_decorator_sets_risk_tags_attribute():
    assert read_risk_tags(_dx_decorated_blocked) == ["blocked"]
    assert read_risk_tags(_dx_decorated_review) == ["review", "slow"]


def test_govern_decorator_stacks_without_duplicates():
    @govern(risk="blocked")
    @govern(risk="blocked")
    @govern(risk="audit")
    def fn():  # pragma: no cover
        return None

    # inner-to-outer: audit first, then blocked (deduped)
    assert read_risk_tags(fn) == ["audit", "blocked"]


def test_mixin_reads_decorated_tags_from_registry():
    adapter = GaiaGovernanceAdapter.default(audit_log=None)
    agent = _GovernedFakeAgent(governance_adapter=adapter)
    # No explicit risk_tags dict; tags come purely from @govern decorator.
    result = agent._execute_tool("_dx_decorated_blocked", {})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"
    assert agent.calls == []


def test_explicit_empty_dict_does_not_downgrade_decorator_tags():
    """Document the additive-only semantic: an empty explicit list cannot
    cancel a decorator-declared tag. Merge rule is union+dedup, not override.
    """
    adapter = GaiaGovernanceAdapter.default(audit_log=None)
    agent = _GovernedFakeAgent(
        governance_adapter=adapter,
        governance_risk_tags={"_dx_decorated_blocked": []},
    )
    # Merged tags = ["blocked"] (decorator) ∪ [] (explicit) = ["blocked"].
    # The decorator tag still applies; the explicit-empty does NOT downgrade.
    result = agent._execute_tool("_dx_decorated_blocked", {})
    assert result["status"] == "denied"


def test_read_risk_tags_handles_missing_attribute():
    def plain():  # pragma: no cover
        return None

    assert read_risk_tags(plain) == []
    assert read_risk_tags(None) == []
