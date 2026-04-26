# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The self-correction loop orchestrator (§7.4 steps 1-10).

:class:`FeedbackLoopDriver.process_pending_feedback` picks one ``pending``
feedback row, runs triage → plan → (optional EM review) → fix → test →
review → publish → wait → verify, and transitions the row through the
states declared in §7.3. Each step is a thin wrapper around the module
it lives in (:mod:`triage`, :mod:`planner`, :mod:`fixer`, :mod:`publisher`,
:mod:`verifier`) so tests can mock one stage without monkey-patching a
mass of internals.

State machine (matches §7.3):

    pending → triaged → in-fix → fix-pr-open → verified | rejected → closed

Each transition is also appended to ``feedback.notes_json`` so the full
history is recoverable from the row alone.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence

from gaia.coder.self_fix.fixer import (
    DEFAULT_BASE_REF,
    SELF_FIX_BRANCH_PREFIX,
    Diff,
    EditHunk,
    generate_fix,
    verify_test_differential,
    write_regression_test,
)
from gaia.coder.self_fix.planner import (
    Plan,
    draft_plan,
    is_large_job,
    request_em_approval,
)
from gaia.coder.self_fix.publisher import (
    PRHandle,
    ReviewGateResult,
    notify_em,
    open_self_fix_pr,
)
from gaia.coder.self_fix.triage import (
    FixClassResult,
    LocalisationHit,
    TriageContext,
    classify_fix_class,
    localise,
)
from gaia.coder.self_fix.verifier import VerificationResult, verify_on_merge
from gaia.coder.stores import feedback as feedback_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopDriverConfig:
    """Construction-time config for :class:`FeedbackLoopDriver`."""

    repo_root: Path
    feedback_db_path: Path
    memory_db_path: Path
    em_config: Mapping[str, Any] = field(default_factory=dict)
    base_ref: str = DEFAULT_BASE_REF
    plan_review_loc_threshold: int = 200


@dataclass
class DriveResult:
    """Summary of one ``process_pending_feedback`` call."""

    feedback_id: Optional[str]
    final_state: str
    plan: Optional[Plan] = None
    fix_class: Optional[FixClassResult] = None
    localisation: tuple[LocalisationHit, ...] = ()
    diff: Optional[Diff] = None
    regression_test_path: Optional[str] = None
    pr: Optional[PRHandle] = None
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_notes(existing: str, entry: Mapping[str, Any]) -> str:
    """Append a state-transition note to the feedback row's JSON array.

    ``notes_json`` is the canonical audit trail per the module docstring —
    silently replacing corrupted / mistyped content with ``[]`` would hide
    a real regression. Fail loudly per CLAUDE.md. Cf. #825 auto-review.
    """
    if not existing:
        parsed: list = []
    else:
        try:
            parsed = json.loads(existing)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"feedback notes_json is corrupted and cannot be extended: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise ValueError(
                f"feedback notes_json must be a JSON array, got "
                f"{type(parsed).__name__}"
            )
    parsed.append(dict(entry))
    return json.dumps(parsed)


def _load_pending_feedback(db_path: Path):
    """Return the oldest row where ``state='pending'``, else ``None``."""
    conn = feedback_store.open_store(db_path)
    try:
        rows = feedback_store.list_rows(conn, {"state": "pending"})
        if not rows:
            return None, conn
        # ``list_rows`` orders by received_at DESC — we want oldest first.
        return rows[-1], conn
    except Exception:
        conn.close()
        raise


