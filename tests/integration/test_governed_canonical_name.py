# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access
"""HIGH-2 regression: canonical tool name resolution before governance.

If governance checks risk tags against the raw LLM-supplied name, a
model can bypass a blocked MCP tool by calling the unprefixed alias
(``get_current_time`` instead of ``mcp_time_get_current_time``). The
mixin must resolve through the base Agent's ``_resolve_tool_name``
before building the ActionRequest.
"""

from __future__ import annotations

import logging
from typing import Any

from gaia.governance import GaiaGovernanceAdapter, GovernedAgentMixin
from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.policy_binding import StaticPolicyBindingService
from gaia.governance.receipt_service import InMemoryReceiptService
from gaia.governance.stubs import RuleBasedPolicyEngine


class _FakeAgentWithResolver:
    """Stand-in that mirrors GAIA's alias-resolution behavior."""

    ALIAS_MAP = {"get_current_time": "mcp_time_get_current_time"}

    def __init__(self, **_: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _resolve_tool_name(self, tool_name: str) -> str | None:
        return self.ALIAS_MAP.get(tool_name)

    def _execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        # Mirror base Agent: resolve alias internally before running
        canonical = self.ALIAS_MAP.get(tool_name, tool_name)
        self.calls.append((canonical, dict(tool_args)))
        return {"status": "ok", "tool": canonical}


class _GovernedFakeWithResolver(GovernedAgentMixin, _FakeAgentWithResolver):
    pass


def _adapter():
    return GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )


def test_unprefixed_alias_is_governed_under_canonical_name():
    agent = _GovernedFakeWithResolver(
        governance_adapter=_adapter(),
        governance_risk_tags={"mcp_time_get_current_time": ["blocked"]},
    )
    # LLM calls the unprefixed alias; governance must still block.
    result = agent._execute_tool("get_current_time", {})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"
    assert agent.calls == []


def test_raw_name_still_governed_directly():
    agent = _GovernedFakeWithResolver(
        governance_adapter=_adapter(),
        governance_risk_tags={"mcp_time_get_current_time": ["blocked"]},
    )
    result = agent._execute_tool("mcp_time_get_current_time", {})
    assert result["status"] == "denied"
    assert agent.calls == []


def test_unresolved_name_falls_through_to_raw():
    # A tool with no alias mapping must still be governable by its
    # own name.
    agent = _GovernedFakeWithResolver(
        governance_adapter=_adapter(),
        governance_risk_tags={"never_heard_of_it": ["blocked"]},
    )
    result = agent._execute_tool("never_heard_of_it", {})
    assert result["status"] == "denied"


class _FakeAgentResolverLookupError(_FakeAgentWithResolver):
    """Resolver raises LookupError — the expected 'not in registry' case.

    The mixin must absorb this silently and govern the raw name. No
    warning should be logged because the absence is a normal condition.
    """

    def _resolve_tool_name(self, _tool_name):
        raise LookupError("tool not registered")


class _GovernedLookupErrorAgent(GovernedAgentMixin, _FakeAgentResolverLookupError):
    pass


def test_resolver_lookup_error_is_silent_and_governs_raw_name(caplog):
    agent = _GovernedLookupErrorAgent(
        governance_adapter=_adapter(),
        governance_risk_tags={"raw_name": ["blocked"]},
    )
    caplog.set_level(logging.WARNING, "gaia.governance.mixin")
    result = agent._execute_tool("raw_name", {})
    # Raw-name governance still works.
    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"
    # Expected miss: no operator-visible warning.
    assert not any(
        record.name == "gaia.governance.mixin" and record.levelname == "WARNING"
        for record in caplog.records
    )


class _FakeAgentResolverBoom(_FakeAgentWithResolver):
    """Resolver raises an unexpected RuntimeError (programming bug).

    The mixin must (1) log a warning so operators can see the bug,
    (2) fall back to the raw name so governance still applies, and
    (3) NOT crash the tool call.
    """

    def _resolve_tool_name(self, _tool_name):
        raise RuntimeError("resolver implementation bug")


class _GovernedBoomAgent(GovernedAgentMixin, _FakeAgentResolverBoom):
    pass


def test_resolver_unexpected_exception_logs_and_governs_raw_name(caplog):
    agent = _GovernedBoomAgent(
        governance_adapter=_adapter(),
        governance_risk_tags={"raw_name": ["blocked"]},
    )
    caplog.set_level(logging.WARNING, "gaia.governance.mixin")
    result = agent._execute_tool("raw_name", {})
    # Raw-name governance still applies — bug in resolver does not bypass.
    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"
    # Operator-visible warning: a future regression that swaps the
    # logger.warning for a silent fallback would fail this assertion.
    assert any(
        record.name == "gaia.governance.mixin" and record.levelname == "WARNING"
        for record in caplog.records
    )
