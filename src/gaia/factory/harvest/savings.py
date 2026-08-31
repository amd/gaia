# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What each proposed architectural change would actually have saved.

A request's prompt is the whole conversation so far, so a tool result emitted
early is paid for again on every later request in that run.  That *carry
multiplier* — not the size of any single result — is what the numbers here
measure.

Shares are taken against the tokens the model *read* (input + cache read +
cache write), not against the corpus total, which also counts output.  Output
is never carried into a later prompt as input, so including it would understate
every share.

Two models, kept separate because they have very different confidence:

* **Attributed savings** — for mechanisms that remove specific, identifiable
  content (a repeated read, the tail of an oversized result, the tool schema
  block).  Measured directly against the steps in the corpus.  Conservative:
  tool results are only part of what a prompt carries, so anything this model
  cannot see, it does not claim.
* **Structural savings** — what bounding the working set would do.  This is an
  upper bound and is labelled as one: it assumes the agent can do the same work
  while never exceeding the bound, which the corpus cannot prove.

Usage::

    python -m gaia.factory.harvest.savings [--cache DIR] [--labels FILE]
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from gaia.factory.harvest.context import collect
from gaia.factory.harvest.scan import DEFAULT_OUT

# Claude's tokenizer averages close to 4 characters per token on source code
# and shell output.  Used only to convert measured result *characters* into
# tokens; every token figure taken straight from ``usage`` is exact.
CHARS_PER_TOKEN = 4.0

# The tool schema block rides every request.  47 distinct tool names were seen;
# 4K tokens is an estimate of their combined JSON schema, not a measurement,
# and is flagged as such wherever it is reported.
SCHEMA_TOKENS = 4000

# Cap used for the result-budgeting mechanism: results larger than this are
# assumed to be summarised to it and the tail retrieved on demand.
RESULT_CAP_TOKENS = 2000


def _pct(n: float, d: float, places: int = 1) -> str:
    """A share, or an em-dash when the denominator is zero."""
    return f"{100 * n / d:.{places}f}%" if d else "—"


def _inflation(unadjusted: float, adjusted: float) -> str:
    """How much larger ``unadjusted`` is than ``adjusted``, as a percentage."""
    return f"{100 * unadjusted / adjusted - 100:.0f}%" if adjusted else "—"


def units(trace: dict) -> Iterator[Tuple[List[dict], int]]:
    """Every independent context in a trace: the session, then each subagent.

    A subagent has its own window, so its results are carried only within it.
    Treating a session and its subagents as one context overstates carry.
    """

    yield trace["steps"], trace.get("assistant_turns") or 1
    for sub in trace["subagents"]:
        yield sub["steps_detail"], sub.get("assistant_turns") or 1


def segment_lengths(series: Optional[List[int]], model_calls: int) -> List[int]:
    """Split a run at its context resets.

    Compaction discards the history, so a result emitted before a reset is not
    carried past it.  A reset shows up in the per-request prompt sizes as a drop
    to below half the running maximum.  Without this the carry model bills every
    early result for the whole run; ``main`` reports the measured overstatement.
    """

    if not series or len(series) < 2:
        return [model_calls]
    cuts, peak = [], series[0]
    for i in range(1, len(series)):
        if series[i] < peak / 2:
            cuts.append(i)
            peak = series[i]
        else:
            peak = max(peak, series[i])
    if not cuts:
        return [model_calls]
    scale = model_calls / len(series)  # model calls need not equal requests
    bounds = [0] + [int(c * scale) for c in cuts] + [model_calls]
    lengths = [b - a for a, b in zip(bounds, bounds[1:])]
    return [n for n in lengths if n > 0] or [model_calls]


def segmented_multiplier(index: int, n_steps: int, segments: List[int]) -> float:
    """``carry_multiplier`` applied within the segment the step falls in."""

    pos = index / n_steps * sum(segments)
    start = 0.0
    for k, length in enumerate(segments):
        if pos < start + length or k == len(segments) - 1:
            local = (pos - start) / length if length else 0.0
            return length * (1 - min(local, 1.0))
        start += length
    return 0.0


def carry_multiplier(index: int, n_steps: int, model_calls: int) -> float:
    """How many later requests a result emitted at ``index`` rides on.

    Step position is mapped linearly onto the run's model calls, because the
    transcript records both counts but not which call each step belongs to.
    """

    return model_calls * (1 - index / n_steps)


