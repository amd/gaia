# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Infer what a transcript does not state: why a human spoke, and how it went.

A transcript records what happened, never whether it was wanted. Everything in
this module is therefore **inference with stated evidence**, never ground truth,
and every annotation ships its own ``basis``, ``confidence`` and ``evidence``
list so a consumer can discount it.

Two things are genuinely recoverable and worth mining:

* **Why a human spoke again.** A follow-up turn that says "no, revert that" is a
  correction; one that says "perfect, ship it" is approval. Classifying those
  turns is the closest the corpus comes to a verdict on the preceding work.
* **How an episode ended.** Verification commands and their exit status, whether
  the run was interrupted, whether the last actions succeeded, and what the human
  said next.

**The hard limit, measured:** 67% of sessions contain exactly one human turn, so
there is no follow-up to read. For those the outcome annotation is driven only by
indirect evidence and is labelled ``unknown`` far more often than not. That is
reported rather than papered over — an outcome label that guesses on two-thirds
of the corpus would be worse than none.
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Human-turn classification
# ---------------------------------------------------------------------------

#: Ordered specific-before-generic, first match wins. A turn that says
#: "no, use pytest instead" is a correction, not a new instruction, even though
#: it also names a tool.
_TURN_PATTERNS: List[tuple] = [
    (
        "interruption",
        re.compile(r"\[request interrupted", re.I),
        "harness recorded an interrupt",
    ),
    (
        "correction",
        re.compile(
            r"\b(?:no[,.\s]|nope\b|that'?s wrong|incorrect|not what i|"
            r"you (?:missed|forgot|broke|misunderstood)|revert|undo|"
            r"stop\b|don'?t\b|shouldn'?t|instead of|actually,|"
            r"still (?:failing|broken|not working|wrong)|didn'?t work|"
            r"try again|that'?s not)",
            re.I,
        ),
        "corrective language",
    ),
    (
        "approval",
        re.compile(
            r"^\W{0,3}(?:thanks|thank you|perfect|great|nice|lgtm|looks good|"
            r"ship it|yes\b|yep\b|correct\b|exactly|good\b|ok(?:ay)?\b|"
            r"sounds good|go ahead|proceed|do it|approved)\W{0,3}$",
            re.I,
        ),
        "short affirmative with no new instruction",
    ),
    (
        "approval_with_followup",
        re.compile(
            r"^\W{0,3}(?:thanks|perfect|great|lgtm|looks good|yes|ok(?:ay)?|"
            r"good|nice)\b[,.! ]",
            re.I,
        ),
        "affirmative opening followed by more instruction",
    ),
    (
        "clarification_answer",
        re.compile(
            r"^\W{0,3}(?:option\s*\d|[a-d]\)|the (?:first|second|latter|former)\b)",
            re.I,
        ),
        "answers a posed question",
    ),
]

#: A turn shorter than this with no verb is more likely an answer than a task.
_SHORT_TURN_CHARS = 40

TURN_CLASSES = (
    "initial_instruction",
    "correction",
    "approval",
    "approval_with_followup",
    "clarification_answer",
    "interruption",
    "follow_up_task",
)


@dataclass
class TurnAnnotation:
    """Why a human spoke, with the evidence for saying so."""

    index: int
    turn_class: str
    confidence: str  # high | medium | low
    evidence: List[str] = field(default_factory=list)
    chars: int = 0
    basis: str = "inferred_from_lexical_signals"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify_turn(text: str, index: int) -> TurnAnnotation:
    """Classify one human turn.

    The first turn is the instruction by definition. Later turns are read for
    corrective or affirmative language; anything else is a follow-up task.
    """
    stripped = (text or "").strip()
    if index == 0:
        return TurnAnnotation(
            index=index,
            turn_class="initial_instruction",
            confidence="high",
            evidence=["first human turn of the transcript"],
            chars=len(stripped),
            basis="structural",
        )
    for name, pattern, reason in _TURN_PATTERNS:
        if pattern.search(stripped):
            # A long turn that merely opens with "thanks" still carries work.
            confidence = "high" if len(stripped) < 400 else "medium"
            return TurnAnnotation(
                index=index,
                turn_class=name,
                confidence=confidence,
                evidence=[reason],
                chars=len(stripped),
            )
    if len(stripped) < _SHORT_TURN_CHARS:
        return TurnAnnotation(
            index=index,
            turn_class="clarification_answer",
            confidence="low",
            evidence=[f"very short turn ({len(stripped)} chars), no corrective signal"],
            chars=len(stripped),
        )
    return TurnAnnotation(
        index=index,
        turn_class="follow_up_task",
        confidence="medium",
        evidence=["no corrective or affirmative signal; reads as new work"],
        chars=len(stripped),
    )


