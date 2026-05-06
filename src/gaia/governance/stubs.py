# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stub policy engine for demos and tests.

Real engines will come from ACGS-lite. This stub decides purely from
``risk_tags`` on the :class:`ActionRequest`.
"""

from __future__ import annotations

from .schemas import ActionRequest, GovernanceDecision


class RuleBasedPolicyEngine:
    """Tiny stub engine.

    Rules:
    - risk tag 'blocked' -> BLOCK
    - risk tag 'review'  -> REVIEW
    - otherwise          -> ALLOW
    """

    def __init__(self, policy_version: str = "v0") -> None:
        self.policy_version = policy_version

    def evaluate_action(self, action_request: ActionRequest) -> GovernanceDecision:
        tags = set(action_request.risk_tags)
        if "blocked" in tags:
            return GovernanceDecision(
                decision="BLOCK",
                reason="blocked by policy",
                policy_version=self.policy_version,
                rule_ids=["rule:block"],
            )
        if "review" in tags:
            return GovernanceDecision(
                decision="REVIEW",
                reason="requires operator review",
                policy_version=self.policy_version,
                rule_ids=["rule:review"],
            )
        return GovernanceDecision(
            decision="ALLOW",
            reason="allowed by policy",
            policy_version=self.policy_version,
            rule_ids=["rule:allow"],
        )