def attribute(
    traces: List[dict], series_by_session: Optional[Dict[str, List[int]]] = None
) -> Dict[str, float]:
    """Carried tokens, and the share of them each mechanism could remove.

    The three mechanisms are made disjoint here, because they are not disjoint
    in nature: an oversized result that is *also* a repeat read would otherwise
    be counted once in full by the read cache and again, in part, by the
    budgeter.  A repeat read is credited only up to the cap; anything above it
    belongs to the budgeter.
    """

    out = {
        "carried_results": 0.0,
        "repeat_reads": 0.0,
        "over_cap": 0.0,
        "repeat_read_calls": 0,
        "stale_read_calls": 0,
        "read_calls": 0,
        "model_calls": 0,
        "resets": 0,
    }
    series_by_session = series_by_session or {}
    for trace in traces:
        series = series_by_session.get(trace.get("session_id", ""))
        for unit_no, (steps, calls) in enumerate(units(trace)):
            n = len(steps)
            out["model_calls"] += calls
            if not n:
                continue
            # Only the main session has a measured series. A subagent is short
            # and is never compacted, so one segment is correct for it.
            segments = segment_lengths(series, calls) if unit_no == 0 else [calls]
            out["resets"] += len(segments) - 1
            seen: set = set()
            touched: set = set()
            for i, step in enumerate(steps):
                tokens = step.get("result_chars", 0) / CHARS_PER_TOKEN
                mult = segmented_multiplier(i, n, segments)
                out["carried_results"] += tokens * mult
                path = step.get("arg_digest") or step["arg_hash"]
                if step["family"] in ("edit", "write"):
                    touched.add(path)
                if step["family"] == "read":
                    out["read_calls"] += 1
                    # Keyed on the path, not the full arguments: paging one
                    # file with successive offsets is a re-read of the same
                    # file, and a content cache would serve it.
                    if path in seen:
                        if path in touched:
                            # Edited since the last read: the bytes genuinely
                            # changed, so no cache could have served this.
                            out["stale_read_calls"] += 1
                        else:
                            out["repeat_reads"] += min(tokens, RESULT_CAP_TOKENS) * mult
                            out["repeat_read_calls"] += 1
                    else:
                        seen.add(path)
                if tokens > RESULT_CAP_TOKENS:
                    out["over_cap"] += (tokens - RESULT_CAP_TOKENS) * mult
    out["schema"] = float(SCHEMA_TOKENS * out["model_calls"])
    return out


def prompt_tokens(stats: dict) -> int:
    """Tokens the model read. Output is not carried into a later prompt as input."""

    tok = stats["tokens"]
    return tok["input"] + tok["cache_read"] + tok["cache_write"]


def mechanism_table(stats: dict, attr: Dict[str, float]) -> str:
    total = prompt_tokens(stats)
    rows = [
        (
            "Result budgeting",
            f"summarise any result over {RESULT_CAP_TOKENS:,} tokens; "
            "fetch the tail on demand",
            attr["over_cap"],
            "measured",
        ),
        (
            "Tool schemas out of the prompt",
            f"{SCHEMA_TOKENS:,} tok x {attr['model_calls']:,} model calls "
            "(schema size estimated)",
            attr["schema"],
            "estimated",
        ),
        (
            "Content-addressed read cache",
            f"{attr['repeat_read_calls']:,} of {attr['read_calls']:,} reads "
            f"re-read an unchanged path ({attr['stale_read_calls']:,} more were "
            "edited in between, so no cache could serve them)",
            attr["repeat_reads"],
            "measured",
        ),
    ]
    rows.sort(key=lambda r: -r[2])
    out = [
        f"_Shares are of the {total / 1e9:.2f} B tokens the model **read** "
        "(input + cache read + cache write). Output is excluded: it is not "
        "carried into a later prompt as input._",
        "",
        "| Mechanism | What it removes | Tokens saved | % of read tokens | Basis |",
        "|---|---|---:|---:|---|",
    ]
    for name, what, saved, basis in rows:
        out.append(
            f"| **{name}** | {what} | {saved / 1e6:,.0f}M | "
            f"{_pct(saved, total, places=2)} | {basis} |"
        )
    combined = sum(r[2] for r in rows)
    out.append(
        f"| **Combined** | made disjoint — see the module docstring | "
        f"**{combined / 1e6:,.0f}M** | **{_pct(combined, total, places=2)}** | |"
    )
    return "\n".join(out)


