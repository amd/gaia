# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Summarise recorded Claude Code sessions as the real-world yardstick.

A benchmark says which arm is better. It cannot say whether the benchmark is
hard enough to matter. Only real recorded work can, and the answer the first
time this was run was uncomfortable: the 8-task suite's *hardest* attempt sat at
the 60th percentile of real tasks, so 39% of genuine work was harder than
anything the benchmark had ever produced.

This emits that comparison as a small JSON file the report reads. Two rules
govern it:

* **Nothing session-derived is written into the repository.** The output is a
  handful of aggregate numbers, and it is written wherever the caller points it
  — a private experiment folder.
* **It is never an arm.** These tasks were not run for a benchmark, had no
  verifier, and nobody scored them. They are observational, and every table
  that shows them labels them ``Claude Code sessions (recorded)`` so they cannot
  be mistaken for a measured result.

Only *human-originated* tasks count. Automation and boilerplate turns are
excluded, because a scheduled job firing 200 times would otherwise dominate the
distribution it is supposed to describe.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import statistics as st
from pathlib import Path
from typing import Any, Dict, List

from gaia.factory.harvest import use_cases as uc_module
from gaia.factory.harvest.tasks import origin_of, segment

#: Assumptions behind the per-use-case cost estimate, kept together so a
#: reader can re-price the table by changing three numbers.
PREFILL_TOKENS = 17_000  # system prompt + tool schemas, measured
CACHE_HIT = 0.95  # share of that prefix served from cache, measured
USD_IN, USD_CACHED, USD_OUT = 5.0 / 1e6, 0.5 / 1e6, 25.0 / 1e6
OUTPUT_TOKENS_PER_STEP = 250  # typical assistant turn


def _est_cost(steps: int, context_tokens: int) -> float:
    """Estimated API cost of one task at Opus list pricing."""
    if not steps:
        return 0.0
    fresh = PREFILL_TOKENS * (1 - CACHE_HIT) + context_tokens / max(steps, 1)
    cached = PREFILL_TOKENS * CACHE_HIT
    return steps * (
        fresh * USD_IN + cached * USD_CACHED + OUTPUT_TOKENS_PER_STEP * USD_OUT
    )


