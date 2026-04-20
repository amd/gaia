# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""gaia-coder: engineering-facing coding agent for the amd/gaia repository.

``gaia-coder`` is NOT a GAIA product agent. It is infrastructure for *building*
GAIA — dev tooling in the same category as ``util/lint.py`` or the
``.github/workflows/`` pipelines. It ships as a separately-installable package
with its own ``gaia-coder`` binary, runs on the engineering manager's
workstation against cloud frontier LLMs (Claude Opus 4.7), and does not
participate in the ``src/gaia/agents/`` product-agent ecosystem.

See ``docs/plans/coder-agent.mdx`` for the full spec.

Phase 1 scaffolding exports:

* :class:`gaia.coder.base.CoderAgent` — her own base class (NOT inheriting
  from ``gaia.agents.base.Agent``).
* :data:`gaia.coder.loop.DEFAULT_LOOP` — the canonical 20-state ReAct loop
  from §15.3 of the spec, editable per §7.8.
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
