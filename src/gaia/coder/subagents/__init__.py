# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Short-lived, isolated subagents dispatched from the main coder loop.

Each module implements one subagent pattern from §5.10 — external-codebase
research, sandbox probes, fresh-context adversarial review, etc. Subagents
are deliberately coarse-grained: each has its own scratch workspace, its
own tool subset, and its own budget ceiling. They return a structured
:class:`pydantic.BaseModel` result to the caller and then exit.

No subagent mutates primary state (memory, RAG, repo). If a subagent's
findings are worth keeping, the main loop decides what to persist — the
subagent itself is pure observation.
"""

from __future__ import annotations

from gaia.coder.subagents.codebase_research import (
    BudgetExceededError,
    CodebaseResearchError,
    ResearchBudget,
    StructuredAnalysis,
    research,
)

__all__ = [
    "BudgetExceededError",
    "CodebaseResearchError",
    "ResearchBudget",
    "StructuredAnalysis",
    "research",
]
