# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gaia-coder — dev-tooling coding agent (see docs/plans/coder-agent.mdx).

This package is intentionally separate from ``src/gaia/agents/`` — the coder
is infrastructure for building GAIA, not a GAIA product agent.
"""

from gaia.coder.base import CoderAgent
from gaia.coder.loop import DEFAULT_LOOP, Loop, State, Transition

__all__ = [
    "CoderAgent",
    "DEFAULT_LOOP",
    "Loop",
    "State",
    "Transition",
]
