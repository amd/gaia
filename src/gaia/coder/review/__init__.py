# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Multi-pass self-review for gaia-coder (Phase 4; see §8 + §15.8).

This package gates ``gh_pr_create``. The coder agent calls
:meth:`ReviewToolsMixin.review_diff_gate` before opening any PR, and the
gate refuses to return ``"pass"`` unless every applicable review pass is
clean.

Public surface:

* :class:`ReviewToolsMixin` — the mixin the coder agent composes in.
* :class:`PassResult` — the per-pass return shape.
* :class:`GateResult` — the aggregated gate verdict.
* :func:`run_all_passes` — the one-shot function-level entry point used
  by tests and by the mixin's ``review_diff_gate`` tool.

Submodules ``pass_1_static`` … ``pass_7_feedback_binding`` expose a
``run_pass(pr_or_branch, ...)`` function each; the mixin wraps them as
``@tool``-registered entries.
"""

from gaia.coder.review.gate import GateResult, run_all_passes
from gaia.coder.review.mixin import ReviewToolsMixin
from gaia.coder.review.pass_result import (
    Finding,
    PassResult,
    PassStatus,
    make_pass_result,
)

__all__ = [
    "Finding",
    "GateResult",
    "PassResult",
    "PassStatus",
    "ReviewToolsMixin",
    "make_pass_result",
    "run_all_passes",
]
