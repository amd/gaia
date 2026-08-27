# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scan the local Claude Code corpus and write traces + aggregate stats.

Deterministic and LLM-free.  Writes to ``~/.gaia/cache/factory/`` so nothing
derived from private session data lands in a repository.

Usage::

    python -m gaia.factory.harvest.scan [--root DIR] [--out DIR]
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from gaia.factory.harvest.analyze import analyze, duration_minutes
from gaia.factory.harvest.reader import Trace, iter_traces

DEFAULT_OUT = Path.home() / ".gaia" / "cache" / "factory"

# USD per million tokens, per token class. Source: Anthropic pricing page,
# https://platform.claude.com/docs/en/about-claude/pricing (retrieved 2026-08-25).
# Cache multipliers on the same page: 5-minute write 1.25x base input,
# 1-hour write 2x, cache read 0.1x. Only the 5-minute write is modelled —
# transcripts do not record which TTL a cache_creation used, and 5 minutes is
# the API default.
PRICING: Dict[str, Dict[str, float]] = {
    "claude-fable-5": {"input": 10.0, "output": 50.0, "write": 12.50, "read": 1.00},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "write": 6.25, "read": 0.50},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "write": 6.25, "read": 0.50},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "write": 6.25, "read": 0.50},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "write": 6.25, "read": 0.50},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "write": 2.50, "read": 0.20},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "write": 3.75, "read": 0.30},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "write": 1.25, "read": 0.10},
}


# Model ids in transcripts may carry a date suffix; match on the longest
# registered prefix so `claude-haiku-4-5-20251001` prices as Haiku 4.5.
def price_for(model: str) -> Optional[Dict[str, float]]:
    """Rates for a model id, or None if it is not a priced Claude model.

    Returns None rather than guessing — `<synthetic>` and other harness
    pseudo-models must not be silently priced as a real tier.
    """
    lowered = model.lower()
    best = None
    for key, rate in PRICING.items():
        if lowered.startswith(key) and (best is None or len(key) > len(best[0])):
            best = (key, rate)
    return best[1] if best else None


def estimate_cost(trace: Trace) -> Dict[str, float]:
    """Cost for one trace, split into fresh tokens vs cache.

    Priced per model actually used: a trace that switches model mid-run is
    billed at each model's own rate for the tokens it produced.
    """
    fresh = cached = 0.0
    unpriced = 0
    for model, u in trace.usage_by_model.items():
        rate = price_for(model)
        if rate is None:
            unpriced += u.total
            continue
        fresh += (
            u.input_tokens * rate["input"] + u.output_tokens * rate["output"]
        ) / 1_000_000
        cached += (
            u.cache_write_tokens * rate["write"] + u.cache_read_tokens * rate["read"]
        ) / 1_000_000
    return {
        "fresh": fresh,
        "cache": cached,
        "total": fresh + cached,
        "unpriced_tokens": unpriced,
    }


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(pct / 100.0 * (len(ordered) - 1) + 0.5))
    return ordered[idx]


