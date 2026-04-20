# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SelfFixToolsMixin — registers the §7.4 self-correction tools on an agent.

Phase 6 scope: the "outward-facing" tools that drive the feedback-loop
stages (triage, planner, fixer, publisher, verifier, continuous critique).

§15.2 lists seven tools: ``classify_failure``, ``pause_current_task``,
``resume_task``, ``restart_self``, ``edit_self_file``, ``bump_loop_version``,
``record_self_fix_pr``. Of those, ``restart_self``, ``pause_current_task`` /
``resume_task``, and ``edit_self_file`` are explicitly Phase 7 work
(dev-mode self-edit, §7.5). ``classify_failure`` is the P8 classifier,
which lives in a separate module in Phase 7.

For Phase 6 we register nine *loop-level* tools — the ones the feedback
loop actually calls — so the mixin has a concrete, testable surface. The
Phase-7 tools are registered by their own mixin later (``DevModeToolsMixin``
when it lands).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional

from gaia.agents.base.tools import tool
from gaia.coder.self_fix.continuous_critique import (
    CritiqueResult,
    critique_recent_output,
)
from gaia.coder.self_fix.fixer import (
    DEFAULT_BASE_REF,
    EditHunk,
    generate_fix,
    verify_test_differential,
    write_regression_test,
)
from gaia.coder.self_fix.planner import (
    Plan,
    draft_plan,
    is_large_job,
)
from gaia.coder.self_fix.publisher import open_self_fix_pr
from gaia.coder.self_fix.triage import (
    CandidateFile,
    FixClassResult,
    LocalisationHit,
    TriageContext,
    classify_fix_class,
    localise,
)
from gaia.coder.stores import feedback as feedback_store

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SelfFixToolsMixin:
    """Register the Phase-6 self-correction tools on an agent.

    Call :meth:`register_self_fix_tools` during agent bootstrap. All nine
    tools run via ``@tool``-decorated closures so they appear in the agent's
    tool registry the same way every other GAIA tool does.
    """

    def register_self_fix_tools(self) -> List[str]:
        """Register the self-fix loop tools and return their names.

        The return value helps ``test_smoke_selffix_mixin_registers_tools``
        assert the ≥ 7 contract without inspecting the registry's private
        state.
        """

        registered: List[str] = []

        @tool
        def triage_feedback(
            feedback_id: str,
            body: str,
            received_at: str,
            from_handle: str,
            severity: str,
            context_url: Optional[str] = None,
        ) -> Mapping[str, Any]:
            """Classify a feedback record into one of the eight §7.4 fix classes.

            Runs prompt P1 on Opus 4.7; a TriageClient must be wired elsewhere.
            """
            result = classify_fix_class(
                body,
                TriageContext(
                    feedback_id=feedback_id,
                    received_at=received_at,
                    from_handle=from_handle,
                    severity=severity,
                    context_url=context_url,
                ),
            )
            return {
                "fix_class": result.fix_class,
                "confidence": result.confidence,
                "root_cause_hypothesis": result.root_cause_hypothesis,
                "candidate_files": [
                    {"path": c.path, "why": c.why} for c in result.candidate_files
                ],
                "prior_pattern_hit": result.prior_pattern_hit,
                "escalated_low_confidence": result.escalated_low_confidence,
            }

        registered.append("triage_feedback")

        @tool
        def localise_feedback(
            fix_class: str,
            candidate_files: List[Mapping[str, str]],
            repo_root: str,
            keywords: Optional[List[str]] = None,
        ) -> List[Mapping[str, Any]]:
            """Grep-localise candidate files to concrete ``file:line-range`` hits."""
            hits = localise(
                fix_class,
                [
                    CandidateFile(path=e["path"], why=e.get("why", ""))
                    for e in candidate_files
                ],
                repo_root=Path(repo_root),
                keywords=keywords or (),
            )
            return [
                {
                    "path": h.path,
                    "line_start": h.line_start,
                    "line_end": h.line_end,
                    "snippet": h.snippet,
                }
                for h in hits
            ]

        registered.append("localise_feedback")

        @tool
        def draft_fix_plan(
            feedback: Mapping[str, Any],
            localised_hits: List[Mapping[str, Any]],
            fix_class: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            """Assemble a Stage-3 plan from triage + localisation output."""
            hits = [
                LocalisationHit(
                    path=h["path"],
                    line_start=h["line_start"],
                    line_end=h["line_end"],
                    snippet=h["snippet"],
                )
                for h in localised_hits
            ]
            fc = FixClassResult(
                fix_class=fix_class["fix_class"],
                root_cause_hypothesis=fix_class.get("root_cause_hypothesis", ""),
                candidate_files=tuple(
                    CandidateFile(path=c["path"], why=c.get("why", ""))
                    for c in fix_class.get("candidate_files", [])
                ),
                prior_pattern_hit=fix_class.get("prior_pattern_hit"),
                confidence=int(fix_class.get("confidence", 100)),
            )
            plan = draft_plan(feedback=feedback, localised_hits=hits, fix_class=fc)
            return _plan_to_dict(plan)

        registered.append("draft_fix_plan")

        @tool
        def is_plan_large_job(
            plan: Mapping[str, Any], threshold_loc: int = 200
        ) -> bool:
            """Return True if the plan crosses the §5.1 Stage-3 review threshold."""
            return is_large_job(_dict_to_plan(plan), threshold_loc=threshold_loc)

        registered.append("is_plan_large_job")

        @tool
        def apply_self_fix(
            plan: Mapping[str, Any],
            edits: List[Mapping[str, Any]],
            repo_root: str,
            base_ref: str = DEFAULT_BASE_REF,
        ) -> Mapping[str, Any]:
            """Create the self-fix branch and apply ``edits``."""
            fx = _stub_fix_class(plan)
            hunks = [
                EditHunk(
                    path=e["path"],
                    old_string=e["old_string"],
                    new_string=e["new_string"],
                    replace_all=bool(e.get("replace_all", False)),
                )
                for e in edits
            ]
            diff = generate_fix(
                _dict_to_plan(plan),
                fx,
                hunks,
                repo_root=Path(repo_root),
                base_ref=base_ref,
            )
            return {
                "feedback_id": diff.feedback_id,
                "branch": diff.branch,
                "files_edited": list(diff.files_edited),
            }

        registered.append("apply_self_fix")

        @tool
        def write_self_fix_regression_test(
            plan: Mapping[str, Any],
            changed_files: List[str],
            repo_root: str,
        ) -> Mapping[str, Any]:
            """Write the required regression test on the current fix branch."""
            tp = write_regression_test(
                _dict_to_plan(plan),
                changed_files,
                repo_root=Path(repo_root),
            )
            return {"path": tp.path, "branch": tp.branch}

        registered.append("write_self_fix_regression_test")

        @tool
        def verify_differential(
            test_path: str,
            base_ref: str,
            fix_branch: str,
            repo_root: str,
        ) -> Mapping[str, Any]:
            """Enforce the fail-on-base / pass-on-fix contract (§7.4 step 5)."""
            res = verify_test_differential(
                test_path=test_path,
                base_ref=base_ref,
                fix_branch=fix_branch,
                repo_root=Path(repo_root),
            )
            return {
                "base_ref": res.base_ref,
                "fix_branch": res.fix_branch,
                "base_returncode": res.base_returncode,
                "fix_returncode": res.fix_returncode,
                "verified": res.verified,
            }

        registered.append("verify_differential")

        @tool
        def publish_self_fix_pr(
            fix_branch: str,
            feedback_id: str,
            plan: Mapping[str, Any],
            feedback_body: str,
            em_handle: str,
            regression_test_path: str,
            context_url: Optional[str] = None,
            repo_root: Optional[str] = None,
            base: str = DEFAULT_BASE_REF,
        ) -> Mapping[str, Any]:
            """Open the draft self-fix PR."""
            pr = open_self_fix_pr(
                fix_branch=fix_branch,
                feedback_id=feedback_id,
                plan=_dict_to_plan(plan),
                review_gate_result=None,
                feedback_body=feedback_body,
                em_handle=em_handle,
                context_url=context_url,
                regression_test_path=regression_test_path,
                repo_root=Path(repo_root) if repo_root else None,
                base=base,
                draft=True,
            )
            return {
                "number": pr.number,
                "url": pr.url,
                "branch": pr.branch,
                "draft": pr.draft,
            }

        registered.append("publish_self_fix_pr")

        @tool
        def record_self_fix_pr(
            pr_url: str,
            feedback_id: str,
            feedback_db_path: str,
        ) -> Mapping[str, Any]:
            """Stamp ``fix_pr_url`` on the feedback row (§15.2 tool)."""
            conn = feedback_store.open_store(Path(feedback_db_path))
            try:
                row = feedback_store.get_row(conn, feedback_id)
                if row is None:
                    raise ValueError(f"feedback {feedback_id!r} not found")
                feedback_store.update_row(
                    conn,
                    feedback_id,
                    {"fix_pr_url": pr_url},
                )
            finally:
                conn.close()
            return {"feedback_id": feedback_id, "fix_pr_url": pr_url}

        registered.append("record_self_fix_pr")

        @tool
        def critique_turn_output(
            success_criterion: str,
            recent_output: str,
            kind: str = "edit",
            gaia_md_principles: str = "",
        ) -> Mapping[str, Any]:
            """Run §7.2 continuous critique on the most recent state-changing tool output."""
            result: CritiqueResult = critique_recent_output(
                success_criterion=success_criterion,
                recent_output=recent_output,
                kind=kind,
                gaia_md_principles=gaia_md_principles,
            )
            return {
                "findings": [
                    {
                        "severity": f.severity,
                        "citation": f.citation,
                        "fix_direction": f.fix_direction,
                        "confidence": f.confidence,
                    }
                    for f in result.findings
                ],
                "most_impactful": (
                    {
                        "severity": result.most_impactful.severity,
                        "citation": result.most_impactful.citation,
                        "fix_direction": result.most_impactful.fix_direction,
                        "confidence": result.most_impactful.confidence,
                    }
                    if result.most_impactful is not None
                    else None
                ),
            }

        registered.append("critique_turn_output")

        logger.info(
            "SelfFixToolsMixin.register_self_fix_tools: registered %d tools",
            len(registered),
        )
        return registered


# ---------------------------------------------------------------------------
# Plan ↔ dict helpers (used by the registered tools so JSON goes both ways)
# ---------------------------------------------------------------------------


def _plan_to_dict(plan: Plan) -> Mapping[str, Any]:
    return {
        "feedback_id": plan.feedback_id,
        "fix_class": plan.fix_class,
        "root_cause": plan.root_cause,
        "proposed_change": plan.proposed_change,
        "regression_test_sketch": plan.regression_test_sketch,
        "files": [
            {"path": f.path, "loc_estimate": f.loc_estimate, "operation": f.operation}
            for f in plan.files
        ],
        "alternatives_considered": list(plan.alternatives_considered),
        "risks": list(plan.risks),
        "success_criterion": plan.success_criterion,
        "cost_estimate": {
            "tokens": plan.cost_estimate.tokens,
            "usd": plan.cost_estimate.usd,
            "wall_clock_minutes": plan.cost_estimate.wall_clock_minutes,
        },
        "refinement_round": plan.refinement_round,
        "created_at": plan.created_at,
    }


def _dict_to_plan(data: Mapping[str, Any]) -> Plan:
    from gaia.coder.self_fix.planner import CostEstimate, FileTouchPlan

    files = tuple(
        FileTouchPlan(
            path=f["path"],
            loc_estimate=int(f.get("loc_estimate", 10)),
            operation=f.get("operation", "edit"),
        )
        for f in data.get("files", [])
    )
    cost = data.get("cost_estimate") or {}
    return Plan(
        feedback_id=str(data["feedback_id"]),
        fix_class=str(data["fix_class"]),
        root_cause=str(data.get("root_cause", "")),
        proposed_change=str(data.get("proposed_change", "")),
        regression_test_sketch=str(data.get("regression_test_sketch", "")),
        files=files,
        alternatives_considered=tuple(data.get("alternatives_considered", [])),
        risks=tuple(data.get("risks", [])),
        success_criterion=str(data.get("success_criterion", "")),
        cost_estimate=CostEstimate(
            tokens=int(cost.get("tokens", 60_000)),
            usd=float(cost.get("usd", 1.2)),
            wall_clock_minutes=float(cost.get("wall_clock_minutes", 12.0)),
        ),
        refinement_round=int(data.get("refinement_round", 0)),
        created_at=data.get("created_at") or _now_iso(),
    )


def _stub_fix_class(plan_dict: Mapping[str, Any]) -> FixClassResult:
    return FixClassResult(
        fix_class=plan_dict["fix_class"],
        root_cause_hypothesis=plan_dict.get("root_cause", ""),
        candidate_files=(),
        prior_pattern_hit=None,
        confidence=100,
    )


__all__ = ["SelfFixToolsMixin"]
