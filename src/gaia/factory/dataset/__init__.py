# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Step-level agentic evaluation dataset built from Claude Code session transcripts.

One record is one decision point — an assistant inference call that dispatched at
least one tool — carrying the state the agent had, the action it took, what came
back, and which capabilities the step probes.

Everything this package writes is derived from private transcripts and belongs in
``~/.gaia/cache/factory/dataset/``. Never in a repository working tree.
"""
