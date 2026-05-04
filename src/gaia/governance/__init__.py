# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Optional governance layer for GAIA agents.

Provides action-level governance (ACGS-lite semantics) plus seams for
future workflow checkpoint / receipt / policy-version binding
(constitutional-swarm semantics).

This package is opt-in. Importing it has no side effects on existing
GAIA agents. To govern an agent, mix :class:`GovernedAgentMixin` into
your agent class and pass a :class:`GaiaGovernanceAdapter` via the
``governance_adapter`` keyword argument.
"""

from .action_mapper import map_gaia_tool_call_to_action_request
from .adapter import GaiaGovernanceAdapter
from .config import GovernanceConfig
from .decorators import govern, read_risk_tags
from .exceptions import (
    CheckpointNotFoundError,
    GaiaGovernanceError,
    InvalidResolutionError,
)
from .mixin import GovernedAgentMixin
from .schemas import (
    ActionRequest,
    CheckpointRecord,
    CheckpointResolution,
    GovernanceDecision,
    PolicyVersionRef,
    ReceiptRecord,
    TransitionOutcome,
    WorkflowTransition,
    new_id,
    utc_now_iso,
)

__all__ = [
    "ActionRequest",
    "CheckpointNotFoundError",
    "CheckpointRecord",
    "CheckpointResolution",
    "GaiaGovernanceAdapter",
    "GaiaGovernanceError",
    "GovernanceConfig",
    "GovernanceDecision",
    "GovernedAgentMixin",
    "InvalidResolutionError",
    "PolicyVersionRef",
    "ReceiptRecord",
    "TransitionOutcome",
    "WorkflowTransition",
    "govern",
    "map_gaia_tool_call_to_action_request",
    "new_id",
    "read_risk_tags",
    "utc_now_iso",
]