def summarize(traces: List[Trace]) -> Dict:
    tools: Counter = Counter()
    families: Counter = Counter()
    projects: Counter = Counter()
    models: Counter = Counter()
    all_traces = [t for root in traces for t in root.walk()]

    for t in all_traces:
        tools.update(t.tool_counts)
        families.update(t.family_counts)
        for m in t.models:
            models[m] += 1
    for t in traces:
        projects[t.project] += 1

    steps = [t.total_calls for t in traces]
    # One assistant turn == one inference call. Always >= tool calls, since a
    # turn can carry several tool_use blocks or none at all.
    model_calls = sum(t.assistant_turns for t in all_traces)
    model_calls_main = sum(t.assistant_turns for t in traces)
    turns = [len(t.prompts) for t in traces]
    attempts = sum(t.attempt_count for t in all_traces)
    failures = attempts - sum(t.success_count for t in all_traces)
    main_calls = sum(t.total_calls for t in traces)
    sub_calls = sum(t.total_calls for t in all_traces) - main_calls

    total_usage = Counter()
    tokens_by_model: Counter = Counter()
    costs = []
    unpriced_tokens = 0
    for t in all_traces:
        total_usage["input"] += t.usage.input_tokens
        total_usage["output"] += t.usage.output_tokens
        total_usage["cache_read"] += t.usage.cache_read_tokens
        total_usage["cache_write"] += t.usage.cache_write_tokens
        for m, u in t.usage_by_model.items():
            tokens_by_model[m] += u.total
    fresh_total = cache_total = 0.0
    for t in traces:
        fresh = cache = 0.0
        for tr in t.walk():
            c = estimate_cost(tr)
            fresh += c["fresh"]
            cache += c["cache"]
            unpriced_tokens += c["unpriced_tokens"]
        fresh_total += fresh
        cache_total += cache
        costs.append(fresh + cache)

    buckets = Counter()
    for n in steps:
        if n == 0:
            buckets["0 (conversation only)"] += 1
        elif n <= 5:
            buckets["1-5"] += 1
        elif n <= 20:
            buckets["6-20"] += 1
        elif n <= 50:
            buckets["21-50"] += 1
        elif n <= 100:
            buckets["51-100"] += 1
        else:
            buckets["100+"] += 1

    tok_total = sum(total_usage.values()) or 1
    return {
        "sessions": len(traces),
        "traces_including_subagents": len(all_traces),
        "tool_calls_main": main_calls,
        "tool_calls_subagent": sub_calls,
        "total_tool_calls": main_calls + sub_calls,
        "model_calls": model_calls,
        "model_calls_main": model_calls_main,
        "model_calls_subagent": model_calls - model_calls_main,
        "tool_calls_per_model_call": (
            round((main_calls + sub_calls) / model_calls, 2) if model_calls else 0.0
        ),
        "subagent_share_pct": round(
            100 * sub_calls / max(main_calls + sub_calls, 1), 1
        ),
        "resolved_tool_calls": attempts,
        "failed_tool_calls": failures,
        "failure_rate": round(failures / attempts, 4) if attempts else 0.0,
        "unresolved_tool_calls": sum(t.unresolved_count for t in all_traces),
        "skipped_lines": sum(t.skipped_lines for t in all_traces),
        "steps_per_session": {
            "p50": percentile(steps, 50),
            "p75": percentile(steps, 75),
            "p90": percentile(steps, 90),
            "max": max(steps) if steps else 0,
        },
        "turns_per_session": {
            "p50": percentile(turns, 50),
            "p90": percentile(turns, 90),
            "max": max(turns) if turns else 0,
        },
        "step_buckets": dict(buckets),
        "tokens": {
            **dict(total_usage),
            "total": tok_total,
            "cache_read_pct": round(100 * total_usage["cache_read"] / tok_total, 1),
            "output_pct": round(100 * total_usage["output"] / tok_total, 1),
        },
        "cost_usd": {
            # Fresh = input + output only (what a cache-less caller would pay).
            "fresh_tokens_only": round(fresh_total, 2),
            "cache_only": round(cache_total, 2),
            "total_est": round(fresh_total + cache_total, 2),
            "per_session_p50": round(percentile(costs, 50), 3),
            "per_session_p90": round(percentile(costs, 90), 3),
            "unpriced_tokens": unpriced_tokens,
            "unpriced_pct": round(100 * unpriced_tokens / tok_total, 2),
        },
        "tools": dict(tools.most_common()),
        "families": dict(families.most_common()),
        "top_projects": dict(projects.most_common(20)),
        # Counted per trace-model pair: a trace that switches model appears
        # under both, so these sum above the trace count by design.
        "models": dict(models.most_common()),
        "model_trace_pairs": sum(models.values()),
        "tokens_by_model": dict(tokens_by_model.most_common()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=None, help="Claude projects dir")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output dir")
    args = ap.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    traces = list(iter_traces(args.root))
    if not traces:
        raise SystemExit(
            f"No Claude Code transcripts found under "
            f"{args.root or Path.home() / '.claude' / 'projects'}. "
            "Pass --root if your sessions live elsewhere."
        )

    with (out / "traces.jsonl").open("w", encoding="utf-8") as fh:
        for t in traces:
            fh.write(json.dumps(t.to_dict(), ensure_ascii=False) + "\n")

    stats = summarize(traces)
    stats["effectiveness"] = analyze(traces)
    (out / "stats.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # The intent corpus — one line per session, for use-case clustering.
    with (out / "intents.jsonl").open("w", encoding="utf-8") as fh:
        for t in traces:
            fh.write(
                json.dumps(
                    {
                        "session_id": t.session_id,
                        "project": t.project,
                        "title": t.title,
                        "goal": " ".join(t.goal.split())[:600],
                        "steps": t.total_calls,
                        "turns": len(t.prompts),
                        "families": t.family_counts,
                        "tokens": t.usage.total,
                        "cost_usd": round(estimate_cost(t)["total"], 4),
                        "duration_min": duration_minutes(t),
                        "subagents": len(t.subagents),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(
        f"sessions={stats['sessions']} "
        f"calls={stats['total_tool_calls']} "
        f"tokens={stats['tokens']['total']:,} "
        f"est_cost=${stats['cost_usd']['total_est']:,.2f}"
    )
    print(f"wrote traces.jsonl, intents.jsonl, stats.json to {out}")


if __name__ == "__main__":
    main()
