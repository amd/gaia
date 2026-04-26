# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Plan drafting, large-job gating, and EM-approval request (§5.1 Stage 3, §7.4 step 3).

The Stage 3 plan captures:

* root cause (from triage),
* proposed change (file + operation + why),
* regression test sketch,
* LoC estimate,
* alternatives considered,
* expected cost (tokens / USD / wall-clock).

Small jobs skip ``plan_review``. Large jobs (> ``plan_review_loc_threshold``)
post a :data:`P3` template message into the EM inbox and wait for ✅. The
approval primitive here is loosely coupled to the Phase-5 trust module: if
``gaia.coder.trust.inbox`` is importable we write through it, otherwise we
return an :class:`ApprovalRequest` that the caller can persist itself and
emit a WARN.
"""

from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from gaia.coder.self_fix.triage import FixClassResult, LocalisationHit
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Default large-job threshold (§5.1 Stage 3). EM can override via ``em.toml``
#: (not wired in Phase 6 — the caller supplies a threshold explicitly).
DEFAULT_LARGE_JOB_LOC_THRESHOLD: int = 200

#: Plan refinement is capped at three rounds before the task surfaces as
#: stuck (§5.1 Stage 3 ``plan_refine`` row).
MAX_PLAN_REFINEMENT_ROUNDS: int = 3

#: Fix-classes that always trigger ``plan_review`` regardless of LoC.
ALWAYS_LARGE_FIX_CLASSES: frozenset[str] = frozenset({"architectural", "state-machine"})


# ---------------------------------------------------------------------------
# Plan dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileTouchPlan:
    """One file + LoC estimate + operation."""

    path: str
    loc_estimate: int
    operation: str = "edit"  # edit | create | delete


@dataclass(frozen=True)
class CostEstimate:
    """Rough pre-flight cost envelope (§15.8 P3)."""

    tokens: int
    usd: float
    wall_clock_minutes: float


@dataclass(frozen=True)
class Plan:
    """Stage 3 §5.1 plan."""

    feedback_id: str
    fix_class: str
    root_cause: str
    proposed_change: str
    regression_test_sketch: str
    files: Tuple[FileTouchPlan, ...]
    alternatives_considered: Tuple[str, ...]
    risks: Tuple[str, ...]
    success_criterion: str
    cost_estimate: CostEstimate
    refinement_round: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def total_loc_estimate(self) -> int:
        """Sum of LoC estimates across every planned file touch."""
        return sum(f.loc_estimate for f in self.files)


@dataclass(frozen=True)
class ApprovalRequest:
    """What :func:`request_em_approval` hands back to the caller.

    Attributes:
        inbox_id: EM-inbox row id (UUID) if the inbox was writable; ``None``
            when the write was deferred (Phase 5 not yet landed).
        body: The fully-rendered P3 message body that was posted (or would
            have been posted).
        posted_at: Timestamp of the inbox write.
        deferred: ``True`` iff we could not actually write to the inbox and
            the caller must retry / surface manually.
    """

    inbox_id: Optional[str]
    body: str
    posted_at: str
    deferred: bool


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


def draft_plan(
    feedback: Mapping[str, Any],
    localised_hits: Sequence[LocalisationHit],
    fix_class: FixClassResult,
    *,
    proposed_change: Optional[str] = None,
    regression_test_sketch: Optional[str] = None,
    alternatives_considered: Sequence[str] = (),
    risks: Sequence[str] = (),
    success_criterion: Optional[str] = None,
    cost_estimate: Optional[CostEstimate] = None,
    refinement_round: int = 0,
) -> Plan:
    """Synthesize a :class:`Plan` from triage + localisation output.

    This helper is deterministic — it does *not* call an LLM. The full
    Karpathy-Principle-4 planner that would compose this from memory hits
    and ADR recall is a Phase 7 deliverable; for Phase 6 the caller fills in
    the creative slots (``proposed_change``, ``regression_test_sketch``,
    etc.) and this helper packages them into the canonical shape for the
    P3 template + downstream fixer.

    Args:
        feedback: A dict-like of the feedback row (``id``, ``body``,
            ``severity``, etc. — see :class:`gaia.coder.stores.feedback.FeedbackRow`).
        localised_hits: Grep hits from :func:`gaia.coder.self_fix.triage.localise`.
        fix_class: The triage classifier's result.
        proposed_change: Free-text description of the diff the fixer will
            write. If omitted, synthesised from the hits.
        regression_test_sketch: Free-text test description. If omitted, a
            minimal placeholder is used ("add pytest covering <fix_class>
            regression for <feedback_id>"). The fixer later materialises
            this into a concrete ``tests/...`` file.
        alternatives_considered / risks: Planner-author-supplied bullet
            text. Default empty tuples.
        success_criterion: Stage-3 success line. Defaults to "feedback
            <fid> marked verified" — what §7.4 step 10 actually checks.
        cost_estimate: Pre-flight cost envelope. Default is a conservative
            60k-token / $1.20 / 12-minute estimate.
        refinement_round: ``0`` for the first draft, incremented on each
            ``plan_refine → plan_draft`` cycle. Caller enforces the
            three-round cap.
    """
    if "id" not in feedback or "body" not in feedback:
        raise ValueError(
            "draft_plan: feedback dict must carry at least 'id' and 'body' "
            f"(got keys: {sorted(feedback.keys())})"
        )

    feedback_id = str(feedback["id"])
    hit_files = sorted({h.path for h in localised_hits})
    if proposed_change is None:
        proposed_change = (
            f"Apply {fix_class.fix_class} fix based on root cause "
            f"'{fix_class.root_cause_hypothesis.strip()[:160]}' — "
            f"touching {', '.join(hit_files) or '(no files located)'}."
        )
    if regression_test_sketch is None:
        regression_test_sketch = (
            f"Add pytest covering the {fix_class.fix_class} regression for "
            f"feedback {feedback_id}: reproduce the reported behaviour, assert "
            "it is fixed on the self-fix branch."
        )
    if success_criterion is None:
        success_criterion = (
            f"Feedback {feedback_id} transitions to 'verified' after a green "
            "pytest run of the new regression test on the merged SHA."
        )
    if cost_estimate is None:
        cost_estimate = CostEstimate(tokens=60_000, usd=1.2, wall_clock_minutes=12.0)

    files = tuple(
        FileTouchPlan(path=path, loc_estimate=_estimate_loc_for(path, localised_hits))
        for path in hit_files
    )
    return Plan(
        feedback_id=feedback_id,
        fix_class=fix_class.fix_class,
        root_cause=fix_class.root_cause_hypothesis,
        proposed_change=proposed_change,
        regression_test_sketch=regression_test_sketch,
        files=files,
        alternatives_considered=tuple(alternatives_considered),
        risks=tuple(risks),
        success_criterion=success_criterion,
        cost_estimate=cost_estimate,
        refinement_round=refinement_round,
    )


def _estimate_loc_for(path: str, hits: Sequence[LocalisationHit]) -> int:
    """Rough-in a LoC estimate from the span of located lines in ``path``.

    Heuristic: for each hit, take ``(line_end - line_start + 1)`` as the
    inner edit span and pad with 5 lines for surrounding context; sum across
    hits in ``path``. Minimum 10 LoC — a single-line change still requires
    import-update / test-scaffold chunks.
    """
    per_file = [h for h in hits if h.path == path]
    if not per_file:
        return 10
    total = 0
    for hit in per_file:
        span = max(1, hit.line_end - hit.line_start + 1)
        total += span + 5
    return max(10, total)


# ---------------------------------------------------------------------------
# Large-job gate
# ---------------------------------------------------------------------------


def is_large_job(
    plan: Plan,
    threshold_loc: int = DEFAULT_LARGE_JOB_LOC_THRESHOLD,
) -> bool:
    """Return ``True`` if the plan needs an EM ✅ before proceeding.

    A plan is "large" (§5.1 Stage 3) when any of:

    * total LoC estimate exceeds ``threshold_loc`` (default 200),
    * plan touches more than one file across different mixins,
    * fix-class is ``architectural`` or ``state-machine``.

    The cross-mixin rule is intentionally conservative: a single-file change
    that crosses more than one directory layer (``src/gaia/coder/review/...``
    + ``src/gaia/coder/self_fix/...``) is treated as large.
    """
    if plan.fix_class in ALWAYS_LARGE_FIX_CLASSES:
        return True
    if plan.total_loc_estimate > threshold_loc:
        return True
    distinct_top_dirs = {_top_dir_of(f.path) for f in plan.files if f.path}
    if len(distinct_top_dirs) >= 2 and len(plan.files) >= 2:
        return True
    return False


def _top_dir_of(path: str) -> str:
    """Return a path prefix coarse enough to separate mixin-level directories.

    For coder paths (``src/gaia/coder/<mixin>/...``) we want ``<mixin>`` to be
    the distinguishing segment — two files under ``src/gaia/coder/review/``
    and ``src/gaia/coder/self_fix/`` must be reported as *different* top
    dirs so the planner flags them as cross-mixin. We therefore take the
    first four segments when available (which captures the mixin name) and
    fall back to whatever segments are present on shorter paths.
    """
    parts = [p for p in Path(path).parts if p and p not in ("/", ".")]
    if len(parts) >= 4:
        return "/".join(parts[:4])
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else path


# ---------------------------------------------------------------------------
# EM approval request (P3 template, EM-inbox write)
# ---------------------------------------------------------------------------


_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _render_p3(plan: Plan) -> str:
    """Render the P3 review message from ``prompts/plan_review.md``."""
    path = _PROMPTS_DIR / "plan_review.md"
    template = path.read_text(encoding="utf-8")
    file_lines = (
        "\n".join(
            f"- `{f.path}` — ~{f.loc_estimate} LoC ({f.operation})" for f in plan.files
        )
        or "- (no files planned yet)"
    )
    alternatives = "\n".join(f"- {a}" for a in plan.alternatives_considered) or (
        "- (none recorded — Phase 6 plans rarely have alternatives)"
    )
    risks = "\n".join(f"- {r}" for r in plan.risks) or "- (no major risks identified)"
    approach = "\n".join(
        f"- {line}" for line in plan.proposed_change.split(". ") if line
    )
    substitutions: dict[str, str] = {
        "task_description": plan.proposed_change,
        "concrete_success_criterion": plan.success_criterion,
        "approach_bullets": approach or f"- {plan.proposed_change}",
        "file_list_with_LoC_estimate": file_lines,
        "alternatives_with_rejection": alternatives,
        "test_description": plan.regression_test_sketch,
        "tokens": f"{plan.cost_estimate.tokens:,}",
        "usd": f"{plan.cost_estimate.usd:.2f}",
        "minutes": f"{plan.cost_estimate.wall_clock_minutes:.0f}",
        "top_2_risks": risks,
    }
    rendered = template
    for key, value in substitutions.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def request_em_approval(
    plan: Plan,
    em_config: Mapping[str, Any],
    *,
    inbox_writer: Optional[Any] = None,
) -> ApprovalRequest:
    """Post the P3 plan-review message to the EM inbox and wait for ✅.

    "Waiting" is asynchronous in the full daemon — this function does **not**
    block. It only writes to the inbox; the loop driver polls for the reply
    on later ticks.

    Loose coupling to Phase 5 trust: if ``inbox_writer`` is supplied or
    ``gaia.coder.trust.inbox`` is importable, we call
    ``inbox_writer.enqueue(...)``. Otherwise we log a WARN and return a
    deferred :class:`ApprovalRequest` carrying the rendered body so the
    caller can persist it themselves.
    """
    body = _render_p3(plan)
    posted_at = datetime.now(timezone.utc).isoformat()
    em_handle = str(em_config.get("em_handle") or "unknown-em")

    writer = inbox_writer
    if writer is None:
        try:  # loose coupling: Phase 5 may or may not have landed yet.
            trust_inbox = importlib.import_module("gaia.coder.trust.inbox")
            writer = getattr(trust_inbox, "enqueue", None)
        except ImportError:
            writer = None

    if writer is None:
        logger.warning(
            "request_em_approval: no EM inbox writer available "
            "(gaia.coder.trust.inbox not yet landed); deferring message for "
            "feedback=%s",
            plan.feedback_id,
        )
        return ApprovalRequest(
            inbox_id=None,
            body=body,
            posted_at=posted_at,
            deferred=True,
        )

    inbox_id = str(uuid.uuid4())
    try:
        # Writer contract matches `gaia.coder.trust.inbox.enqueue(id=..., ...)`
        # from the Phase 5 plan; any extra kwargs are forwarded as metadata.
        writer(
            id=inbox_id,
            from_handle=em_handle,
            severity="question",
            channel="cli",
            body=body,
            metadata={
                "kind": "plan_review",
                "feedback_id": plan.feedback_id,
                "fix_class": plan.fix_class,
            },
        )
    except TypeError:
        # Alternate writer shape: positional ``(body, severity)``.
        writer(body, "question")
    return ApprovalRequest(
        inbox_id=inbox_id,
        body=body,
        posted_at=posted_at,
        deferred=False,
    )


__all__ = [
    "ApprovalRequest",
    "ALWAYS_LARGE_FIX_CLASSES",
    "CostEstimate",
    "DEFAULT_LARGE_JOB_LOC_THRESHOLD",
    "FileTouchPlan",
    "MAX_PLAN_REFINEMENT_ROUNDS",
    "Plan",
    "draft_plan",
    "is_large_job",
    "request_em_approval",
]