def bounded_table(requests: List[int]) -> str:
    """What holding every request under a fixed window would have cost."""

    total = sum(requests)
    out = [
        f"_Measured over {len(requests):,} individual API requests totalling "
        f"{total / 1e9:.2f} B prompt tokens._",
        "",
        "| Working-set bound | Prompt tokens | Reduction | Requests already under it |",
        "|---|---:|---:|---:|",
    ]
    for cap in (16384, 32768, 65536, 131072):
        # A request already under the bound is unchanged; charging it the full
        # bound would invent tokens and understate the saving.
        bounded = sum(min(r, cap) for r in requests)
        under = _pct(sum(1 for r in requests if r <= cap), len(requests))
        out.append(
            f"| {cap // 1024}K | {bounded / 1e9:.2f} B | "
            f"**{_pct(total - bounded, total, places=0)}** | {under} |"
        )
    return "\n".join(out)


def warm_cold_table(sessions: List[dict], d: int = 20640, t0: int = 10930) -> str:
    """Cold prefill vs prefilling only the changed suffix, per session.

    With prefix caching a turn re-prefills only what changed, at the depth it
    sits at.  Whether that is available decides whether shrinking the prompt is
    worth a lot or almost nothing, so the gap is reported as a distribution
    rather than a single number.  Curve constants are the published RTX 4090 8B
    fit; only the *ratio* is used, which is insensitive to ``t0``.
    """

    def ttft(n: float) -> float:
        return (n + n * n / (2 * d)) / t0

    ratios = []
    for sess in sessions:
        series = sess.get("series") or []
        if len(series) < 5:
            continue
        deltas = [series[i] - series[i - 1] for i in range(1, len(series))]
        growth = sum(deltas) / len(deltas)
        if growth <= 0:
            continue
        cold = sum(ttft(x) for x in series) / len(series)
        warm = sum(growth * (1 + x / d) / t0 for x in series) / len(series)
        ratios.append(cold / warm)
    ratios.sort()
    if not ratios:
        return (
            "_No session in this corpus has at least 5 requests with net context "
            "growth, so the cold/warm prefill ratio is not measurable here._"
        )

    def q(p: float) -> float:
        return ratios[int(p * (len(ratios) - 1))]

    return (
        f"_Over the {len(ratios)} sessions with at least 5 requests and net "
        "growth. Sessions shorter than that have too few points to fit._\n\n"
        "| | Cold prefill / warm prefill |\n|---|---:|\n"
        f"| p25 | {q(0.25):.0f}x |\n| **median** | **{q(0.50):.0f}x** |\n"
        f"| p75 | {q(0.75):.0f}x |"
    )


def load(cache: Path) -> Tuple[dict, List[dict]]:
    stats = json.loads((cache / "stats.json").read_text(encoding="utf-8"))
    # Iterate the handle rather than splitlines(): transcripts contain raw
    # U+2028/U+000B inside strings, which splitlines() treats as line breaks
    # and which then fail to parse.
    with (cache / "traces.jsonl").open(encoding="utf-8") as fh:
        traces = [json.loads(line) for line in fh if line.strip()]
    return stats, traces


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--projects", type=Path, default=Path.home() / ".claude" / "projects"
    )
    args = ap.parse_args()

    stats, traces = load(args.cache)
    sessions, requests = collect(args.cache, args.projects)
    attr = attribute(traces, {s["session_id"]: s.get("series") or [] for s in sessions})

    print("## Attributed savings — measured against specific content\n")
    print(mechanism_table(stats, attr))
    print(
        f"\n_Tool results carry {attr['carried_results'] / 1e9:.2f} B of the "
        f"corpus's {stats['tokens']['total'] / 1e9:.2f} B "
        f"({_pct(attr['carried_results'], stats['tokens']['total'], places=0)}). "
        "The rest is system prompt, tool schemas, user turns and the model's own "
        "prior output, carried the same way. A mechanism that touches only tool "
        "results cannot save more than that share._"
    )
    flat = attribute(traces)  # same model, no reset detection
    attr_carry = attr["over_cap"] + attr["repeat_reads"]
    print(
        f"\n_Carry stops at a context reset — {attr['resets']} were detected "
        "corpus-wide, as a drop in prompt size to below half the running maximum. "
        "Ignoring them would inflate the combined saving by "
        f"{_inflation(flat['over_cap'] + flat['repeat_reads'], attr_carry)} "
        "and the carried-results figure by "
        f"{_inflation(flat['carried_results'], attr['carried_results'])}. "
        "The schema row is unaffected: it scales with model calls, not carry._"
    )

    print("\n## Structural saving — bounding the working set (upper bound)\n")
    print(bounded_table(requests))
    print("\n## How much prefix caching changes the answer\n")
    print(warm_cold_table(sessions))


if __name__ == "__main__":
    main()
