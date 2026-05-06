# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Static PolicyBinding reference implementation.

Swap for constitutional-swarm's PolicyBinding once the policy control
plane is in place. The receipt issuer reads ``current_version()`` to
stamp policy-version + constitution-hash onto every decision.
"""

from __future__ import annotations

from .schemas import PolicyVersionRef, utc_now_iso


class StaticPolicyBindingService:
    def __init__(
        self,
        version: str = "v0",
        constitution_hash: str = "constitution-dev",
    ) -> None:
        self._current = PolicyVersionRef(
            version=version,
            constitution_hash=constitution_hash,
            activated_at=utc_now_iso(),
        )

    def current_version(self) -> PolicyVersionRef:
        return self._current
