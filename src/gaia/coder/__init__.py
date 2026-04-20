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

This module is the Phase 1 scaffold entry point. ``CoderAgent`` and
``DEFAULT_LOOP`` are added as re-exports once the corresponding modules
land in follow-up commits on this same branch.
"""

__all__: list[str] = []
