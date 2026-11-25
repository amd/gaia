# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A small eval kit for an agent you built on the SDK.

Runs a list of conversations against any ``Agent``, keeps each turn's reply,
tool calls, tokens and cost, hands them to a grader you write, and prints a
table of quality, cost and speed. No Agent UI, no judge model: the grader is a
function, so the same run gives the same table.

    cases = [Case("T001", ["Where is order 1001?"], expect={"tool": "get_order"})]

    def grade(case, turns):
        called = [c["name"] for t in turns for c in t.tool_calls]
        return Verdict(case.expect["tool"] in called, "tool not called")

    report = run_cases(lambda case: MyAgent(...), cases, grade)
    print(report.table())
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class Case:
    """One conversation to run: what the user says, turn by turn."""

    id: str
    turns: Sequence[str]
    #: Whatever the grader needs to judge this case. The kit never reads it.
    expect: Any = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Case needs an id.")
        if not self.turns or isinstance(self.turns, str):
            raise ValueError(
                f"Case {self.id!r} needs turns as a list of user messages, "
                f"got {self.turns!r}."
            )


@dataclass(frozen=True)
class TurnRecord:
    """What one turn produced, as the agent's result reported it."""

    user: str
    reply: str
    status: str
    stop_reason: Optional[str]
    tool_calls: List[Dict[str, Any]]
    reprompts: List[Dict[str, Any]]
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Optional[float]
    seconds: float
    #: The whole result dict, for a grader that needs more.
    result: Dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    """A grader's judgement of one case."""

    passed: bool
    #: Why it failed; shown in the table. Leave empty for a pass.
    reason: str = ""


Grader = Callable[[Case, List[TurnRecord]], Verdict]


@dataclass(frozen=True)
class CaseResult:
    case: Case
    turns: List[TurnRecord]
    verdict: Verdict

    @property
    def model_calls(self) -> int:
        return sum(turn.model_calls for turn in self.turns)

    @property
    def tool_calls(self) -> int:
        return sum(len(turn.tool_calls) for turn in self.turns)

    @property
    def cost_usd(self) -> Optional[float]:
        """Dollars for the case; None when any turn's model has no list price."""
        costs = [turn.cost_usd for turn in self.turns]
        if any(cost is None for cost in costs):
            return None
        return sum(costs)


def _turn_record(user: str, result: Dict[str, Any]) -> TurnRecord:
    for key in ("status", "result"):
        if key not in result:
            raise ValueError(
                f"The agent's result has no {key!r} key, so this is not a "
                "process_query() result. run_cases() needs the factory to "
                "return a gaia Agent."
            )
    timing = result.get("timing_summary") or {}
    return TurnRecord(
        user=user,
        reply=result["result"] or "",
        status=result["status"],
        stop_reason=result.get("stop_reason"),
        tool_calls=list(result.get("tool_calls") or []),
        reprompts=list(result.get("reprompts") or []),
        model_calls=int(timing.get("llm_calls") or 0),
        input_tokens=int(result.get("input_tokens") or 0),
        output_tokens=int(result.get("output_tokens") or 0),
        cost_usd=result.get("cost_usd"),
        seconds=float(result.get("duration") or 0.0),
        result=result,
    )


def run_cases(
    make_agent: Callable[[Case], Any],
    cases: Sequence[Case],
    grader: Grader,
) -> "Report":
    """Run every case on a fresh agent and grade it.

    Args:
        make_agent: Builds the agent for one case. Called once per case, so no
            case sees another's conversation.
        cases: The conversations to run.
        grader: Judges a finished case from its turns.

    An exception from the agent or the grader is not caught: a run that
    cannot finish has no table worth reading.
    """
    ids = [case.id for case in cases]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ValueError(f"Case ids must be unique; repeated: {', '.join(repeated)}.")
    results = []
    for case in cases:
        agent = make_agent(case)
        turns = [_turn_record(user, agent.process_query(user)) for user in case.turns]
        verdict = grader(case, turns)
        if not isinstance(verdict, Verdict):
            raise TypeError(
                f"The grader returned {type(verdict).__name__} for case "
                f"{case.id!r}; it must return a Verdict."
            )
        results.append(CaseResult(case, turns, verdict))
    return Report(results)


def _percentile(values: List[float], share: float) -> float:
    """Nearest-rank percentile: a value that was actually measured."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * share // 1))
    return ordered[int(rank) - 1]


def _dollars(value: Optional[float]) -> str:
    return "n/a" if value is None else f"${value:.4f}"


@dataclass(frozen=True)
class Report:
    """The graded results of one run."""

    results: List[CaseResult]

    def summary(self) -> Dict[str, Any]:
        """Quality, cost and speed for the run, as numbers."""
        turns = [turn for result in self.results for turn in result.turns]
        passed = [result for result in self.results if result.verdict.passed]
        costs = [result.cost_usd for result in self.results]
        priced = all(cost is not None for cost in costs) and bool(costs)
        total = sum(costs) if priced else None
        seconds = [turn.seconds for turn in turns]
        return {
            "cases": len(self.results),
            "passed": len(passed),
            "pass_rate": len(passed) / len(self.results) if self.results else 0.0,
            "turns": len(turns),
            "model_calls_per_turn": (
                sum(turn.model_calls for turn in turns) / len(turns) if turns else 0.0
            ),
            "not_success_turns": sum(turn.status != "success" for turn in turns),
            "reprompts": sum(len(turn.reprompts) for turn in turns),
            "cost_usd": total,
            "cost_per_pass_usd": (
                total / len(passed) if total is not None and passed else None
            ),
            "turn_seconds_p50": statistics.median(seconds) if seconds else 0.0,
            "turn_seconds_p95": _percentile(seconds, 0.95) if seconds else 0.0,
        }

    def table(self, timing: bool = True) -> str:
        """A Markdown table, one row per case, then the totals.

        *timing*: include wall-clock figures. Leave them out to compare two
        runs byte for byte.
        """
        lines = [
            "| Case | Result | Turns | Model calls | Tool calls | Tokens in/out "
            "| Cost | Why |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for result in self.results:
            tokens_in = sum(turn.input_tokens for turn in result.turns)
            tokens_out = sum(turn.output_tokens for turn in result.turns)
            lines.append(
                f"| {result.case.id} "
                f"| {'pass' if result.verdict.passed else 'FAIL'} "
                f"| {len(result.turns)} | {result.model_calls} "
                f"| {result.tool_calls} | {tokens_in}/{tokens_out} "
                f"| {_dollars(result.cost_usd)} | {result.verdict.reason} |"
            )
        s = self.summary()
        lines += [
            "",
            f"Passed {s['passed']} of {s['cases']} ({s['pass_rate']:.1%}). "
            f"Model calls per turn: {s['model_calls_per_turn']:.2f}. "
            f"Turns that did not succeed: {s['not_success_turns']}. "
            f"Re-prompts: {s['reprompts']}.",
            f"Cost: {_dollars(s['cost_usd'])} total, "
            f"{_dollars(s['cost_per_pass_usd'])} per passed case.",
        ]
        if timing:
            lines.append(
                f"Turn time: p50 {s['turn_seconds_p50']:.1f} s, "
                f"p95 {s['turn_seconds_p95']:.1f} s."
            )
        return "\n".join(lines)