def _with_token_share(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Add each use case's share of total estimated spend and tokens.

    Per-task cost ranks how expensive one run is; this ranks where the money
    actually goes. They disagree sharply — a one-step question that runs
    thousands of times can outweigh a rare long task.
    """
    total_cost = sum(v["est_cost_usd"] * v["tasks"] for v in profile.values()) or 1.0
    total_tok = (
        sum(v["median_result_tokens"] * v["tasks"] for v in profile.values()) or 1
    )
    for v in profile.values():
        v["share_of_spend"] = round(
            100 * v["est_cost_usd"] * v["tasks"] / total_cost, 1
        )
        v["share_of_tokens"] = round(
            100 * v["median_result_tokens"] * v["tasks"] / total_tok, 1
        )
    return profile


def _pct(sorted_values: List[int], value: float) -> float:
    if not sorted_values:
        return 0.0
    return 100 * bisect.bisect_left(sorted_values, value) / len(sorted_values)


def summarise(traces_path: Path) -> Dict[str, Any]:
    """Per-task complexity of recorded sessions, keyed by one human ask."""
    steps: List[int] = []
    files: List[int] = []
    families: List[int] = []
    failures: List[int] = []
    sessions = 0
    sessions_delegating = 0

    use_case_counts: collections.Counter = collections.Counter()
    per_use_case: Dict[str, Dict[str, Any]] = {}
    domain_counts: collections.Counter = collections.Counter()

    with traces_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            trace = json.loads(line)
            sessions += 1
            # Use-case and domain labels come from the harvest classifier, so
            # the enterprise grouping is measured against real work rather than
            # assumed from the suite's own composition.
            for task in segment(trace):
                if getattr(task, "is_work", False):
                    uc = uc_module.classify(task)
                    use_case_counts[uc] += 1
                    from gaia.factory.harvest.tasks import classify as _cls

                    domain_counts[_cls(task)[1]] += 1
                    # Per-use-case behaviour, from the recorded corpus. This is
                    # an order of magnitude more observations per use case than
                    # the benchmark has, so it is the honest place to describe
                    # what a use case *costs* rather than how well we score it.
                    steps_here = list(getattr(task, "steps", []) or [])
                    bucket = per_use_case.setdefault(
                        uc,
                        {
                            "steps": [],
                            "changed": 0,
                            "n": 0,
                            "tools": [],
                            "chars": [],
                            "failed": [],
                        },
                    )
                    bucket["n"] += 1
                    bucket["steps"].append(len(steps_here))
                    bucket["tools"].append(
                        len({s.get("tool") for s in steps_here if s.get("tool")})
                    )
                    bucket["chars"].append(
                        sum(int(s.get("result_chars") or 0) for s in steps_here)
                    )
                    bucket["failed"].append(
                        sum(1 for s in steps_here if not s.get("ok"))
                    )
                    if any(s.get("family") in ("edit", "write") for s in steps_here):
                        bucket["changed"] += 1
            if trace.get("subagents"):
                sessions_delegating += 1
            by_ask = collections.defaultdict(list)
            for step in trace.get("steps") or []:
                by_ask[step.get("prompt_index", -1)].append(step)
            prompts = trace.get("prompts") or []
            for idx, group in by_ask.items():
                if idx < 0 or idx >= len(prompts):
                    continue
                ask = prompts[idx]
                if origin_of(ask if isinstance(ask, str) else str(ask)) != "human":
                    continue
                steps.append(len(group))
                touched = {
                    s.get("arg_digest", "")
                    for s in group
                    if s.get("family") in ("read", "edit", "write")
                }
                files.append(len({f for f in touched if f and len(f) > 3}))
                families.append(
                    len({s.get("family") for s in group if s.get("family")})
                )
                failures.append(sum(1 for s in group if not s.get("ok")))

    def dist(values: List[int]) -> Dict[str, float]:
        ordered = sorted(values)

        def at(p: float) -> int:
            return ordered[min(len(ordered) - 1, int(len(ordered) * p))]

        return {
            "median": st.median(ordered),
            "p75": at(0.75),
            "p90": at(0.90),
            "p99": at(0.99),
            "max": max(ordered),
        }

    ordered_steps = sorted(steps)
    return {
        "source": "Claude Code sessions (recorded, not a benchmark run)",
        "human_tasks": len(steps),
        "sessions": sessions,
        "sessions_using_subagents": sessions_delegating,
        "steps": dist(steps),
        "files_touched": dist(files),
        "tool_families": dist(families),
        "failed_calls": dist(failures),
        "share_over_20_steps": round(
            100 * sum(1 for s in steps if s > 20) / len(steps), 1
        ),
        "share_over_50_steps": round(
            100 * sum(1 for s in steps if s > 50) / len(steps), 1
        ),
        # Lets the report place any benchmark figure inside this distribution.
        "use_case_counts": dict(use_case_counts.most_common()),
        "use_case_profile": _with_token_share(
            {
                uc: {
                    "tasks": b["n"],
                    "share_of_tasks": round(100 * b["n"] / max(len(steps), 1), 1),
                    "median_steps": int(st.median(b["steps"])) if b["steps"] else 0,
                    "p90_steps": (
                        sorted(b["steps"])[
                            min(len(b["steps"]) - 1, int(len(b["steps"]) * 0.9))
                        ]
                        if b["steps"]
                        else 0
                    ),
                    "max_steps": max(b["steps"]) if b["steps"] else 0,
                    "median_tools": int(st.median(b["tools"])) if b["tools"] else 0,
                    "pct_changing_files": round(100 * b["changed"] / max(b["n"], 1)),
                    # Tool output the model had to read back. A rough but honest
                    # proxy for context pressure: ~4 chars per token.
                    "median_result_tokens": (
                        int(st.median(b["chars"]) / 4) if b["chars"] else 0
                    ),
                    "p90_result_tokens": (
                        int(
                            sorted(b["chars"])[
                                min(len(b["chars"]) - 1, int(len(b["chars"]) * 0.9))
                            ]
                            / 4
                        )
                        if b["chars"]
                        else 0
                    ),
                    "median_failed_calls": (
                        int(st.median(b["failed"])) if b["failed"] else 0
                    ),
                    # Rough per-task API cost, stated as an estimate because it
                    # rests on three assumptions the corpus cannot confirm: a fixed
                    # prefix of PREFILL_TOKENS re-sent per step, CACHE_HIT of it
                    # served from cache, and Opus list pricing. Useful for ranking
                    # use cases against each other; not a bill.
                    "est_cost_usd": round(
                        _est_cost(
                            int(st.median(b["steps"])) if b["steps"] else 0,
                            int(st.median(b["chars"]) / 4) if b["chars"] else 0,
                        ),
                        2,
                    ),
                }
                for uc, b in sorted(per_use_case.items(), key=lambda kv: -kv[1]["n"])
            }
        ),
        "domain_counts": dict(domain_counts.most_common()),
        "percentile_of": {
            str(v): round(_pct(ordered_steps, v), 1)
            for v in (5, 10, 14, 20, 23, 50, 100)
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--traces", type=Path, required=True, help="traces.jsonl")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    summary = summarise(a.traces)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"wrote {a.out}: {summary['human_tasks']} human tasks from "
        f"{summary['sessions']} recorded sessions"
    )


if __name__ == "__main__":
    main()
