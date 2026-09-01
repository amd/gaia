# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Effectiveness and friction analysis over normalized session traces.

Separate from ``reader`` on purpose: the reader transcribes what a transcript
says, this module is the opinionated read of *how well it went*.  Every signal
here is a proxy — a corrected prompt is evidence of friction, not proof of it —
so results are meant to be compared between workflows, not read as verdicts.
"""

import re
from collections import Counter
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from gaia.factory.harvest.reader import Trace

# A user turn that pushes back on what the model just did. Deliberately narrow:
# bare "stop"/"wrong"/"again" were dropped after they proved to match harness
# text and ordinary follow-ups far more often than real corrections.
CORRECTION_PATTERNS = [
    r"\bthat'?s not\b",
    r"\bthat is not\b",
    r"\bnot what i\b",
    r"\bstill (?:not|broken|failing|fails|wrong|doesn'?t)\b",
    r"\b(?:doesn'?t|didn'?t) work\b",
    r"\bis wrong\b",
    r"\byou (?:broke|removed|deleted|missed|forgot|ignored)\b",
    r"\bi (?:said|told you|asked for)\b",
    r"\bwhy did you\b",
    r"\bno[,.]\s",
]

APPROVAL_PATTERNS = [
    r"\bperfect\b",
    r"\bthanks\b",
    r"\bthank you\b",
    r"\blgtm\b",
    r"\bship it\b",
    r"\blooks good\b",
]

_CORRECTION_RE = re.compile("|".join(CORRECTION_PATTERNS), re.I)
_APPROVAL_RE = re.compile("|".join(APPROVAL_PATTERNS), re.I)

# Failure taxonomy, applied to the leading text of a failed tool_result.
# ORDER MATTERS: first match wins, so every specific class must precede the
# generic ``command_failed`` — a Bash timeout also carries a non-zero exit
# code and would otherwise be filed as a plain command failure.
ERROR_CLASSES: List[tuple[str, str]] = [
    ("interrupted", r"\[request interrupted"),
    (
        "user_rejected",
        r"user (?:doesn't want|rejected|denied)|requested permission|tool use was rejected",
    ),
    ("timeout", r"timed out|timeout|exit code 143"),
    (
        "string_not_found",
        r"string to replace not found|old_string not found|no match(?:es)? found|not found in file",
    ),
    (
        "stale_read",
        r"has (?:been|not been) (?:modified|read)|file has changed|read it first|must (?:read|use) the .*tool",
    ),
    ("command_not_found", r"command not found|is not recognized as an internal"),
    ("file_not_found", r"no such file|does not exist|cannot find|not found|enoent"),
    ("permission", r"permission denied|access is denied|eacces"),
    ("syntax_error", r"syntaxerror|parse error|unexpected token|indentationerror"),
    ("import_error", r"modulenotfounderror|importerror|cannot import"),
    ("test_failure", r"assertionerror|test(?:s)? failed|failed[,:] \d+|\d+ failed"),
    ("type_error", r"typeerror|attributeerror|nameerror|keyerror|valueerror"),
    (
        "network",
        r"connection refused|econnrefused|unreachable|http (?:50[234]|429)|status (?:50[234]|429)",
    ),
    ("git_conflict", r"merge conflict|would be overwritten|non-fast-forward"),
    ("empty_result", r"no files found|returned no results"),
    ("command_failed", r"exit code [1-9]|command failed|non-zero"),
]

_ERROR_RES = [(name, re.compile(pat, re.I)) for name, pat in ERROR_CLASSES]


def classify_error(text: str) -> str:
    for name, rx in _ERROR_RES:
        if rx.search(text):
            return name
    return "unclassified"


def duration_minutes(trace: Trace) -> Optional[float]:
    """Wall-clock span of a session, or ``None`` if timestamps are unusable.

    This is elapsed time, not time worked — a session left open overnight
    reports the whole night, so only the median is trustworthy.
    """
    if not trace.started_at or not trace.last_at:
        return None
    try:
        a = datetime.fromisoformat(trace.started_at.replace("Z", "+00:00"))
        b = datetime.fromisoformat(trace.last_at.replace("Z", "+00:00"))
        delta = (b - a).total_seconds() / 60.0
    # TypeError: one stamp carried a zone offset and the other did not.
    except (TypeError, ValueError):
        return None
    return delta if delta >= 0 else None


def thrash_runs(trace: Trace, min_run: int = 3) -> List[tuple[str, int]]:
    """Runs of the identical call — same tool AND same full arguments.

    Identity is ``arg_hash``, never the display digest: keying on one argument
    would merge distinct edits to a single file into a fake repeat, which is
    how an earlier version of this overstated thrash roughly forty-fold.
    """
    runs: List[tuple[str, int]] = []
    prev_key = None
    count = 0
    for step in trace.steps:
        key = f"{step.tool}:{step.arg_hash}"
        if key == prev_key:
            count += 1
        else:
            if prev_key is not None and count >= min_run:
                runs.append((prev_key, count))
            prev_key = key
            count = 1
    if prev_key is not None and count >= min_run:
        runs.append((prev_key, count))
    return runs


def redundant_calls(trace: Trace, min_run: int = 3) -> int:
    """Calls in a thrash run beyond the first — the actually wasted ones."""
    return sum(c - 1 for _, c in thrash_runs(trace, min_run))


def repair_loops(trace: Trace, window: int = 6) -> int:
    """Count edit → verify → edit cycles on the SAME file.

    Non-overlapping: a burst of edits before one shell command is one cycle,
    not one per edit. Requiring the same target keeps an ordinary two-file
    change from scoring as rework.
    """
    steps = trace.steps
    cycles = 0
    i = 0
    while i < len(steps):
        if steps[i].family != "edit":
            i += 1
            continue
        target = steps[i].arg_digest
        shell_at = None
        for j in range(i + 1, min(i + 1 + window, len(steps))):
            if steps[j].family == "shell":
                shell_at = j
                break
        if shell_at is None:
            i += 1
            continue
        for k in range(shell_at + 1, min(shell_at + 1 + window, len(steps))):
            if steps[k].family == "edit" and steps[k].arg_digest == target:
                cycles += 1
                i = k  # resume from the closing edit; the burst is one cycle
                break
        else:
            i += 1
            continue
        i += 1
    return cycles


def error_profile(traces: List[Trace], include_subagents: bool = True) -> Dict:
    """Where failures land, and what happens next.

    Failure *rate* alone says little — a tool called once and failed is not a
    problem, and a 3% corpus rate hides tools that fail a third of the time.
    This reports rate per tool, position within a session, whether failures
    arrive in streaks, and whether the very next call recovers.
    """
    scope = (
        [t for root in traces for t in root.walk()]
        if include_subagents
        else list(traces)
    )

    per_tool_total: Counter = Counter()
    per_tool_fail: Counter = Counter()
    per_family_total: Counter = Counter()
    per_family_fail: Counter = Counter()
    main_total = main_fail = sub_total = sub_fail = 0
    # Failure rate across the session, in tenths of its length.
    decile_total = [0] * 10
    decile_fail = [0] * 10
    streaks: Counter = Counter()
    recovered = retried_same = 0
    fail_events = 0

    for t in scope:
        resolved = [s for s in t.steps if s.ok is not None]
        n = len(resolved)
        run = 0
        for i, s in enumerate(resolved):
            per_tool_total[s.tool] += 1
            per_family_total[s.family] += 1
            if t.kind == "subagent":
                sub_total += 1
            else:
                main_total += 1
            if n:
                d = min(9, int(10 * i / n))
                decile_total[d] += 1
            if not s.ok:
                per_tool_fail[s.tool] += 1
                per_family_fail[s.family] += 1
                if t.kind == "subagent":
                    sub_fail += 1
                else:
                    main_fail += 1
                if n:
                    decile_fail[min(9, int(10 * i / n))] += 1
                run += 1
                nxt = resolved[i + 1] if i + 1 < len(resolved) else None
                # A failure that ends its trace can never recover; counting it
                # in the denominator deflates the recovery rate by construction.
                if nxt is not None:
                    fail_events += 1
                    if nxt.ok:
                        recovered += 1
                    if nxt.tool == s.tool and nxt.arg_hash == s.arg_hash:
                        retried_same += 1
            else:
                if run:
                    streaks[min(run, 5)] += 1
                run = 0
        if run:
            streaks[min(run, 5)] += 1

    worst = []
    for tool, total in per_tool_total.most_common():
        # A rate on a handful of calls is noise, not a signal.
        if total >= 50:
            worst.append(
                {
                    "tool": tool,
                    "calls": total,
                    "failures": per_tool_fail[tool],
                    "rate_pct": round(100 * per_tool_fail[tool] / total, 1),
                }
            )
    worst.sort(key=lambda r: -r["rate_pct"])

    return {
        "by_tool": worst[:15],
        "by_family": {
            f: {
                "calls": per_family_total[f],
                "failures": per_family_fail[f],
                "rate_pct": round(100 * per_family_fail[f] / per_family_total[f], 1),
            }
            for f in sorted(per_family_total, key=lambda k: -per_family_total[k])
        },
        "main_vs_subagent": {
            "main_rate_pct": (
                round(100 * main_fail / main_total, 2) if main_total else 0
            ),
            "subagent_rate_pct": (
                round(100 * sub_fail / sub_total, 2) if sub_total else 0
            ),
            "main_failures": main_fail,
            "subagent_failures": sub_fail,
        },
        "rate_by_session_decile_pct": [
            round(100 * f / t, 1) if t else 0.0
            for f, t in zip(decile_fail, decile_total)
        ],
        "streaks": {
            (f"len_{k}" if k < 5 else "len_5plus"): v
            for k, v in sorted(streaks.items())
        },
        "after_a_failure": {
            "failures_with_a_next_call": fail_events,
            "next_call_succeeded_pct": (
                round(100 * recovered / fail_events, 1) if fail_events else 0
            ),
            "retried_identical_call_pct": (
                round(100 * retried_same / fail_events, 1) if fail_events else 0
            ),
        },
    }


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(pct / 100.0 * (len(ordered) - 1) + 0.5))
    return ordered[idx]


def analyze(traces: List[Trace], include_subagents: bool = True) -> Dict:
    """Friction and effectiveness across ``traces``.

    ``include_subagents`` folds delegated runs into the tool-level signals
    (errors, thrash, context pressure).  They carry a large share of all tool
    work, so excluding them silently reports on a fraction of the corpus.
    """
    scope: Iterable[Trace] = (
        [t for root in traces for t in root.walk()] if include_subagents else traces
    )
    scope = list(scope)

    errors: Counter = Counter()
    error_by_tool: Counter = Counter()
    big_results = 0
    unresolved = 0
    for t in scope:
        for s in t.steps:
            if s.ok is None:
                unresolved += 1
                continue
            if not s.ok:
                cls = classify_error(s.error)
                errors[cls] += 1
                error_by_tool[f"{s.tool}/{cls}"] += 1
            if s.result_chars > 40000:
                big_results += 1

    thrash_sessions = 0
    redundant = 0
    repair_total = 0
    for t in scope:
        r = redundant_calls(t)
        if r:
            thrash_sessions += 1
            redundant += r
        repair_total += repair_loops(t)

    # Human-interaction signals belong to real sessions; a subagent has no user.
    corrections = 0
    approvals = 0
    sessions_with_correction = 0
    sessions_with_interrupt = 0
    total_interrupts = 0
    durations: List[float] = []
    for t in traces:
        had = False
        for p in t.prompts[1:]:
            if _CORRECTION_RE.search(p):
                corrections += 1
                had = True
            if _APPROVAL_RE.search(p):
                approvals += 1
        if had:
            sessions_with_correction += 1
        total_i = sum(x.interrupts for x in t.walk())
        if total_i:
            sessions_with_interrupt += 1
            total_interrupts += total_i
        d = duration_minutes(t)
        if d is not None:
            durations.append(d)

    sub_count = sum(len(t.subagents) for t in traces)
    sessions_with_subs = sum(1 for t in traces if t.subagents)
    sub_steps = sum(s.total_calls for t in traces for s in t.subagents)
    sub_tools: Counter = Counter()
    for t in traces:
        for s in t.subagents:
            sub_tools.update(s.tool_counts)

    n = len(traces) or 1
    scope_calls = sum(t.total_calls for t in scope)
    return {
        "scope": {
            "sessions": len(traces),
            "traces_including_subagents": len(scope),
            "tool_calls_in_scope": scope_calls,
            "unresolved_calls": unresolved,
        },
        "error_classes": dict(errors.most_common()),
        "error_by_tool": dict(error_by_tool.most_common(25)),
        "error_profile": error_profile(traces, include_subagents),
        "corrections": {
            "total_correcting_turns": corrections,
            "sessions_with_correction": sessions_with_correction,
            "pct_sessions_with_correction": round(
                100 * sessions_with_correction / n, 1
            ),
            "total_approval_turns": approvals,
        },
        "interrupts": {
            "total": total_interrupts,
            "sessions": sessions_with_interrupt,
            "pct_sessions": round(100 * sessions_with_interrupt / n, 1),
        },
        "duration_minutes": {
            "p50": round(_percentile(durations, 50), 1),
            "p75": round(_percentile(durations, 75), 1),
            "p90": round(_percentile(durations, 90), 1),
        },
        "thrash": {
            "traces_with_repeat_runs": thrash_sessions,
            "pct_traces": (
                round(100 * thrash_sessions / len(scope), 1) if scope else 0.0
            ),
            "redundant_calls": redundant,
            "pct_of_calls": (
                round(100 * redundant / scope_calls, 2) if scope_calls else 0.0
            ),
        },
        "repair_loops": {
            "total_same_file_edit_verify_edit": repair_total,
            "per_session_avg": round(repair_total / n, 2),
        },
        "context_pressure": {"tool_results_over_40k_chars": big_results},
        "delegation": {
            "sessions_delegating": sessions_with_subs,
            "pct_sessions": round(100 * sessions_with_subs / n, 1),
            "subagent_transcripts": sub_count,
            "subagent_tool_calls": sub_steps,
            "avg_subagents_per_delegating_session": round(
                sub_count / (sessions_with_subs or 1), 1
            ),
            "subagent_top_tools": dict(sub_tools.most_common(12)),
        },
    }
