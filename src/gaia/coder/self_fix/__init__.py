# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Self-correction primitives for gaia-coder (§7).

Phase 6 exports:

* :class:`SelfFixToolsMixin` — registers the nine self-fix loop tools on
  an agent.
* :class:`FeedbackLoopDriver` — orchestrates one iteration of the §7.4
  loop (triage → plan → fix → test → review → publish → verify).
* The continuous-critique entry point :func:`critique_recent_output` so
  the main loop can wire it after every state-changing tool call (§7.2).

The Phase-7 sub-loop (self-detected bug mid-task, §7.5) and ReAct-loop
self-edit (§7.8) are not exported here — they land in a separate mixin
when the dev-mode gate is wired up.
"""

from __future__ import annotations

from gaia.coder.self_fix.continuous_critique import (
    CritiqueResult,
    Finding,
    critique_recent_output,
)
from gaia.coder.self_fix.fixer import (
    DEFAULT_BASE_REF,
    SELF_FIX_BRANCH_PREFIX,
    Diff,
    DifferentialResult,
    EditHunk,
    TestPath,
    generate_fix,
    verify_test_differential,
    write_regression_test,
)
from gaia.coder.self_fix.loop_driver import (
    DriveResult,
    FeedbackLoopDriver,
    LoopDriverConfig,
)
from gaia.coder.self_fix.mixin import SelfFixToolsMixin
from gaia.coder.self_fix.planner import (
    ALWAYS_LARGE_FIX_CLASSES,
    DEFAULT_LARGE_JOB_LOC_THRESHOLD,
    MAX_PLAN_REFINEMENT_ROUNDS,
    ApprovalRequest,
    CostEstimate,
    FileTouchPlan,
    Plan,
    draft_plan,
    is_large_job,
    request_em_approval,
)
from gaia.coder.self_fix.publisher import (
    PRHandle,
    ReviewGateResult,
    compose_pr_body,
    notify_em,
    open_self_fix_pr,
)
from gaia.coder.self_fix.triage import (
    FIX_CLASSES,
    LOW_CONFIDENCE_ESCALATION_THRESHOLD,
    CandidateFile,
    FixClassResult,
    LocalisationHit,
    TriageContext,
    classify_fix_class,
    localise,
)
from gaia.coder.self_fix.verifier import VerificationResult, verify_on_merge

__all__ = [
    # Top-level public surface used by the loop driver and CLI.
    "FeedbackLoopDriver",
    "LoopDriverConfig",
    "DriveResult",
    "SelfFixToolsMixin",
    # Triage.
    "FIX_CLASSES",
    "LOW_CONFIDENCE_ESCALATION_THRESHOLD",
    "CandidateFile",
    "FixClassResult",
    "LocalisationHit",
    "TriageContext",
    "classify_fix_class",
    "localise",
    # Planner.
    "ALWAYS_LARGE_FIX_CLASSES",
    "ApprovalRequest",
    "CostEstimate",
    "DEFAULT_LARGE_JOB_LOC_THRESHOLD",
    "FileTouchPlan",
    "MAX_PLAN_REFINEMENT_ROUNDS",
    "Plan",
    "draft_plan",
    "is_large_job",
    "request_em_approval",
    # Fixer.
    "DEFAULT_BASE_REF",
    "SELF_FIX_BRANCH_PREFIX",
    "Diff",
    "DifferentialResult",
    "EditHunk",
    "TestPath",
    "generate_fix",
    "verify_test_differential",
    "write_regression_test",
    # Publisher.
    "PRHandle",
    "ReviewGateResult",
    "compose_pr_body",
    "notify_em",
    "open_self_fix_pr",
    # Verifier.
    "VerificationResult",
    "verify_on_merge",
    # Continuous critique.
    "CritiqueResult",
    "Finding",
    "critique_recent_output",
]
