# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tag a decision point with the capabilities it probes.

A harness designer needs to ask "how does mine do on error recovery
specifically", not "what is my average".  Each axis below has a mechanical
detector and traces to a measured finding in the corpus analysis, so a record's
tags are reproducible and arguable rather than a matter of taste.

A record carries every axis that fires — they are not mutually exclusive, and the
interesting records fire several.
"""

import re
from typing import Any, Dict, List, Optional, Sequence

#: Corpus median tool calls per human turn.  Past it, the agent is sustaining a
#: plan rather than answering directly.
PLANNING_DEPTH = 8

#: Corpus max depth is 273 and p90 is 64; 32 is comfortably into the tail.
HARD_DEPTH = 32

#: p90 read result is ~32K chars, so a prior observation past 20K is a context
#: pressure the harness has to handle.
LARGE_OBSERVATION = 20_000
HUGE_OBSERVATION = 50_000

_TRUNCATORS = re.compile(
    r"\|\s*(?:head|tail)\b|\bhead\s+-\d|\btail\s+-\d|--max-count|\|\s*wc\b"
)
_REGEX_META = re.compile(r"[\\\[\]().*+?{}|^$]")
_VERIFIERS = re.compile(
    r"\bpytest\b|\bunittest\b|\bnpm (?:run )?test\b|\bgo test\b"
    r"|\bblack\b|\bisort\b|\bflake8\b|\bruff\b|\beslint\b|\blint\b"
    r"|\bmake\b|\bnpm run build\b|\btsc\b|\bcargo build\b"
    r"|git (?:diff|status)\b|gh pr checks\b"
)
_SCAFFOLD_LEADERS = frozenset({"cd", "export", "pwd", "source", "set"})

#: Every axis, in report order.  Keeping the list explicit means a new axis has to
#: be added deliberately rather than appearing because some detector happened to
#: return a new string.
ALL_AXES: List[str] = [
    "tool_selection",
    "argument_construction",
    "error_recovery",
    "context_management",
    "state_reconstruction",
    "parallelism",
    "delegation",
    "verification",
    "multi_step_planning",
    "stopping",
]


def _substantive_segments(call: Dict[str, Any]) -> int:
    return sum(
        1 for s in call.get("shell_segments", []) if s.get("kind") == "substantive"
    )


def axes_for(
    record: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
    is_episode_final: bool,
) -> List[str]:
    """Which capabilities this decision point probes.

    ``previous`` is the preceding decision point in the same transcript, needed
    because error recovery is a property of the *transition*, not of the record.
    """
    axes = {"tool_selection"}
    calls: Sequence[Dict[str, Any]] = record["action"]["calls"]
    observations: Sequence[Dict[str, Any]] = record["observation"]

    if record["action"]["width"] > 1:
        axes.add("parallelism")

    if previous is not None and previous["outcome"]["reference_quality"] == "errored":
        axes.add("error_recovery")

    if record["depth_index"] >= PLANNING_DEPTH:
        axes.add("multi_step_planning")

    if is_episode_final:
        axes.add("stopping")

    prior_big = previous is not None and any(
        (o.get("chars") or 0) > LARGE_OBSERVATION for o in previous["observation"]
    )
    if prior_big:
        axes.add("context_management")

    for call in calls:
        tool = call["tool"]
        args = call.get("arguments", {})

        if call["family"] == "delegate":
            axes.add("delegation")

        if tool == "Bash":
            command = str(args.get("command", ""))
            if _substantive_segments(call) >= 2:
                axes.add("argument_construction")
            if _TRUNCATORS.search(command):
                axes.add("context_management")
            if _VERIFIERS.search(command):
                axes.add("verification")
            segments = call.get("shell_segments", [])
            if segments and segments[0].get("leader") in _SCAFFOLD_LEADERS:
                axes.add("state_reconstruction")

        elif tool in ("Grep", "Glob"):
            pattern = str(args.get("pattern", ""))
            if _REGEX_META.search(pattern):
                axes.add("argument_construction")

        elif tool in ("Edit", "MultiEdit"):
            if len(str(args.get("old_string", ""))) >= 200:
                axes.add("argument_construction")

        elif tool == "Read" and args.get("offset") is not None:
            axes.add("context_management")

    if any((o.get("chars") or 0) > LARGE_OBSERVATION for o in observations):
        axes.add("context_management")

    return [a for a in ALL_AXES if a in axes]


def difficulty_for(record: Dict[str, Any], prior_failures_in_episode: int) -> str:
    """Bucket a record's difficulty *for the reference harness*.

    Derived from position and outcome, which are the only difficulty signals the
    corpus actually holds.  This is not a measure of intrinsic task difficulty and
    the datasheet says so — a trivial action taken at depth 40 lands in ``hard``
    because everything at depth 40 is expensive, not because the action was.
    """
    depth = record["depth_index"]
    width = record["action"]["width"]
    biggest = (
        max((o.get("chars") or 0) for o in record["observation"])
        if record["observation"]
        else 0
    )

    if (
        depth >= HARD_DEPTH
        or record["outcome"]["reference_quality"] == "errored"
        or prior_failures_in_episode >= 2
        or biggest > HUGE_OBSERVATION
        or width >= 4
    ):
        return "hard"
    if (
        depth < 4
        and width == 1
        and record["outcome"]["reference_quality"] == "succeeded"
        and prior_failures_in_episode == 0
    ):
        return "easy"
    return "moderate"