def annotate_turns(prompts: Sequence[str]) -> List[TurnAnnotation]:
    return [classify_turn(text, i) for i, text in enumerate(prompts)]


# ---------------------------------------------------------------------------
# Episode outcome inference
# ---------------------------------------------------------------------------

_VERIFY_PASS = re.compile(
    r"\b(\d+)\s+passed\b|\ball checks pass|\bOK\b|\bSUCCESS\b|build succeeded", re.I
)
_VERIFY_FAIL = re.compile(
    r"\b(\d+)\s+failed\b|\bFAILED\b|\berror:|\btraceback\b|build failed", re.I
)
_VERIFY_CMD = re.compile(
    r"\bpytest\b|\bnpm (?:run )?test\b|\bgo test\b|\bruff\b|\bflake8\b|"
    r"\bblack\b|\bisort\b|\bmake\b|\btsc\b|gh pr checks",
    re.I,
)
_LANDING_CMD = re.compile(r"git (?:commit|push)\b|gh pr (?:create|merge)\b", re.I)

OUTCOME_LABELS = ("likely_succeeded", "likely_failed", "mixed", "unknown")


@dataclass
class OutcomeAnnotation:
    """An inferred verdict on an episode, and why.

    ``basis`` is deliberately verbose. Nothing in a transcript states whether the
    human's goal was met, and this label must never be read as if it did.
    """

    label: str
    confidence: str
    evidence: List[str] = field(default_factory=list)
    next_turn_class: Optional[str] = None
    verification_ran: bool = False
    verification_passed: Optional[bool] = None
    landed_change: bool = False
    interrupted: bool = False
    final_action_ok: Optional[bool] = None
    basis: str = "inferred_from_transcript_signals_NOT_ground_truth"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def infer_episode_outcome(
    decisions: Sequence[Any],
    next_turn: Optional[TurnAnnotation],
    interrupted: bool = False,
) -> OutcomeAnnotation:
    """Infer how one episode went from the evidence the transcript does hold.

    Weighting, strongest first:

    1. **What the human said next.** A correction is the clearest negative signal
       in the corpus; an approval the clearest positive.
    2. **Verification.** A test or lint run and its result — the only
       machine-checkable definition of done available.
    3. **Landing.** A commit, push or merged PR implies the work was accepted.
    4. **The final action's own outcome**, which is weak: a run can end on a
       successful ``ls`` and still have failed the task.
    """
    evidence: List[str] = []
    verification_ran = False
    verification_passed: Optional[bool] = None
    landed = False
    final_ok: Optional[bool] = None

    for point in decisions:
        for call, obs in zip(point.calls, point.observations):
            command = (
                str(call.arguments.get("command", "")) if call.tool == "Bash" else ""
            )
            if command and _VERIFY_CMD.search(command):
                verification_ran = True
                text = obs.text or ""
                if obs.ok is False or _VERIFY_FAIL.search(text):
                    verification_passed = False
                elif verification_passed is not False and _VERIFY_PASS.search(text):
                    verification_passed = True
            if command and _LANDING_CMD.search(command) and obs.ok:
                landed = True
    if decisions:
        final_ok = (
            decisions[-1].observations[-1].ok if decisions[-1].observations else None
        )

    next_class = next_turn.turn_class if next_turn else None

    # 1. The human's own reaction.
    if next_class == "correction":
        evidence.append("the next human turn corrects the agent")
        label, confidence = "likely_failed", "medium"
    elif next_class in ("approval", "approval_with_followup"):
        evidence.append("the next human turn approves")
        label, confidence = "likely_succeeded", "medium"
    elif interrupted:
        evidence.append("the run was interrupted by the user")
        label, confidence = "likely_failed", "low"
    else:
        label, confidence = "unknown", "low"
        if next_class is None:
            evidence.append(
                "no following human turn — 67% of sessions never get one, so "
                "there is no reaction to read"
            )
        else:
            evidence.append(f"next human turn reads as {next_class}, which is neutral")

    # 2. Verification, which can confirm or contradict.
    if verification_ran:
        evidence.append(
            f"verification ran and {'passed' if verification_passed else 'failed'}"
            if verification_passed is not None
            else "verification ran, result unreadable"
        )
        if verification_passed is True and label == "unknown":
            label, confidence = "likely_succeeded", "low"
        elif verification_passed is False:
            if label == "likely_succeeded":
                label, confidence = "mixed", "low"
                evidence.append("human approved but verification failed — conflicting")
            else:
                label, confidence = "likely_failed", "medium"

    # 3. Landing the change.
    if landed:
        evidence.append("the episode committed, pushed or merged")
        if label == "unknown":
            label, confidence = "likely_succeeded", "low"
        elif label == "likely_succeeded":
            confidence = "high" if confidence == "medium" else confidence

    if final_ok is False:
        evidence.append("the episode's final tool call errored")

    return OutcomeAnnotation(
        label=label,
        confidence=confidence,
        evidence=evidence,
        next_turn_class=next_class,
        verification_ran=verification_ran,
        verification_passed=verification_passed,
        landed_change=landed,
        interrupted=interrupted,
        final_action_ok=final_ok,
    )