def _transition(
    conn, feedback_id: str, patch: Mapping[str, Any], *, note: Mapping[str, Any]
):
    """Apply ``patch`` to the feedback row and append a transition note."""
    row = feedback_store.get_row(conn, feedback_id)
    if row is None:
        raise ValueError(f"feedback {feedback_id!r} not found")
    new_notes = _append_notes(row.notes_json, note)
    full_patch = dict(patch)
    full_patch["notes_json"] = new_notes
    feedback_store.update_row(conn, feedback_id, full_patch)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class FeedbackLoopDriver:
    """Orchestrate one iteration of the §7.4 self-correction loop."""

    def __init__(
        self,
        config: LoopDriverConfig,
        *,
        triage_client: Optional[Callable[..., str]] = None,
        edit_hunk_planner: Optional[Callable[..., Sequence[EditHunk]]] = None,
        gh_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
        inbox_writer: Optional[Any] = None,
        review_gate_runner: Optional[Callable[..., ReviewGateResult]] = None,
        skip_differential_verify: bool = False,
        skip_fix_apply: bool = False,
    ) -> None:
        """Construct a driver.

        Args:
            config: Paths + EM config.
            triage_client: LLM call injected into :func:`classify_fix_class`.
            edit_hunk_planner: Callable returning the concrete edit hunks the
                fixer will apply. When omitted, :meth:`_default_edit_hunks`
                drives :class:`gaia.coder.llm.CoderLLM` against the
                ``prompts/edit_hunks.md`` template (production path). Tests
                inject a deterministic stub here so they never hit the LLM —
                see ``tests/coder/test_self_fix/test_loop_driver.py``.
            gh_runner: ``gh`` subprocess shim for tests.
            inbox_writer: Phase-5 trust inbox ``enqueue`` — optional.
            review_gate_runner: Phase-4 review-gate runner — optional. When
                omitted, the PR body is published with a "review gate not
                available" placeholder (§7.4 step 6).
            skip_differential_verify: Bypass the fail-then-pass check in
                tests that cannot check out between refs.
            skip_fix_apply: Skip the edit-hunk application entirely — used by
                end-to-end tests that only need branch creation + PR opening.
        """
        self.config = config
        self._triage_client = triage_client
        self._edit_hunk_planner = edit_hunk_planner or self._default_edit_hunks
        self._gh_runner = gh_runner
        self._inbox_writer = inbox_writer
        self._review_gate_runner = review_gate_runner
        self._skip_differential_verify = skip_differential_verify
        self._skip_fix_apply = skip_fix_apply

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def process_pending_feedback(self) -> DriveResult:
        """Pull one pending row and drive it through the loop. Idempotent.

        Returns a :class:`DriveResult` whose ``final_state`` is one of:
        ``'no-pending'``, ``'rejected'``, ``'fix-pr-open'``, ``'verified'``.
        """
        row_and_conn = _load_pending_feedback(self.config.feedback_db_path)
        pending, conn = row_and_conn
        try:
            if pending is None:
                return DriveResult(feedback_id=None, final_state="no-pending")
            result = self._drive_one(conn, pending)
            return result
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _drive_one(self, conn, row) -> DriveResult:
        """Run the full pipeline for ``row``."""
        drive = DriveResult(feedback_id=row.id, final_state="pending")

        # --- Step 1: triage -------------------------------------------------
        fix_class = classify_fix_class(
            row.body,
            TriageContext(
                feedback_id=row.id,
                received_at=row.received_at,
                from_handle=row.from_handle,
                severity=row.severity,
                context_url=row.context_url,
            ),
            client=self._triage_client,
        )
        drive.fix_class = fix_class
        _transition(
            conn,
            row.id,
            {
                "state": "triaged",
                "fix_class": fix_class.fix_class,
                "root_cause": fix_class.root_cause_hypothesis,
            },
            note={
                "at": _now_iso(),
                "transition": "pending → triaged",
                "confidence": fix_class.confidence,
                "escalated_low_confidence": fix_class.escalated_low_confidence,
            },
        )
        drive.notes.append(
            f"triaged as {fix_class.fix_class} "
            f"(conf={fix_class.confidence}, esc={fix_class.escalated_low_confidence})"
        )
        if fix_class.fix_class == "out-of-scope":
            _transition(
                conn,
                row.id,
                {"state": "rejected"},
                note={
                    "at": _now_iso(),
                    "transition": "triaged → rejected",
                    "reason": (
                        "classifier returned out-of-scope or escalated low "
                        "confidence"
                    ),
                },
            )
            drive.final_state = "rejected"
            return drive

        # --- Step 2: localise ----------------------------------------------
        hits = tuple(
            localise(
                fix_class.fix_class,
                fix_class.candidate_files,
                repo_root=self.config.repo_root,
                keywords=_keywords_from_body(row.body),
            )
        )
        drive.localisation = hits
        drive.notes.append(f"localised {len(hits)} hit(s)")

        # --- Step 3: plan --------------------------------------------------
        plan = draft_plan(
            feedback={"id": row.id, "body": row.body, "severity": row.severity},
            localised_hits=hits,
            fix_class=fix_class,
        )
        drive.plan = plan
        _transition(
            conn,
            row.id,
            {
                "state": "in-fix",
                "success_criterion": plan.success_criterion,
            },
            note={
                "at": _now_iso(),
                "transition": "triaged → in-fix",
                "plan_loc_estimate": plan.total_loc_estimate,
            },
        )

        # --- Step 3b: optional EM approval ---------------------------------
        if is_large_job(plan, self.config.plan_review_loc_threshold):
            approval = request_em_approval(
                plan,
                self.config.em_config,
                inbox_writer=self._inbox_writer,
            )
            drive.notes.append(
                "posted EM plan-review message "
                f"(deferred={approval.deferred}, inbox_id={approval.inbox_id})"
            )

        # --- Step 4: generate fix ------------------------------------------
        hunks: Sequence[EditHunk]
        if self._skip_fix_apply:
            # Only create the branch via a direct git call — useful for tests
            # that cannot prepare real edit hunks.
            self._create_branch_only(row.id)
            drive.diff = Diff(
                feedback_id=row.id,
                branch=f"{SELF_FIX_BRANCH_PREFIX}/{row.id}",
                files_edited=(),
            )
        else:
            hunks = self._edit_hunk_planner(plan=plan, fix_class=fix_class, hits=hits)
            drive.diff = generate_fix(
                plan=plan,
                fix_class=fix_class,
                edits=hunks,
                repo_root=self.config.repo_root,
                base_ref=self.config.base_ref,
            )
        drive.notes.append(f"fix branch {drive.diff.branch}")

        # --- Step 5: regression test ---------------------------------------
        test_path = write_regression_test(
            plan=plan,
            changed_files=drive.diff.files_edited,
            repo_root=self.config.repo_root,
        )
        drive.regression_test_path = test_path.path

        # Differential check (§7.4 step 5) — skipped in e2e tests that cannot
        # check out between refs.
        if not self._skip_differential_verify:
            verify_test_differential(
                test_path=test_path.path,
                base_ref=self.config.base_ref,
                fix_branch=drive.diff.branch,
                repo_root=self.config.repo_root,
            )
            drive.notes.append("differential verified (base FAIL → fix PASS)")

        # --- Step 6: review gate (loose-coupled to Phase 4) ----------------
        review_gate_result: Optional[ReviewGateResult] = None
        if self._review_gate_runner is not None:
            try:
                review_gate_result = self._review_gate_runner(
                    diff=drive.diff,
                    plan=plan,
                    feedback_body=row.body,
                )
                drive.notes.append(f"review gate overall={review_gate_result.overall}")
            except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
                # Loose coupling to Phase 4: a runtime/subprocess/IO problem
                # in the review gate must not block a Phase-6 drive.
                # Programming errors (AttributeError/TypeError/etc.) still
                # surface per CLAUDE.md fail-loudly. Cf. #825 auto-review.
                logger.warning(
                    "review gate runner failed for feedback %s: %s",
                    row.id,
                    exc,
                )

        # --- Step 7: publish PR --------------------------------------------
        pr = open_self_fix_pr(
            fix_branch=drive.diff.branch,
            feedback_id=row.id,
            plan=plan,
            review_gate_result=review_gate_result,
            feedback_body=row.body,
            em_handle=str(
                self.config.em_config.get("em_handle") or row.from_handle or "em"
            ),
            context_url=row.context_url,
            regression_test_path=test_path.path,
            repo_root=self.config.repo_root,
            base=self.config.base_ref,
            draft=True,
            gh_runner=self._gh_runner,
        )
        drive.pr = pr
        _transition(
            conn,
            row.id,
            {
                "state": "fix-pr-open",
                "fix_pr_url": pr.url,
                "regression_test_path": test_path.path,
            },
            note={
                "at": _now_iso(),
                "transition": "in-fix → fix-pr-open",
                "pr_number": pr.number,
            },
        )

        # --- Step 8: notify EM ---------------------------------------------
        try:
            notify_em(
                pr_url=pr.url,
                feedback_id=row.id,
                context_url=row.context_url,
                repo_root=self.config.repo_root,
                gh_runner=self._gh_runner,
            )
            drive.notes.append("notified EM")
        except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
            # Same shape as the review-gate block: tolerate runtime / subprocess
            # / IO failures of the notification step without discarding the
            # open PR. Programming errors still surface. Cf. #825 auto-review.
            logger.warning("notify_em failed for feedback %s: %s", row.id, exc)
            drive.notes.append(f"notify_em failed: {exc}")

        drive.final_state = "fix-pr-open"
        return drive

    # ------------------------------------------------------------------
    # Step-10 verifier wiring (called by EventBridge on merge)
    # ------------------------------------------------------------------

    def verify_merged(
        self,
        merged_sha: str,
        feedback_id: str,
        regression_test_path: str,
    ) -> VerificationResult:
        """Thin pass-through to :func:`verify_on_merge`."""
        return verify_on_merge(
            merged_sha=merged_sha,
            feedback_id=feedback_id,
            regression_test_path=regression_test_path,
            repo_root=self.config.repo_root,
            feedback_db_path=self.config.feedback_db_path,
            memory_db_path=self.config.memory_db_path,
        )

    # ------------------------------------------------------------------
    # Fix-generation helpers
    # ------------------------------------------------------------------

    def _default_edit_hunks(
        self,
        *,
        plan: Plan,
        fix_class: FixClassResult,
        hits: Sequence[LocalisationHit],
    ) -> Sequence[EditHunk]:
        """LLM-driven hunk planner — the production default.

        Renders ``src/gaia/coder/prompts/edit_hunks.md`` with the plan, the
        triage classification, and the localised hits, then asks
        :class:`gaia.coder.llm.CoderLLM` for a JSON list of concrete edit
        hunks. The response is parsed with :func:`json.loads` and validated
        against the constraints in the prompt's ``<output_contract>``:

        * ``edits`` must be a JSON array of objects.
        * Each object must carry at least ``path`` / ``old_string`` /
          ``new_string`` (``replace_all`` defaults to ``False``).
        * ``path`` must match a path that appeared in the localised hits —
          we refuse to edit files the triage step never proposed.

        Any violation raises :class:`RuntimeError` with the first 500
        characters of the raw response embedded so the caller has something
        actionable to debug — fail-loudly per ``CLAUDE.md``.

        Tests substitute this default by passing ``edit_hunk_planner=`` to
        :class:`FeedbackLoopDriver` (see ``tests/coder/test_self_fix/
        test_loop_driver.py``) so they never hit a real LLM.
        """
        if not hits:
            raise RuntimeError(
                "_default_edit_hunks: no localised hits — triage did not "
                "produce any candidate files for the planner to edit. "
                "Either inject `edit_hunk_planner=` or fix the upstream "
                "localisation step."
            )

        prompt = _render_edit_hunks_prompt(plan=plan, fix_class=fix_class, hits=hits)

        # Lazy import — same rationale as the triage / critique defaults:
        # the loop_driver module stays importable on hosts without the
        # anthropic SDK installed.
        from gaia.coder.llm import default_completion_client

        raw = default_completion_client(prompt=prompt, max_tokens=4096)
        return _parse_edit_hunks_response(raw, allowed_paths={h.path for h in hits})

    def _create_branch_only(self, feedback_id: str) -> str:
        """Create the self-fix branch without applying any edits."""
        branch = f"{SELF_FIX_BRANCH_PREFIX}/{feedback_id}"
        cwd = self.config.repo_root
        completed = subprocess.run(  # pylint: disable=subprocess-run-check
            ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            subprocess.run(  # pylint: disable=subprocess-run-check
                ["git", "checkout", self.config.base_ref],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(  # pylint: disable=subprocess-run-check
                ["git", "checkout", "-b", branch, self.config.base_ref],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            subprocess.run(  # pylint: disable=subprocess-run-check
                ["git", "checkout", branch],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                check=True,
            )
        return branch


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _keywords_from_body(body: str, *, max_keywords: int = 5) -> List[str]:
    """Pluck a handful of keywords from the feedback body for grep seeding.

    Dumb but effective — strip punctuation, lowercase, skip stopwords, take
    the longest tokens. Good enough for Phase 6 localisation; a later phase
    may swap in something smarter.
    """
    stop = {
        "the",
        "and",
        "that",
        "with",
        "have",
        "from",
        "this",
        "there",
        "which",
        "your",
        "you",
        "about",
        "into",
        "when",
        "what",
        "been",
        "were",
        "does",
        "should",
    }
    tokens: List[str] = []
    for raw in body.split():
        cleaned = "".join(ch.lower() for ch in raw if ch.isalnum() or ch == "_")
        if len(cleaned) >= 4 and cleaned not in stop:
            tokens.append(cleaned)
    # Preserve original order, drop duplicates, take the longest tokens.
    seen: set[str] = set()
    ordered: List[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        ordered.append(t)
    ordered.sort(key=len, reverse=True)
    return ordered[:max_keywords]


# ---------------------------------------------------------------------------
# Edit-hunks prompt rendering + response parsing
# ---------------------------------------------------------------------------


_EDIT_HUNKS_PROMPT_PATH: Path = (
    Path(__file__).resolve().parent.parent / "prompts" / "edit_hunks.md"
)


def _render_edit_hunks_prompt(
    *,
    plan: Plan,
    fix_class: FixClassResult,
    hits: Sequence[LocalisationHit],
) -> str:
    """Render ``prompts/edit_hunks.md`` for the LLM-driven hunk planner.

    Substitutes the double-brace placeholders in the canonical prompt with
    a stable, deterministic projection of the plan + hits — same convention
    used by ``prompts/triage.md`` and ``prompts/critique.md``.

    The ``hits_block`` slot lists every hit as ``<path>:<start>-<end>``
    followed by its snippet inside a fenced code block; this is what the
    prompt's self-check ("does each ``old_string`` appear inside one of the
    localised snippets?") actually verifies.
    """
    if not _EDIT_HUNKS_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"prompt template missing: {_EDIT_HUNKS_PROMPT_PATH}. "
            "Create it under src/gaia/coder/prompts/."
        )
    template = _EDIT_HUNKS_PROMPT_PATH.read_text(encoding="utf-8")

    hit_blocks: List[str] = []
    for hit in hits:
        hit_blocks.append(
            f"### {hit.path}:{hit.line_start}-{hit.line_end}\n"
            "```\n"
            f"{hit.snippet}\n"
            "```"
        )
    hits_block = "\n\n".join(hit_blocks) if hit_blocks else "(no localised hits)"

    subs: dict[str, str] = {
        "feedback_id": plan.feedback_id,
        "fix_class": fix_class.fix_class,
        "root_cause": plan.root_cause,
        "proposed_change": plan.proposed_change,
        "success_criterion": plan.success_criterion,
        "hits_block": hits_block,
    }
    rendered = template
    for key, value in subs.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _parse_edit_hunks_response(
    raw: str,
    *,
    allowed_paths: set[str],
) -> List[EditHunk]:
    """Parse the JSON response from the edit-hunks LLM call.

    Validates against the prompt's ``<output_contract>``:

    * Must parse as a JSON object with an ``edits`` array.
    * Each entry must carry ``path`` / ``old_string`` / ``new_string`` of
      the right type; ``replace_all`` defaults to ``False``.
    * ``path`` must be in ``allowed_paths`` — i.e. a path the localisation
      step actually proposed. Reject silently invented files.
    * The list may be empty, but each entry that *is* present must be
      complete.

    On any violation raises :class:`RuntimeError` with the first 500
    characters of the raw response embedded — fail-loudly per CLAUDE.md.
    The 500-char cap protects logs / audit trail from runaway responses.
    """
    snippet = raw[:500]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"edit_hunks response is not valid JSON: {exc}. "
            f"First 500 chars of response: {snippet!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(
            "edit_hunks response must be a JSON object with an 'edits' key. "
            f"First 500 chars of response: {snippet!r}"
        )
    edits_raw = parsed.get("edits")
    if not isinstance(edits_raw, list):
        raise RuntimeError(
            "edit_hunks response 'edits' must be a JSON array. "
            f"First 500 chars of response: {snippet!r}"
        )

    out: List[EditHunk] = []
    for index, entry in enumerate(edits_raw):
        if not isinstance(entry, dict):
            raise RuntimeError(
                f"edit_hunks edits[{index}] must be a JSON object. "
                f"First 500 chars of response: {snippet!r}"
            )
        path = entry.get("path")
        old_string = entry.get("old_string")
        new_string = entry.get("new_string")
        replace_all = entry.get("replace_all", False)
        if not isinstance(path, str) or not path:
            raise RuntimeError(
                f"edit_hunks edits[{index}].path must be a non-empty string. "
                f"First 500 chars of response: {snippet!r}"
            )
        if path not in allowed_paths:
            raise RuntimeError(
                f"edit_hunks edits[{index}].path={path!r} is not one of the "
                f"localised paths {sorted(allowed_paths)!r}. The model is "
                "not allowed to invent files. "
                f"First 500 chars of response: {snippet!r}"
            )
        if not isinstance(old_string, str) or not old_string:
            raise RuntimeError(
                f"edit_hunks edits[{index}].old_string must be a non-empty "
                f"string. First 500 chars of response: {snippet!r}"
            )
        if not isinstance(new_string, str):
            raise RuntimeError(
                f"edit_hunks edits[{index}].new_string must be a string. "
                f"First 500 chars of response: {snippet!r}"
            )
        if not isinstance(replace_all, bool):
            raise RuntimeError(
                f"edit_hunks edits[{index}].replace_all must be a bool. "
                f"First 500 chars of response: {snippet!r}"
            )
        out.append(
            EditHunk(
                path=path,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            )
        )
    return out


__all__ = [
    "DriveResult",
    "FeedbackLoopDriver",
    "LoopDriverConfig",
]
