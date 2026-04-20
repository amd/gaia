#  Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Debug sub-loop state machine (§5.9).

The main ReAct loop hands off to this sub-loop whenever ``verify_local`` or
``self_review`` surfaces a failure that can't be resolved by a trivial patch,
or when a task *is* a bug-fix (triage routes straight here). The sub-loop
returns control to the parent on one of two outcomes:

* ``propose_fix`` succeeds — parent resumes ``edit`` with the fix candidate.
* ``plan_draft`` required — parent resumes planning with the diagnosis as
  additional context (used when the fix needs architectural change).

Seven states mirror §5.9's table:

    reproduce → bisect → hypothesise → probe → localise_bug → propose_fix → postmortem

The class enforces the four discipline rules from the spec at the
state-machine layer (not just in prompts), so a caller that bypasses the
prompts still cannot land an undisciplined fix:

1. ``propose_fix`` refuses unless ``reproduced=True``.
2. ``propose_fix`` refuses unless top-hypothesis confidence ≥ 80%
   (override requires ``--shotgun`` + EM elevation).
3. ``hypothesise`` enforces ``len(hypotheses) >= 3``.
4. ``postmortem`` writes to ``feedback.db`` + ``failure_patterns`` memory.

The actual tool implementations live in :mod:`gaia.coder.tools.debug`
(Phase 8). This module only owns the control flow — it is deliberately
*not* wired into the main ReAct loop yet (Phase 11 production swap).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

from gaia.coder.stores import feedback as feedback_store
from gaia.coder.stores import memory as memory_store

logger = logging.getLogger(__name__)


#: Confidence threshold below which ``propose_fix`` refuses. §5.9 "Discipline
#: enforced by the loop": *"propose_fix refuses to run if the top-confidence
#: hypothesis is below 80%."*
PROPOSE_FIX_CONFIDENCE_THRESHOLD: int = 80

#: Minimum hypotheses §5.9 requires at ``hypothesise``. "Single-hypothesis
#: debugging is the most common self-deception pattern."
MIN_HYPOTHESES: int = 3

#: Hard cap on ``hypothesise → probe`` iterations before surfacing to the EM
#: as stuck. §5.9: "cap of 5 rounds before surfacing to EM as stuck."
MAX_DEBUG_ROUNDS: int = 5


class DebugState(str, Enum):
    """The seven debug sub-loop states (§5.9)."""

    REPRODUCE = "reproduce"
    BISECT = "bisect"
    HYPOTHESISE = "hypothesise"
    PROBE = "probe"
    LOCALISE_BUG = "localise_bug"
    PROPOSE_FIX = "propose_fix"
    POSTMORTEM = "postmortem"


class DebugDisciplineError(Exception):
    """Raised when a discipline rule would be violated.

    Subclass-free by design: the message carries the rule number and the
    fix direction (§5.9's discipline list). The caller either surfaces to
    the EM or retries the offending state.
    """


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Hypothesis:
    """One root-cause hypothesis generated at the ``hypothesise`` state.

    Attributes:
        text: One-line description ("off-by-one in the loop counter").
        confidence: 0-100. Updated at every ``probe`` iteration based on
            experiment outcomes.
        evidence: Free-text log of probes that confirmed/refuted this
            hypothesis.
    """

    text: str
    confidence: int = 0
    evidence: List[str] = field(default_factory=list)