# ---------------------------------------------------------------------------
# Inferred reasoning
# ---------------------------------------------------------------------------

_INTENT_BY_FAMILY = {
    "read": "inspect a file's contents",
    "search": "locate code or files matching a pattern",
    "edit": "modify a file in place",
    "write": "create or overwrite a file",
    "shell": "run a command",
    "web": "fetch information from outside the repository",
    "delegate": "hand a scoped sub-task to a subagent",
    "plan": "record or revise a plan",
    "meta": "invoke a skill or command",
    "mcp": "call an external typed service",
}


def infer_reasoning(
    point: Any,
    previous: Optional[Any],
    goal: str,
    depth: int,
    stated_preamble: str = "",
) -> Dict[str, Any]:
    """Reconstruct a rationale for an action the model never explained.

    Extended thinking is encrypted corpus-wide, so the model's actual reasoning
    is gone. This synthesises a rationale from what *is* observable: the goal, the
    step immediately before, what the action does, and where in the episode it
    sits.

    It is explicitly **not** the model's reasoning and is marked as such. It is
    useful as a grounded description of the decision context — enough to give a
    candidate harness the same framing — and useless as evidence about how the
    reference model actually thought.
    """
    families = [c.family for c in point.calls]
    tools = [c.tool for c in point.calls]
    primary = _INTENT_BY_FAMILY.get(families[0], "act") if families else "act"

    situation: List[str] = []
    if depth == 0:
        situation.append("opening move for this instruction")
    elif depth >= 8:
        situation.append(f"step {depth} of a sustained chain")
    else:
        situation.append(f"step {depth} of this instruction")

    if previous is not None and previous.reference_quality == "errored":
        classes = [o.error_class for o in previous.observations if o.error_class]
        situation.append(
            f"the previous step failed ({', '.join(classes) or 'unclassified'}), "
            "so this is a recovery attempt"
        )
    elif previous is not None:
        prior_tools = ", ".join(sorted({c.tool for c in previous.calls}))
        situation.append(f"follows a successful {prior_tools}")

    if len(point.calls) > 1:
        situation.append(f"dispatches {len(point.calls)} tools together")

    # The caller supplies the preamble already scrubbed. Reading
    # point.reasoning_text here instead published two raw usernames: the record's
    # own `reasoning.visible_text` was scrubbed, but this copy was not.
    stated = (stated_preamble or "").strip()[:300]
    if stated:
        confidence = "medium"
        source = "grounded_in_visible_preamble"
    else:
        confidence = "low"
        source = "grounded_in_state_and_action_only"

    summary = (
        f"To advance \"{goal[:110].strip()}\", {primary} via {'/'.join(sorted(set(tools)))}; "
        + "; ".join(situation)
        + "."
    )
    return {
        "inferred_summary": summary,
        "stated_preamble": stated,
        "situation": situation,
        "confidence": confidence,
        "source": source,
        "basis": (
            "reconstructed_from_observable_state — the model's own reasoning is "
            "encrypted corpus-wide and is NOT recoverable; this is a description "
            "of the decision context, not the model's thought process"
        ),
    }
