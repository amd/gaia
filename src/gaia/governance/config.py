# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Consolidated governance configuration.

:class:`GovernanceConfig` bundles every governance knob the
:class:`GovernedAgentMixin` accepts into a single object, so user
agents do not carry six ``governance_*`` keywords in their
``__init__`` signatures.

Both styles are supported — use whichever feels more ergonomic::

    agent = MyAgent(governance=GovernanceConfig(
        adapter=adapter,
        risk_tags={"delete_record": ["blocked"]},
    ))

or, equivalently::

    agent = MyAgent(
        governance_adapter=adapter,
        governance_risk_tags={"delete_record": ["blocked"]},
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .adapter import GaiaGovernanceAdapter
from .schemas import ActionRequest, GovernanceDecision

# Observational callback: (tool_name, tool_args, action, decision) -> None.
GovernanceCallback = Callable[
    [str, dict[str, Any], ActionRequest, GovernanceDecision], None
]

# Reviewer callback: (tool_name, tool_args, decision) -> bool.
GovernanceReviewer = Callable[[str, dict[str, Any], GovernanceDecision], bool]


@dataclass(slots=True)
class GovernanceConfig:
    """All governance options in one object."""

    adapter: GaiaGovernanceAdapter
    actor_id: str = "gaia-agent"
    workflow_id: str | None = None
    risk_tags: dict[str, list[str]] = field(default_factory=dict)
    callback: GovernanceCallback | None = None
    reviewer: GovernanceReviewer | None = None
