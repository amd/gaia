# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Goal mode policy: decide, round by round, whether a goal run keeps going.

No I/O here. A host (``gaia -p``, the TUI's stdio host, the Agent UI) runs each
round as a normal turn, records it in its own history, asks
:mod:`gaia.agents.base.goal_check` for a verdict, and calls :func:`next_action`.
See docs/plans/goal-mode.mdx.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Tuple

GoalOutcome = Literal["met", "needs_user", "stalled", "budget", "cleared", "error"]

DEFAULT_MAX_ROUNDS = 20
DEFAULT_MAX_SECONDS = 2 * 60 * 60
#: Rounds in a row with nothing new done before the run counts as stalled.
STALL_ROUNDS = 3
#: Most "still missing" items a round prompt carries.
_PROMPT_ITEMS = 8
#: Characters of the user's request repeated in every later round's prompt.
_REQUEST_CHARS = 1500
_DURATION = re.compile(r"\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds?|min|m)\b")


@dataclass(frozen=True)
class GoalSpec:
    """What the user asked to hold, and the bounds on getting there."""

    condition: str
    checks: Tuple[str, ...] = ()
    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_seconds: float = DEFAULT_MAX_SECONDS

    def __post_init__(self):
        if not self.condition or not self.condition.strip():
            raise ValueError("A goal needs a condition: say what must be true.")
        if self.max_rounds < 1:
            raise ValueError(f"max_rounds must be at least 1, got {self.max_rounds}.")
        if self.max_seconds <= 0:
            raise ValueError(f"max_seconds must be positive, got {self.max_seconds}.")


@dataclass
class GoalVerdict:
    """One check of the goal after a round."""

    met: bool
    missing: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    blocked: bool = False
    question: str = ""
    #: Which tier decided: "check", "evidence" or "evaluator".
    decided_by: str = "evaluator"
    #: Check commands that passed before a later tier decided.
    checks_passed: int = 0

    def to_dict(self) -> dict:
        return {
            "met": self.met,
            "missing": list(self.missing),
            "evidence": list(self.evidence),
            "blocked": self.blocked,
            "question": self.question,
            "decided_by": self.decided_by,
            "checks_passed": self.checks_passed,
        }


@dataclass
class RoundRecord:
    verdict: GoalVerdict
    #: Files written so far, across all rounds, after this one.
    written: Tuple[str, ...]
    progressed: bool


@dataclass
class GoalLedger:
    """What's been done and what's left, kept outside the conversation."""

    started_at: float = field(default_factory=time.monotonic)
    rounds: List[RoundRecord] = field(default_factory=list)
    written: List[str] = field(default_factory=list)

    @property
    def round_count(self) -> int:
        return len(self.rounds)

    @property
    def last(self) -> Optional[GoalVerdict]:
        return self.rounds[-1].verdict if self.rounds else None

    def record(self, verdict: GoalVerdict, written_files: List[str]) -> None:
        """Add a round, noting whether it moved the goal forward.

        Progress is a new file, new proof, fewer items missing, or a different
        check failure. The evaluator rewords its missing list every round and
        test runners print new timings, so changed wording alone isn't progress.
        """
        new_files = [path for path in written_files if path not in self.written]
        self.written.extend(new_files)
        proven = self.cited()
        previous = self.last
        progressed = (
            previous is None
            or bool(new_files)
            or any(quote not in proven for quote in verdict.evidence)
            or len(verdict.missing) < len(previous.missing)
            or (
                verdict.decided_by != "evaluator"
                and _normalized(verdict.missing) != _normalized(previous.missing)
            )
        )
        self.rounds.append(RoundRecord(verdict, tuple(self.written), progressed))

    def stalled(self) -> bool:
        tail = self.rounds[-STALL_ROUNDS:]
        return len(tail) == STALL_ROUNDS and not any(r.progressed for r in tail)

    def cited(self) -> List[str]:
        """Evidence quotes earlier rounds proved against real tool output."""
        quotes: List[str] = []
        for record in self.rounds:
            quotes.extend(q for q in record.verdict.evidence if q not in quotes)
        return quotes


def next_action(
    spec: GoalSpec,
    ledger: GoalLedger,
    *,
    now: Optional[float] = None,
    cleared: bool = False,
) -> Optional[GoalOutcome]:
    """How the run ends after the latest round, or None to run another."""
    verdict = ledger.last
    if verdict is None:
        return None
    if verdict.met:
        return "met"
    if cleared:
        return "cleared"
    if verdict.blocked:
        return "needs_user"
    if ledger.stalled():
        return "stalled"
    elapsed = (now if now is not None else time.monotonic()) - ledger.started_at
    if ledger.round_count >= spec.max_rounds or elapsed >= spec.max_seconds:
        return "budget"
    return None


def round_prompt(spec: GoalSpec, ledger: GoalLedger, first_prompt: str) -> str:
    """The text the next round runs. Round 1 is the user's own request."""
    verdict = ledger.last
    if verdict is None:
        return first_prompt
    request = first_prompt.strip()
    if len(request) > _REQUEST_CHARS:
        request = request[:_REQUEST_CHARS] + "…"
    lines = [
        f"Goal not met yet (round {ledger.round_count + 1} of at most "
        f"{spec.max_rounds}). The goal: {spec.condition.strip()}",
        f"The original request: {request}",
    ]
    if verdict.missing:
        lines.append("Still missing:")
        lines.extend(f"- {item}" for item in verdict.missing[:_PROMPT_ITEMS])
    if ledger.written:
        lines.append(f"Files written so far: {', '.join(ledger.written[-10:])}")
    lines.append(
        "Continue the work. Check results with tools before saying anything is "
        "done; the goal is checked again from what the tools show, not from "
        "your answer."
    )
    return "\n".join(lines)


def outcome_message(outcome: GoalOutcome, ledger: GoalLedger) -> str:
    """One line saying how the goal run ended. Never reads as success unless met."""
    verdict = ledger.last
    rounds = ledger.round_count
    left = "; ".join(verdict.missing[:_PROMPT_ITEMS]) if verdict else ""
    if outcome == "met":
        if verdict and verdict.checks_passed:
            how = "its checks passed; the rest judged from tool results"
        else:
            how = "judged from tool results, not proven by a check"
        return f"Goal met after {rounds} round(s) ({how})."
    if outcome == "error":
        return "Goal not done: it could not be checked."
    if outcome == "needs_user":
        question = verdict.question if verdict and verdict.question else left
        return f"Goal not done: it needs you. {question}".strip()
    if outcome == "stalled":
        return (
            f"Goal not done: no progress in the last {STALL_ROUNDS} rounds. "
            f"Still missing: {left or 'see the last answer'}"
        )
    if outcome == "budget":
        return (
            f"Goal not done: stopped after {rounds} round(s), its budget. "
            f"Still missing: {left or 'see the last answer'}"
        )
    return "Goal cleared before it was met."


def _normalized(items: List[str]) -> List[str]:
    """Items compared without case, spacing or durations ("in 1.52s")."""
    return sorted(" ".join(_DURATION.sub("", item.lower()).split()) for item in items)