@dataclass
class DebugContext:
    """Mutable context passed across sub-loop transitions.

    Kept as a plain dataclass (not a BaseModel) because it is read/written
    by the state-machine many times per task — Pydantic validation on every
    mutation is more expensive than we need. Validation happens at the
    state-transition boundary via the guards on :class:`DebugSubLoop`.
    """

    task_id: str
    feedback_id: str
    error_signature: str
    repro_command: str

    reproduced: bool = False
    repro_attempts: int = 0
    repro_attempts_matched: int = 0
    repro_actual_output: str = ""

    culprit_sha: Optional[str] = None
    bisect_log: str = ""
    bisect_skipped: bool = False

    hypotheses: List[Hypothesis] = field(default_factory=list)
    probes_run: int = 0
    round_count: int = 0

    localised_file: Optional[str] = None
    localised_line: Optional[int] = None

    fix_summary: Optional[str] = None
    fix_is_architectural: bool = False
    shotgun: bool = False

    postmortem_written: bool = False
    notes: List[str] = field(default_factory=list)

    def record_note(self, line: str) -> None:
        """Append a timestamped breadcrumb to :attr:`notes`."""
        self.notes.append(f"{_utc_now_iso()} :: {line}")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _stack_hash(error_signature: str) -> str:
    """Derive a short stable signature for a traceback string.

    Used as the ``stack_hash`` key on ``failure_patterns`` memory rows.
    Intentionally *not* a cryptographic hash — a readable-but-stable
    digest is friendlier in audit logs and memory lookups.
    """
    import hashlib

    return hashlib.sha1(
        error_signature.encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]


# ---------------------------------------------------------------------------
# The sub-loop
# ---------------------------------------------------------------------------


@dataclass
class DebugSubLoop:
    """Seven-state debug sub-loop (§5.9).

    Methods named after each state mutate the context and return the next
    :class:`DebugState`. The caller drives transitions explicitly so the
    loop composes with the main ReAct loop — see the module docstring for
    why this is Phase-8 scaffolding rather than a wired-in mixin.

    Discipline rules are enforced at method entry / exit — violating one
    raises :class:`DebugDisciplineError` rather than silently continuing.
    """

    context: DebugContext
    em_elevation_granted: bool = False

    # Injected collaborators — all tests replace these with deterministic stubs.
    repro_fn: Optional[Callable[..., Mapping[str, Any]]] = None
    bisect_fn: Optional[Callable[..., Mapping[str, Any]]] = None
    probe_fn: Optional[Callable[[Hypothesis, DebugContext], Tuple[bool, str, int]]] = (
        None
    )
    memory_conn_factory: Optional[Callable[[], sqlite3.Connection]] = None
    feedback_db_path: Optional[Path] = None
    memory_db_path: Optional[Path] = None

    # -----------------------------------------------------------------
    # State: reproduce
    # -----------------------------------------------------------------

    def reproduce(self, *, attempts: int = 3) -> DebugState:
        """Enter the reproduce state — confirm we can trigger the failure.

        §5.9: "Failure here (can't repro in ≥ 3 of 3 attempts) pauses
        debug and asks the EM." We call the injected repro function; on
        failure we raise :class:`DebugDisciplineError` with the exact
        question to surface to the EM.
        """
        if self.repro_fn is None:
            raise DebugDisciplineError(
                "reproduce: no repro_fn wired. Inject a callable that returns "
                "{reproduced, actual_output, match_score, ...}."
            )
        result = self.repro_fn(
            command=self.context.repro_command,
            expected_failure_signature=self.context.error_signature,
            attempts=attempts,
        )
        self.context.reproduced = bool(result.get("reproduced", False))
        self.context.repro_attempts = int(result.get("attempts", attempts))
        self.context.repro_attempts_matched = int(result.get("attempts_reproduced", 0))
        self.context.repro_actual_output = str(result.get("actual_output", ""))
        self.context.record_note(
            f"reproduce: reproduced={self.context.reproduced} "
            f"matched={self.context.repro_attempts_matched}/{self.context.repro_attempts}"
        )
        if not self.context.reproduced:
            raise DebugDisciplineError(
                "reproduce: failed in "
                f"{self.context.repro_attempts_matched}/{self.context.repro_attempts}"
                " attempts. Pause debug and ask the EM: "
                '"I cannot reproduce; is this environment-specific?"'
            )
        return DebugState.BISECT

    # -----------------------------------------------------------------
    # State: bisect
    # -----------------------------------------------------------------

    def bisect(
        self,
        good_ref: str,
        bad_ref: str,
        *,
        skip: bool = False,
    ) -> DebugState:
        """Run automated bisection between ``good_ref`` and ``bad_ref``.

        Passes ``skip=True`` when the bug was not recently introduced
        (e.g. a pre-existing bug surfaced by a new test) — bisection is
        meaningless there and only the ``hypothesise`` state is useful.
        """
        if skip:
            self.context.bisect_skipped = True
            self.context.record_note("bisect: skipped (caller opted out)")
            return DebugState.HYPOTHESISE
        if self.bisect_fn is None:
            raise DebugDisciplineError(
                "bisect: no bisect_fn wired. Inject `tools.debug.git_bisect` "
                "or a test stub."
            )
        result = self.bisect_fn(
            good_ref=good_ref,
            bad_ref=bad_ref,
            repro_command=self.context.repro_command,
        )
        self.context.culprit_sha = result.get("culprit_sha")
        self.context.bisect_log = str(result.get("log", ""))
        self.context.record_note(f"bisect: culprit={self.context.culprit_sha}")
        return DebugState.HYPOTHESISE

    # -----------------------------------------------------------------
    # State: hypothesise
    # -----------------------------------------------------------------

    def hypothesise(self, hypotheses: Sequence[Hypothesis]) -> DebugState:
        """Record ≥ 3 distinct root-cause hypotheses.

        Enforces Discipline rule 3 at the state-machine layer. §5.9:
        *"enforces len(hypotheses) >= 3. Single-hypothesis debugging is
        caught here."*

        Re-entering ``hypothesise`` after a ``probe`` round replaces the
        hypothesis set rather than appending — the caller is expected to
        carry forward high-confidence survivors and add new candidates.
        """
        cleaned = [h for h in hypotheses if h.text.strip()]
        if len(cleaned) < MIN_HYPOTHESES:
            raise DebugDisciplineError(
                f"hypothesise: require >= {MIN_HYPOTHESES} distinct hypotheses "
                f"(got {len(cleaned)}). "
                "Generate more root causes before probing — single-hypothesis "
                "debugging is blocked at the loop layer."
            )
        self.context.hypotheses = list(cleaned)
        self.context.record_note(f"hypothesise: recorded {len(cleaned)} hypotheses")
        return DebugState.PROBE

    # -----------------------------------------------------------------
    # State: probe
    # -----------------------------------------------------------------

    def probe(self) -> DebugState:
        """Run a distinguishing experiment for each hypothesis.

        Each probe returns ``(confirmed, evidence_line, confidence_delta)``.
        The sub-loop updates each hypothesis's confidence and records the
        evidence for audit. After probing, it returns LOCALISE_BUG when
        the top hypothesis already clears the propose_fix bar, otherwise
        HYPOTHESISE so the caller can refine. Round-count is bounded by
        :data:`MAX_DEBUG_ROUNDS`.
        """
        if self.probe_fn is None:
            raise DebugDisciplineError(
                "probe: no probe_fn wired. Inject a callable that accepts a "
                "Hypothesis + DebugContext and returns "
                "(confirmed, evidence, confidence_delta)."
            )
        self.context.round_count += 1
        if self.context.round_count > MAX_DEBUG_ROUNDS:
            raise DebugDisciplineError(
                f"probe: exceeded MAX_DEBUG_ROUNDS ({MAX_DEBUG_ROUNDS}) without "
                "localising. Surface to the EM as stuck (§5.9)."
            )

        for hyp in self.context.hypotheses:
            confirmed, evidence_line, delta = self.probe_fn(hyp, self.context)
            hyp.evidence.append(evidence_line)
            new_confidence = max(0, min(100, hyp.confidence + int(delta)))
            hyp.confidence = (
                new_confidence if confirmed else max(0, new_confidence - 10)
            )
            self.context.probes_run += 1

        top = self._top_hypothesis()
        self.context.record_note(
            f"probe: round {self.context.round_count} top={top.text!r} "
            f"confidence={top.confidence}"
        )
        if top.confidence >= PROPOSE_FIX_CONFIDENCE_THRESHOLD:
            return DebugState.LOCALISE_BUG
        return DebugState.HYPOTHESISE

    # -----------------------------------------------------------------
    # State: localise_bug
    # -----------------------------------------------------------------

    def localise_bug(
        self,
        file: str,
        line: int,
    ) -> DebugState:
        """Pin the top-confidence hypothesis to a concrete ``file:line``.

        Deterministic — the caller has already done the searching. The
        sub-loop just records where the bug lives and advances.
        """
        if (
            not self._top_hypothesis()
            or self._top_hypothesis().confidence < PROPOSE_FIX_CONFIDENCE_THRESHOLD
        ):
            # This should have been caught by `probe` — raise to keep the
            # invariant visible in tests.
            raise DebugDisciplineError(
                "localise_bug: top hypothesis confidence is below the "
                f"{PROPOSE_FIX_CONFIDENCE_THRESHOLD}% bar; do not localise yet."
            )
        self.context.localised_file = file
        self.context.localised_line = line
        self.context.record_note(f"localise_bug: {file}:{line}")
        return DebugState.PROPOSE_FIX

    # -----------------------------------------------------------------
    # State: propose_fix
    # -----------------------------------------------------------------

    def propose_fix(
        self,
        *,
        summary: str,
        is_architectural: bool = False,
        shotgun: bool = False,
    ) -> DebugState:
        """Propose a fix candidate — enforces the two hard discipline rules.

        Rule 1: no fix before repro (``context.reproduced`` must be True).
        Rule 2: no fix before root cause — the top hypothesis must clear
        :data:`PROPOSE_FIX_CONFIDENCE_THRESHOLD`, unless ``shotgun=True``
        *and* ``self.em_elevation_granted=True`` (the override pair from
        §5.9).
        """
        if not self.context.reproduced:
            raise DebugDisciplineError(
                "propose_fix: refused — reproduce did not return reproduced=True. "
                '"I think this is the bug but I can\'t repro it" is blocked (§5.9 rule 1).'
            )
        top = self._top_hypothesis()
        if top is None:
            raise DebugDisciplineError(
                "propose_fix: no hypotheses recorded; run hypothesise first."
            )
        if top.confidence < PROPOSE_FIX_CONFIDENCE_THRESHOLD:
            if not (shotgun and self.em_elevation_granted):
                raise DebugDisciplineError(
                    f"propose_fix: top hypothesis confidence {top.confidence}% "
                    f"< {PROPOSE_FIX_CONFIDENCE_THRESHOLD}%. "
                    "Pass shotgun=True AND request EM elevation "
                    "(em_elevation_granted) to override (§5.9 rule 2)."
                )
            self.context.shotgun = True
            self.context.record_note(
                "propose_fix: shotgun override ACTIVE (EM elevation granted)"
            )

        self.context.fix_summary = summary.strip()
        self.context.fix_is_architectural = is_architectural
        self.context.record_note(
            f"propose_fix: summary={summary!r} architectural={is_architectural}"
        )
        return DebugState.POSTMORTEM

    # -----------------------------------------------------------------
    # State: postmortem
    # -----------------------------------------------------------------

    def postmortem(
        self,
        *,
        feedback_conn: Optional[sqlite3.Connection] = None,
        memory_conn: Optional[sqlite3.Connection] = None,
        fix_pr_url: Optional[str] = None,
        loop_version: int = 1,
    ) -> DebugState:
        """Write postmortem notes to ``feedback.db`` + ``failure_patterns`` memory.

        §5.9 last state. Mandatory — the sub-loop does NOT return to the
        parent until the postmortem is recorded. Discipline rule 5
        ("Postmortem in the feedback record") is enforced by
        :meth:`require_postmortem_or_raise`.
        """
        if self.context.fix_summary is None:
            raise DebugDisciplineError(
                "postmortem: cannot write before propose_fix has run."
            )

        # Feedback-record update (§7.4 step 10 "verify on merge" half —
        # we append a postmortem line to notes_json, not transition state).
        if feedback_conn is not None and self.context.feedback_id:
            row = feedback_store.get_row(feedback_conn, self.context.feedback_id)
            if row is None:
                raise DebugDisciplineError(
                    f"postmortem: feedback row {self.context.feedback_id!r} not found. "
                    "Create the feedback record before entering debug, or "
                    "point the sub-loop at a different feedback_id."
                )
            try:
                notes = json.loads(row.notes_json) if row.notes_json else []
            except json.JSONDecodeError:
                notes = []
            notes.append(
                {
                    "ts": _utc_now_iso(),
                    "stage": "postmortem",
                    "summary": self.context.fix_summary,
                    "top_hypothesis": (
                        self._top_hypothesis().text if self._top_hypothesis() else ""
                    ),
                    "fix_pr_url": fix_pr_url,
                }
            )
            feedback_store.update_row(
                feedback_conn,
                self.context.feedback_id,
                {"notes_json": json.dumps(notes)},
            )

        # Memory write — failure_patterns topic per §6.8.1.
        if memory_conn is not None:
            payload = {
                "error_signature": self.context.error_signature,
                "stack_hash": _stack_hash(self.context.error_signature),
                "root_cause": (
                    self._top_hypothesis().text if self._top_hypothesis() else ""
                ),
                "fix_pr_url": fix_pr_url,
                "affected_files": (
                    [self.context.localised_file] if self.context.localised_file else []
                ),
                "fix_class": (
                    "architectural" if self.context.fix_is_architectural else "tool"
                ),
                "summary": self.context.fix_summary,
                "shotgun": self.context.shotgun,
                "loop_version": loop_version,
            }
            row = memory_store.MemoryRow(
                id=str(uuid.uuid4()),
                topic="failure_patterns",
                created_at=_utc_now_iso(),
                source_kind="feedback",
                source_id=self.context.feedback_id,
                payload_json=json.dumps(payload, sort_keys=True),
                embedding_key=_stack_hash(self.context.error_signature),
                confidence=max(
                    self._top_hypothesis().confidence if self._top_hypothesis() else 80,
                    80,
                ),
            )
            memory_store.insert_row(memory_conn, row)

        self.context.postmortem_written = True
        self.context.record_note("postmortem: feedback + failure_patterns written")
        return DebugState.POSTMORTEM  # terminal — caller exits the sub-loop

    # -----------------------------------------------------------------
    # Invariants callers can assert externally
    # -----------------------------------------------------------------

    def require_postmortem_or_raise(self) -> None:
        """Discipline rule 5 — refuse to close the sub-loop without postmortem.

        Called from the parent ReAct loop's ``verify_merge`` state once this
        Phase-8 module is wired in (Phase 11). For now it's a free-standing
        check callers can invoke.
        """
        if not self.context.postmortem_written:
            raise DebugDisciplineError(
                "verify_merge: refuse to close bug-fix feedback without a "
                "postmortem (§5.9 rule 5)."
            )

    def _top_hypothesis(self) -> Optional[Hypothesis]:
        """Return the hypothesis with the highest confidence, or None."""
        if not self.context.hypotheses:
            return None
        return max(self.context.hypotheses, key=lambda h: h.confidence)


__all__ = [
    "DebugContext",
    "DebugDisciplineError",
    "DebugState",
    "DebugSubLoop",
    "Hypothesis",
    "MAX_DEBUG_ROUNDS",
    "MIN_HYPOTHESES",
    "PROPOSE_FIX_CONFIDENCE_THRESHOLD",
]
