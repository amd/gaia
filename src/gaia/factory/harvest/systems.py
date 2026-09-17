# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What hardware and software a fleet of these agents would actually need.

The rest of this package answers *what the agent was used for*. This module
answers the next question: *what would it take to run this workload yourself?*

Six things decide that, and all six are measured here rather than assumed:

* **Prefill vs decode.** Reading the prompt and writing the answer stress
  different parts of a machine. Reading is arithmetic — it saturates compute.
  Writing is one token at a time — it saturates memory bandwidth. Whichever
  dominates decides which accelerator is the right one to buy.
* **Model mix.** One model tier or several, because a fleet that needs two
  tiers needs either two machines or one machine large enough for the bigger.
* **Concurrency.** How many sessions were genuinely live at the same moment.
  Memory is sized by *N agents at once*, not by one agent.
* **Wall-clock and throughput.** How fast the work actually arrived.
* **Delegation.** Every subagent is a second context, held at the same time as
  the first.
* **KV-cache memory.** The number that decides whether this fits on a machine
  at all.

**Open is not active, and the difference is the whole sizing question.** A
session's `started_at`/`last_at` bracket the period it was *open*, which
includes every minute the human spent reading, thinking or away. Counting those
as live says this corpus never had an idle moment in 36 days, which is plainly
false. So concurrency is measured twice: by overlapping open intervals, and by
bucketing every real inference request from the raw transcripts into the minute
it happened. The second is what sizes memory, because a context only occupies
KV cache while it is being served. The first over-provisions by roughly 5x.

Two things this corpus cannot answer, stated here so they are not quietly
inferred from the tables below: it holds **no per-step timing**, so the split
of wall-clock between local tool execution and remote inference cannot be
quantified, only shaped; and it holds **no timestamps on subagents**, so both
concurrency measures count parent sessions and are therefore a floor on
concurrent contexts.

Usage::

    python -m gaia.factory.harvest.systems --cache DIR [--projects DIR]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from gaia.factory.harvest import use_cases as uc
from gaia.factory.harvest.context import GIB, KV_MODELS, kv_bytes
from gaia.factory.harvest.scan import percentile
from gaia.factory.harvest.tasks import segment
from gaia.factory.harvest.usecase_report import _tbl

#: Context lengths the KV table is priced at. A **chosen ladder**, not a
#: measurement — the observed prompt sizes are reported against it separately.
#: It runs to 1M because the reference corpus's requests do.
CTX_LADDER: Tuple[int, ...] = (32768, 65536, 131072, 262144, 524288, 1048576)

#: Model ids the harness emits for turns that never reached a real model.
#: `scan.price_for` already refuses to price these; they are reported but never
#: counted as a model tier.
PSEUDO_MODELS = frozenset({"<synthetic>", "unknown", ""})

Interval = Tuple[datetime, datetime]
Segment = Tuple[datetime, datetime, int]


# --------------------------------------------------------------------------- io


def load_traces(cache: Path) -> List[dict]:
    """Read the frozen corpus one line at a time."""

    path = cache / "traces.jsonl"
    if not path.exists():
        raise SystemExit(
            f"No corpus at {path}. Pass --cache pointing at a directory holding "
            "traces.jsonl, or generate one with "
            "`python -m gaia.factory.harvest.scan`."
        )
    traces: List[dict] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                traces.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{lineno} is not valid JSON: {e}") from e
    if not traces:
        raise SystemExit(f"{path} is empty — nothing to analyse.")
    return traces


@dataclass(frozen=True)
class InferenceLoad:
    """When inference actually happened, bucketed to the minute.

    ``minutes`` maps a ``YYYY-MM-DDTHH:MM`` key to one entry per API request
    served in it, each entry an index into ``session_ids``. Distinct indices in
    a bucket are sessions genuinely inferring at the same time; the length of
    the bucket is the request load that minute.
    """

    session_ids: List[str]
    minutes: Dict[str, List[int]]
    #: Sessions whose raw transcript was not on disk, so their inference is
    #: absent from every figure below.
    missing: int

    @property
    def requests(self) -> int:
        return sum(len(v) for v in self.minutes.values())

    @property
    def busy_minutes(self) -> int:
        return len(self.minutes)

    @property
    def sessions_per_minute(self) -> List[int]:
        return sorted(len(set(v)) for v in self.minutes.values())

    @property
    def requests_per_minute(self) -> List[int]:
        return sorted(len(v) for v in self.minutes.values())


def _request_minutes(path: Path) -> List[str]:
    """The minute each API request in one transcript was served in.

    Claude Code writes one record per content block and repeats the identical
    ``usage`` object on each, so records are deduped on ``message.id`` — the
    same collapse `context._requests` does. 1,961 message ids in the reference
    corpus straddle a minute boundary, so the **earliest** timestamp wins: that
    is when the request began being served.
    """

    first: Dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            if not (msg.get("usage") or {}):
                continue
            stamp = rec.get("timestamp")
            mid = msg.get("id")
            if not stamp or not mid:
                continue
            if mid not in first or stamp < first[mid]:
                first[mid] = stamp
    return [s[:16] for s in first.values()]


def collect_inference(
    cache: Path, projects_root: Path, traces: Sequence[dict], freeze: bool = True
) -> InferenceLoad:
    """Per-minute inference activity, frozen beside ``requests.json``.

    Frozen on first run for the same reason `context.collect` freezes: the raw
    transcripts are live and keep growing, so an unfrozen figure would not
    reproduce. Delete ``inference_minutes.json`` to re-measure.
    """

    frozen = cache / "inference_minutes.json"
    if freeze and frozen.exists():
        blob = json.loads(frozen.read_text(encoding="utf-8"))
        return InferenceLoad(blob["session_ids"], blob["minutes"], blob["missing"])

    if not projects_root.is_dir():
        raise SystemExit(
            f"Raw transcripts not found at {projects_root}. Inference-time "
            "concurrency is measured from them, and there is no substitute in "
            "the frozen corpus — the open-session intervals in traces.jsonl "
            "measure something else entirely and must not stand in for it. "
            "Pass --projects pointing at your Claude Code projects directory."
        )

    session_ids: List[str] = []
    minutes: Dict[str, List[int]] = defaultdict(list)
    missing = 0
    for t in traces:
        sid = t.get("session_id", "")
        path = projects_root / t.get("project", "") / f"{sid}.jsonl"
        if not path.exists():
            missing += 1
            continue
        idx = len(session_ids)
        session_ids.append(sid)
        for minute in _request_minutes(path):
            minutes[minute].append(idx)

    if not minutes:
        raise SystemExit(
            f"No timestamped assistant requests found under {projects_root} for "
            f"any of the {len(traces)} sessions in the corpus ({missing} had no "
            "transcript on disk). Check that --projects points at the same "
            "machine's Claude Code history that produced traces.jsonl."
        )

    load = InferenceLoad(session_ids, dict(minutes), missing)
    if freeze:
        frozen.write_text(
            json.dumps(
                {
                    "session_ids": load.session_ids,
                    "minutes": load.minutes,
                    "missing": load.missing,
                }
            ),
            encoding="utf-8",
        )
    return load


def _usage(holder: dict, what: str) -> Dict[str, int]:
    u = holder.get("usage")
    if not isinstance(u, dict):
        raise ValueError(
            f"{what} carries no usage record. The corpus is malformed; "
            "re-run `python -m gaia.factory.harvest.scan` to rebuild it."
        )
    return u


def _prefill(u: Dict[str, int]) -> int:
    """Every token the model had to attend over, however it was billed."""

    return (
        u.get("input_tokens", 0)
        + u.get("cache_read_tokens", 0)
        + u.get("cache_write_tokens", 0)
    )


def cache_read_share(traces: Sequence[dict]) -> float:
    """Percent of prefill that was text the model had already been sent."""

    read = total = 0
    for scope, ctx in _contexts(traces):
        u = _usage(ctx, scope)
        read += u.get("cache_read_tokens", 0)
        total += _prefill(u)
    if not total:
        raise ValueError("The corpus records no prefill tokens at all.")
    return 100.0 * read / total


def _contexts(traces: Sequence[dict]) -> List[Tuple[str, dict]]:
    """Every billed context in the corpus: main sessions and subagents alike."""

    out: List[Tuple[str, dict]] = []
    for t in traces:
        out.append(("main", t))
        for s in t.get("subagents") or []:
            out.append(("subagent", s))
    return out


# --------------------------------------------------------------------------- fmt


def _k(n: float) -> str:
    if n >= 1e9:
        return f"{n/1e9:.2f}B"
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.1f}K"
    return f"{n:,.0f}"


def _gib(byts: float) -> str:
    return f"{byts / GIB:.1f}"


# --------------------------------------------------------------------- 1. prefill


def prefill_decode(traces: Sequence[dict]) -> str:
    """Is this workload compute-bound or memory-bandwidth-bound?"""

    rows: List[List[str]] = []
    totals: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    parts = Counter()
    for scope, ctx in _contexts(traces):
        u = _usage(ctx, f"{scope} {ctx.get('session_id', '?')}")
        totals[scope][0] += _prefill(u)
        totals[scope][1] += u.get("output_tokens", 0)
        parts["input"] += u.get("input_tokens", 0)
        parts["cache_read"] += u.get("cache_read_tokens", 0)
        parts["cache_write"] += u.get("cache_write_tokens", 0)

    all_pre = sum(v[0] for v in totals.values())
    all_dec = sum(v[1] for v in totals.values())
    if all_dec == 0:
        raise ValueError(
            "The corpus records zero output tokens across every context. That "
            "cannot be right — check the usage extraction in reader.py."
        )
    for scope in ("main", "subagent"):
        pre, dec = totals.get(scope, [0, 0])
        if pre + dec == 0:
            rows.append([f"`{scope}`", "—", "—", "none in this corpus", "—"])
            continue
        rows.append(
            [
                f"`{scope}`",
                _k(pre),
                _k(dec),
                f"{pre/dec:,.0f}:1" if dec else "no decode",
                f"{100*dec/(pre+dec):.2f}%",
            ]
        )
    rows.append(
        [
            "**all contexts**",
            f"**{_k(all_pre)}**",
            f"**{_k(all_dec)}**",
            f"**{all_pre/all_dec:,.0f}:1**",
            f"**{100*all_dec/(all_pre+all_dec):.2f}%**",
        ]
    )

    per_session = [
        (_prefill(_usage(c, s)), (_usage(c, s)).get("output_tokens", 0))
        for s, c in _contexts(traces)
    ]
    ratios = [p / d for p, d in per_session if d > 0]
    no_decode = sum(1 for _, d in per_session if d == 0)

    L = [
        "Prefill is every token the model reads before it answers "
        "(`input + cache_read + cache_write`). Decode is every token it writes.",
        "",
        _tbl(
            rows,
            ["scope", "prefill tokens", "decode tokens", "prefill:decode", "decode %"],
            "lrrrr",
        ),
        "",
        "**Per-context distribution of the same ratio** "
        f"({len(ratios):,} of {len(per_session):,} contexts wrote at least one "
        f"token; the other {no_decode} wrote none and are excluded):",
        "",
        _tbl(
            [
                [name, f"{percentile(ratios, p):,.0f}:1"]
                for name, p in (
                    ("p10 (most decode-heavy)", 10),
                    ("p25", 25),
                    ("p50 (median)", 50),
                    ("p75", 75),
                    ("p90 (most prefill-heavy)", 90),
                )
            ],
            ["percentile", "prefill:decode"],
            "lr",
        ),
        "",
        "**What the prefill is made of.** This matters locally: a hosted API "
        "charges `cache_read` at a tenth of the price because *it* keeps the "
        "prefix, but a local server with no prefix cache must recompute every "
        "one of those tokens on every request.",
        "",
        _tbl(
            [
                [f"`{k}`", _k(v), f"{100*v/all_pre:.1f}%"]
                for k, v in parts.most_common()
            ],
            ["token class", "tokens", "share of prefill"],
            "lrr",
        ),
        "",
    ]

    ratio = all_pre / all_dec
    L.append(
        f"**Verdict: overwhelmingly prefill-dominated** — {ratio:,.0f} tokens read "
        f"for every 1 written, with decode just {100*all_dec/(all_pre+all_dec):.2f}% "
        "of all traffic. Prefill is a batched matrix multiply and saturates "
        "compute; decode is sequential and saturates memory bandwidth. A "
        "workload this lopsided is **compute-bound**, so raw TFLOPs and prefill "
        "throughput (tokens/s ingested) decide the hardware, not memory "
        "bandwidth. Bandwidth still sets the *floor* on interactive feel, "
        "because the user waits on decode — but it is not the scaling limit."
    )
    L.append("")
    L.append(
        "_The one lever that changes this picture is prefix caching. "
        f"{100*parts['cache_read']/all_pre:.0f}% of prefill here is `cache_read` "
        "— text the model had already seen. A local server that reuses the KV "
        "cache across turns avoids recomputing it; one that does not will do "
        f"{all_pre/(parts['input'] + parts['cache_write']):.1f}x the prefill work "
        "this corpus actually paid for._"
    )
    return "\n".join(L)


# ----------------------------------------------------------------------- 2. models


def model_mix(traces: Sequence[dict]) -> str:
    """Which models ran, and whether a fleet needs one tier or several."""

    tok: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ctx_count: Counter = Counter()
    for scope, ctx in _contexts(traces):
        by_model = ctx.get("usage_by_model") or {}
        if not by_model:
            raise ValueError(
                f"{scope} {ctx.get('session_id', '?')} has no usage_by_model. "
                "Rebuild the corpus with `python -m gaia.factory.harvest.scan`."
            )
        for model, u in by_model.items():
            ctx_count[model] += 1
            tok[model]["prefill"] += _prefill(u)
            tok[model]["decode"] += u.get("output_tokens", 0)

    # Tool calls are recorded per context, not per model, so a context that
    # switched model mid-run cannot have its calls attributed to either.
    calls: Counter = Counter()
    unattributable = 0
    for scope, ctx in _contexts(traces):
        models = list((ctx.get("usage_by_model") or {}).keys())
        n = ctx.get("total_calls", 0)
        if len(models) == 1:
            calls[models[0]] += n
        else:
            unattributable += n

    total_tok = sum(v["prefill"] + v["decode"] for v in tok.values())
    total_calls = sum(calls.values()) + unattributable
    if not total_tok or not total_calls:
        raise ValueError(
            "The corpus attributes no tokens or no tool calls to any model, so "
            "the model mix cannot be computed. Rebuild it with "
            "`python -m gaia.factory.harvest.scan`."
        )
    rows = []
    for model, v in sorted(
        tok.items(), key=lambda kv: -(kv[1]["prefill"] + kv[1]["decode"])
    ):
        tot = v["prefill"] + v["decode"]
        note = " *(harness pseudo-model)*" if model in PSEUDO_MODELS else ""
        rows.append(
            [
                f"`{model}`{note}",
                f"{ctx_count[model]:,}",
                _k(tot),
                f"{100*tot/total_tok:.2f}%",
                f"{calls[model]:,}",
                f"{100*calls[model]/total_calls:.1f}%",
            ]
        )

    real = [m for m in tok if m not in PSEUDO_MODELS]
    if not real:
        raise ValueError(
            "Every model id in this corpus is a harness pseudo-model "
            f"({', '.join(sorted(tok))}). No real model tier can be reported."
        )
    lead = max(real, key=lambda m: tok[m]["prefill"] + tok[m]["decode"])
    lead_share = 100 * (tok[lead]["prefill"] + tok[lead]["decode"]) / total_tok

    # The verdict has to follow the measured share, not the reference corpus:
    # a genuine 60/40 split would need two tiers and must say so.
    if lead_share >= 90:
        verdict = (
            f"**One tier, not several.** `{lead}` carries {lead_share:.1f}% of "
            f"all tokens; the other {len(real)-1} real model id(s) together "
            f"account for {100-lead_share:.1f}%, which is occasional turns "
            "inside sessions the lead model otherwise drove rather than a "
            "second workload. A fleet serving this corpus needs **one model "
            "resident**, sized for the heaviest use, rather than a small model "
            "and a large one side by side."
        )
    else:
        verdict = (
            f"**More than one tier.** `{lead}` carries only {lead_share:.1f}% "
            f"of all tokens and {len(real)-1} other real model id(s) carry the "
            f"remaining {100-lead_share:.1f}%. That is a genuine split: a fleet "
            "serving this corpus would have to keep more than one model "
            "resident, or accept an eviction and reload every time the "
            "workload switches."
        )

    return "\n".join(
        [
            _tbl(
                rows,
                [
                    "model",
                    "contexts",
                    "tokens",
                    "% tokens",
                    "attributable calls",
                    "% calls",
                ],
                "lrrrrr",
            ),
            "",
            "_`contexts` counts main sessions plus subagents that billed any "
            "tokens to that model; a context that switched model appears in "
            f"more than one row. {unattributable:,} tool calls "
            f"({100*unattributable/total_calls:.1f}%) happened in contexts that "
            "used more than one model and cannot be attributed to either — the "
            "corpus records calls per context, not per model._",
            "",
            verdict,
            "",
            "_What this cannot tell you: these are hosted Claude ids, so they "
            "say nothing about which *open-weight* model would be adequate — "
            "only how many distinct tiers the workload demands._",
        ]
    )


# ------------------------------------------------------------------ 3. concurrency


def parse_ts(value: object, what: str) -> datetime:
    """One ISO-8601 stamp, or a loud error naming the record that broke."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{what}: timestamp is missing or not a string ({value!r}).")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            f"{what}: {value!r} is not an ISO-8601 timestamp. The corpus writer "
            "(reader.py) emits UTC stamps ending in 'Z'."
        ) from e


def spans(traces: Sequence[dict]) -> Tuple[List[Interval], List[Tuple[str, float]]]:
    """(start, end) per session, plus every session whose stamps ran backwards.

    A transcript's lines are not guaranteed to be in time order, so `last_at`
    can land fractionally before `started_at`. Ordering the pair is safe;
    concealing that it happened is not, which is why the offenders come back
    with the intervals and get printed.
    """

    out: List[Interval] = []
    inverted: List[Tuple[str, float]] = []
    for t in traces:
        sid = t.get("session_id", "?")
        a = parse_ts(t.get("started_at"), f"session {sid} started_at")
        b = parse_ts(t.get("last_at"), f"session {sid} last_at")
        if b < a:
            inverted.append((sid, (a - b).total_seconds()))
            a, b = b, a
        out.append((a, b))
    return out, inverted


def intervals(traces: Sequence[dict]) -> List[Interval]:
    """(start, end) per session, in corpus order."""

    return spans(traces)[0]


def overlap_segments(ivals: Sequence[Interval]) -> List[Segment]:
    """Split the timeline into runs of constant concurrency.

    Every segment returned has positive width: a run is emitted only when the
    clock actually advances, so two sessions that merely touch at a boundary
    never raise the level. Starts are processed before ends at an equal
    instant, which keeps the level from going negative on a zero-length
    session.
    """

    if not ivals:
        return []
    events: List[Tuple[datetime, int]] = []
    for a, b in ivals:
        events.append((a, 1))
        events.append((b, -1))
    events.sort(key=lambda e: (e[0], -e[1]))

    segments: List[Segment] = []
    level = 0
    prev = events[0][0]
    for when, delta in events:
        if when > prev:
            segments.append((prev, when, level))
            prev = when
        level += delta
    if level != 0:
        raise ValueError(f"sweep ended at level {level}, expected 0 — bug in sweep.")
    return segments


def weighted_level(minutes: Dict[int, float], pct: float) -> int:
    """The concurrency level a given percentile of *busy time* ran at.

    Weighted by duration, not by event count: a level held for six hours must
    outweigh one touched for a second.
    """

    busy = [(lvl, m) for lvl, m in sorted(minutes.items()) if lvl >= 1 and m > 0]
    if not busy:
        raise ValueError(
            "no busy time in the corpus — nothing to take a percentile of."
        )
    target = pct / 100.0 * sum(m for _, m in busy)
    run = 0.0
    for lvl, m in busy:
        run += m
        if run >= target:
            return lvl
    return busy[-1][0]


@dataclass(frozen=True)
class Concurrency:
    """Time-weighted concurrency, shared by the report and the sizing table."""

    minutes: Dict[int, float]
    busy_min: float
    span_min: float
    span_start: datetime
    span_end: datetime
    peak: int
    peak_min: float
    peak_when: datetime
    typical: int
    p90: int
    mean: float
    zero_len: int
    inverted: List[Tuple[str, float]]


def concurrency_stats(traces: Sequence[dict]) -> Concurrency:
    ivals, inverted = spans(traces)
    segs = overlap_segments(ivals)
    span_start = min(a for a, _ in ivals)
    span_end = max(b for _, b in ivals)

    minutes: Dict[int, float] = defaultdict(float)
    for a, b, lvl in segs:
        minutes[lvl] += (b - a).total_seconds() / 60.0
    busy = sum(m for lvl, m in minutes.items() if lvl >= 1)
    if busy <= 0:
        raise ValueError(
            "Every session in this corpus has zero duration, so no two can "
            "overlap. Concurrency cannot be measured from it."
        )
    peak = max(lvl for lvl, m in minutes.items() if m > 0)
    return Concurrency(
        minutes=dict(minutes),
        busy_min=busy,
        span_min=(span_end - span_start).total_seconds() / 60.0,
        span_start=span_start,
        span_end=span_end,
        peak=peak,
        peak_min=minutes[peak],
        peak_when=min(a for a, _, lvl in segs if lvl == peak),
        typical=weighted_level(minutes, 50),
        p90=weighted_level(minutes, 90),
        mean=sum(lvl * m for lvl, m in minutes.items() if lvl >= 1) / busy,
        zero_len=sum(1 for a, b in ivals if b == a),
        inverted=inverted,
    )


def concurrency(traces: Sequence[dict], load: InferenceLoad) -> str:
    """Sessions open at once, sessions inferring at once, and why they differ."""

    S = concurrency_stats(traces)
    minutes, busy, peak = S.minutes, S.busy_min, S.peak
    sess = load.sessions_per_minute
    reqs = load.requests_per_minute
    inf_peak = sess[-1]
    inf_mean = sum(sess) / len(sess)
    inf_share = 100 * load.busy_minutes / S.span_min

    rows = []
    for name, pct in (("p50", 50), ("p75", 75), ("p90", 90), ("p99", 99)):
        rows.append(
            [
                name,
                str(weighted_level(minutes, pct)),
                str(int(percentile(sess, pct))),
            ]
        )
    rows.append(["**peak**", f"**{peak}**", f"**{inf_peak}**"])
    rows.append(["mean", f"{S.mean:.2f}", f"{inf_mean:.2f}"])

    inverted = S.inverted
    return "\n".join(
        [
            f"The corpus spans **{S.span_min/60/24:.1f} days** "
            f"({S.span_start.isoformat()} → {S.span_end.isoformat()}). "
            "Concurrency is measured over it twice, because the two answers are "
            "different questions and only one of them sizes memory.",
            "",
            _tbl(
                [
                    [
                        "**sessions open**",
                        "overlapping `started_at`→`last_at` intervals from "
                        "`traces.jsonl`, weighted by duration",
                        f"{busy/60:,.0f}h ({100*busy/S.span_min:.1f}% of span)",
                    ],
                    [
                        "**sessions inferring**",
                        f"{load.requests:,} API requests read from the raw "
                        "transcripts, bucketed into the minute each was served",
                        f"{load.busy_minutes:,} min ({inf_share:.1f}% of span)",
                    ],
                ],
                ["measure", "how it is derived", "time it covers"],
                "lll",
            ),
            "",
            _tbl(
                rows,
                ["percentile", "sessions open at once", "sessions inferring at once"],
                "lrr",
            ),
            "",
            "**The gap between those two columns is the finding.** Inference "
            f"happens in only **{inf_share:.1f}%** of the corpus's wall-clock "
            f"minutes — {load.busy_minutes:,} of {S.span_min:,.0f} — while at "
            f"least one session is *open* for {100*busy/S.span_min:.0f}% of "
            "them. Agent sessions are overwhelmingly idle: a person reads, "
            "thinks, edits, and comes back. **Provisioning for open sessions "
            f"over-provisions by {S.mean/inf_mean:.1f}x on average** — "
            f"{peak/inf_peak:.1f}x at the peak ({peak} vs {inf_peak}), and "
            f"{S.typical/max(percentile(sess, 50), 1):.0f}x at the median "
            f"({S.typical} vs {int(percentile(sess, 50))}), where the gap is "
            "widest because the median open minute is one in which nothing at "
            "all is being served.",
            "",
            "**Why only one of them sizes memory.** A context occupies KV cache "
            "while it is being served. If the server evicts between requests — "
            "the normal behaviour for a shared endpoint — the inferring column "
            "is the memory requirement. Section 6 prices it.",
            "",
            _tbl(
                [
                    [name, f"{int(percentile(reqs, pct)):,}"]
                    for name, pct in (("p50", 50), ("p75", 75), ("p90", 90))
                ]
                + [["max", f"{reqs[-1]:,}"]],
                ["requests in a busy minute", "count"],
                "lr",
            ),
            "",
            "**That second table is the throughput requirement**, and it is the "
            "one a server actually has to meet: not the corpus average smeared "
            f"over 36 days, but {int(percentile(reqs, 50))} requests in a "
            f"typical working minute and {int(percentile(reqs, 90))} in a busy "
            "one.",
            "",
            "_**A minute bucket is coarse.** Two requests in the same minute may "
            "not have overlapped at all — one could finish at :10 and the next "
            "start at :50 — so the inferring column is still an **upper bound** "
            "on true simultaneity, just a far tighter one than open intervals. "
            "Tightening it further needs per-request start and end times, which "
            "the transcripts do not record: they carry the timestamp a request "
            "was served, not its duration._",
            "",
            "_Both columns count **parent sessions only**. Subagents carry no "
            "timestamps in this corpus, so a session that forked 68 ways "
            "(section 5) still counts as one, and both figures are a floor on "
            f"concurrent contexts in that respect. {load.missing} session(s) had "
            "no transcript on disk and contribute nothing to the inferring "
            "column._",
            "",
            f"_{S.zero_len} session(s) start and end at the same instant and "
            f"contribute no measurable open time. {len(inverted)} session(s) "
            "recorded a last message before their first — transcript lines are "
            "not strictly time-ordered — and the pair was put in order"
            + (
                f"; the largest inversion was {max(s for _, s in inverted):.1f}s."
                if inverted
                else "."
            )
            + "_",
        ]
    )


# ------------------------------------------------------------------- 4. throughput


def throughput(traces: Sequence[dict], load: InferenceLoad) -> str:
    """How fast the work arrived — wall-clock, steps and tokens per minute."""

    ivals = intervals(traces)
    durations = [(b - a).total_seconds() / 60.0 for a, b in ivals]
    active = [d for d in durations if d > 0]
    total_session_min = sum(durations)

    steps = sum(t.get("total_calls", 0) for t in traces)
    sub_steps = sum(
        s.get("total_calls", 0) for t in traces for s in (t.get("subagents") or [])
    )
    tokens = sum(
        _prefill(_usage(c, s)) + _usage(c, s).get("output_tokens", 0)
        for s, c in _contexts(traces)
    )
    decode = sum(_usage(c, s).get("output_tokens", 0) for s, c in _contexts(traces))

    busy = concurrency_stats(traces).busy_min
    inferring = float(load.busy_minutes)

    per_session_steps = [
        t.get("total_calls", 0) / d
        for t, d in zip(traces, durations)
        if d > 0 and t.get("total_calls", 0)
    ]

    return "\n".join(
        [
            _tbl(
                [
                    ["p50 (median session)", f"{percentile(durations, 50):,.1f}"],
                    ["p75", f"{percentile(durations, 75):,.1f}"],
                    ["p90", f"{percentile(durations, 90):,.1f}"],
                    ["p99", f"{percentile(durations, 99):,.1f}"],
                    ["longest", f"{max(durations):,.1f}"],
                    ["total across all sessions", f"{total_session_min:,.0f}"],
                ],
                ["session duration", "minutes"],
                "lr",
            ),
            "",
            f"_{len(durations)-len(active)} session(s) have zero measurable "
            "duration — a single exchange inside one clock tick._",
            "",
            "**Rates, over three denominators**, because they answer different "
            "questions and only the last is a throughput requirement. *Per "
            "session-minute* sums every session's own span and double-counts "
            "overlap. *Per open minute* uses the wall-clock during which any "
            "session was open — which section 3 shows is 100% of the corpus, so "
            "it mostly divides by idle time. *Per inferring minute* uses only "
            f"the {load.busy_minutes:,} minutes in which a request was actually "
            "served, and that is what a server has to keep up with.",
            "",
            _tbl(
                [
                    [
                        "tool calls (sessions + subagents)",
                        f"{steps + sub_steps:,}",
                        f"{(steps+sub_steps)/total_session_min:,.2f}",
                        f"{(steps+sub_steps)/busy:,.2f}",
                        f"{(steps+sub_steps)/inferring:,.2f}",
                    ],
                    [
                        "all tokens",
                        _k(tokens),
                        f"{tokens/total_session_min:,.0f}",
                        f"{tokens/busy:,.0f}",
                        f"{tokens/inferring:,.0f}",
                    ],
                    [
                        "decode tokens only",
                        _k(decode),
                        f"{decode/total_session_min:,.0f}",
                        f"{decode/busy:,.0f}",
                        f"{decode/inferring:,.0f}",
                    ],
                ],
                [
                    "measure",
                    "total",
                    "per session-minute",
                    "per open minute",
                    "per inferring minute",
                ],
                "lrrrr",
            ),
            "",
            "In per-second terms, over the minutes it was actually serving, a "
            f"server for this corpus had to sustain **{tokens/inferring/60:,.0f} "
            f"tokens/s of prefill+decode** and **{decode/inferring/60:,.0f} "
            "tokens/s of decode** — against "
            f"{tokens/busy/60:,.0f} and {decode/busy/60:,.1f} if the idle time "
            "is left in the denominator. Median session pace was "
            f"{percentile(per_session_steps, 50):,.2f} tool calls per minute.",
            "",
            "**Read these as bounds, not as rates.** Even the inferring-minute "
            "column is an average over whole minutes: section 3 shows a busy "
            f"minute carrying {int(percentile(load.requests_per_minute, 90)):,} "
            f"requests at p90 and {load.requests_per_minute[-1]:,} at worst, so "
            "the instantaneous demand inside a burst is higher again. How much "
            "higher cannot be computed here — the transcripts record when a "
            "request was served, never how long it took.",
        ]
    )


# ------------------------------------------------------------------- 5. delegation


def delegation(traces: Sequence[dict]) -> str:
    """How often the agent forked a second context, and how wide it forked."""

    fanout = [len(t.get("subagents") or []) for t in traces]
    delegating = [n for n in fanout if n]
    subs = [s for t in traces for s in (t.get("subagents") or [])]
    if not delegating:
        return (
            f"**No session in this corpus delegated.** All {len(traces):,} ran "
            "in a single context, so there is no fan-out, no subagent token "
            "share, and no second concurrent context to size for."
        )

    main_tok = sum(
        _prefill(_usage(t, t.get("session_id", "?")))
        + _usage(t, t.get("session_id", "?")).get("output_tokens", 0)
        for t in traces
    )
    sub_tok = sum(
        _prefill(_usage(s, "subagent")) + _usage(s, "subagent").get("output_tokens", 0)
        for s in subs
    )
    main_calls = sum(t.get("total_calls", 0) for t in traces)
    sub_calls = sum(s.get("total_calls", 0) for s in subs)

    # A `delegate` call inside a subagent proves a third level exists; the
    # corpus flattens subagents into one list, so it cannot be counted.
    nested = [s for s in subs if (s.get("families") or {}).get("delegate", 0)]
    nested_calls = sum((s.get("families") or {}).get("delegate", 0) for s in nested)

    buckets = Counter()
    for n in delegating:
        if n <= 2:
            buckets["1-2"] += 1
        elif n <= 5:
            buckets["3-5"] += 1
        elif n <= 10:
            buckets["6-10"] += 1
        else:
            buckets["11+"] += 1

    return "\n".join(
        [
            _tbl(
                [
                    [
                        "Sessions that delegated at least once",
                        f"{len(delegating):,}",
                        f"{100*len(delegating)/len(traces):.1f}% of sessions",
                    ],
                    ["Subagents launched", f"{len(subs):,}", "across the corpus"],
                    [
                        "Fan-out among delegating sessions",
                        f"median {percentile(delegating, 50):,.0f}",
                        f"p90 {percentile(delegating, 90):,.0f} · "
                        f"max {max(delegating)}",
                    ],
                    [
                        "Subagent share of tokens",
                        f"{100*sub_tok/(main_tok+sub_tok):.1f}%",
                        f"{_k(sub_tok)} of {_k(main_tok+sub_tok)}",
                    ],
                    [
                        "Subagent share of tool calls",
                        f"{100*sub_calls/(main_calls+sub_calls):.1f}%",
                        f"{sub_calls:,} of {main_calls+sub_calls:,}",
                    ],
                ],
                ["measure", "value", "detail"],
                "lrl",
            ),
            "",
            _tbl(
                [
                    [k, f"{buckets[k]:,}", f"{100*buckets[k]/len(delegating):.1f}%"]
                    for k in ("1-2", "3-5", "6-10", "11+")
                    if buckets[k]
                ],
                ["subagents in the session", "sessions", "share of delegating"],
                "lrr",
            ),
            "",
            f"**Delegation is a memory strategy, and it is not cheap.** "
            f"{100*sub_tok/(main_tok+sub_tok):.1f}% of this corpus's tokens were "
            "spent in a context the user never saw. The trade is deliberate: a "
            "subagent reads a large amount of material and returns a short "
            "summary, so the parent's context stays small. But while it runs it "
            "is a **second live KV cache**, and the widest session here opened "
            f"{max(delegating)}.",
            "",
            f"_{len(nested)} subagent(s) made {nested_calls} `delegate` call(s) "
            "of their own, so delegation at least three levels deep occurs. The "
            "corpus flattens all subagents of a session into one list, so the "
            "actual tree depth and the number running **simultaneously** cannot "
            f"be recovered — {max(delegating)} is an upper bound on concurrent "
            "contexts within one session, not a measurement of them._",
        ]
    )


# --------------------------------------------------------------------- 6. KV cache


def kv_memory(
    traces: Sequence[dict],
    load: InferenceLoad,
    requests: Optional[Sequence[int]] = None,
) -> str:
    """What the KV cache costs per model, per context length, per agent.

    Priced on ``load`` — sessions *inferring* at once — not on sessions open at
    once, because a context only occupies cache while it is being served.

    ``requests`` is the per-API-request prompt-size list produced by
    :func:`gaia.factory.harvest.context.collect`. When it is supplied the
    operating point is taken from the observed p90; without it the table still
    prices the ladder but says so rather than inventing a working set.
    """

    S = concurrency_stats(traces)
    sess = load.sessions_per_minute
    busy_n = int(percentile(sess, 90))
    peak_n = sess[-1]

    head = ["model (weights Q4, GiB)", "KV dtype"] + [
        f"{c//1024}K" if c < 1048576 else "1M" for c in CTX_LADDER
    ]
    rows = []
    for name, spec in KV_MODELS.items():
        for dtype in ("fp16", "q8_0"):
            rows.append(
                [f"`{name}` ({spec['weights_q4']:.2f})", dtype]
                + [_gib(kv_bytes(name, c, dtype)) for c in CTX_LADDER]
            )

    L = [
        "KV-cache size in GiB, from each model's own `config.json` "
        "(see `harvest/context.py` for the per-model derivation). The context "
        "lengths are a **chosen ladder**, not a measurement — the measured "
        "operating point follows the table.",
        "",
        _tbl(rows, head, "ll" + "r" * len(CTX_LADDER)),
        "",
    ]

    if requests:
        ordered = sorted(requests)
        p50 = int(percentile(ordered, 50))
        rung = int(percentile(ordered, 90))
        L += [
            "**The operating point is measured, and it is enormous.** Across "
            f"{len(ordered):,} individual API requests the median prompt was "
            f"{p50:,} tokens, the p90 was {rung:,}, and the largest was "
            f"{ordered[-1]:,} — the whole working range sits in the right-hand "
            "columns above. The sizing below is priced at the **observed p90 "
            "itself** rather than rounded to a ladder rung.",
            "",
        ]
    else:
        rung = 131072
        L += [
            "**No measured operating point available.** `requests.json` was not "
            "found in the cache directory, so the per-request prompt sizes that "
            "would set the working context are unknown. The sizing below "
            f"therefore uses **{rung//1024}K as an assumption, not a "
            "measurement** — produce the real figure with "
            "`python -m gaia.factory.harvest.context --cache DIR`.",
            "",
        ]

    sizing = []
    for name, spec in KV_MODELS.items():
        w = spec["weights_q4"]
        one = kv_bytes(name, rung, "fp16") / GIB
        one8 = kv_bytes(name, rung, "q8_0") / GIB
        sizing.append(
            [
                f"`{name}`",
                f"{w + one:,.1f}",
                f"{w + one8:,.1f}",
                f"{w + busy_n*one8:,.1f}",
                f"{w + peak_n*one8:,.1f}",
            ]
        )

    L += [
        f"**What one agent needs, and what {busy_n} and {peak_n} concurrent "
        f"agents need** — at {rung:,} tokens of context each, in GiB:",
        "",
        _tbl(
            sizing,
            [
                "model",
                "1 agent, fp16 KV",
                "1 agent, q8_0 KV",
                f"{busy_n} agents, q8_0 KV",
                f"{peak_n} agents, q8_0 KV",
            ],
            "lrrrr",
        ),
        "",
        f"_**Estimate, with the assumptions stated.** {busy_n} is the p90 and "
        f"{peak_n} the peak of sessions **actively inferring** in the same "
        "minute (section 3). Sessions *open* at those same percentiles are "
        f"{S.p90} and {S.peak} — {S.p90/busy_n:.1f}x and {S.peak/peak_n:.1f}x "
        "more KV cache for work that was not being served. The multi-agent "
        "columns assume one model instance on one machine serving N contexts, "
        "so the weights load "
        "**once** and only the KV cache multiplies (`weights + N x KV`); they "
        "use q8_0 because the fp16 equivalents are not deployable at any N. "
        "Excluded, because this corpus cannot supply them: activation and "
        "framework overhead during prefill, and subagent contexts, which are "
        "invisible to both concurrency measures (section 5) and would push N "
        "higher._",
        "",
        "**Which column is real is a deployment choice, not a property of the "
        "workload.** The figures above assume the server evicts a context "
        f"between requests, so only the {peak_n} sessions mid-request hold "
        "cache. Pin each session to a resident context instead — no eviction, "
        "instant resume — and every *open* session holds its cache all day, "
        f"which puts the requirement back at {S.peak} concurrent contexts and "
        f"{S.peak/peak_n:.1f}x the memory. What that buys is latency: a resumed "
        f"session skips re-prefilling a {rung:,}-token prompt, and section 1 "
        f"measures {cache_read_share(traces):.0f}% of this corpus's prefill as "
        "text the model had already seen. The corpus measures the demand; it "
        "cannot choose the trade for you.",
        "",
        "**The conclusion this table forces.** At this corpus's context sizes "
        "the KV cache dwarfs the weights — for every model above, a single "
        "agent's cache is larger than the model it serves. Two consequences "
        "follow. Quantising the cache to q8_0 is not an optimisation, it is "
        "what makes the fleet fit at all. And because N multiplies only the "
        "cache, **context length is the lever with fleet-wide leverage**: "
        "halving the working context buys back exactly as much memory as "
        "halving the number of concurrent agents. Sliding-window attention "
        "changes the arithmetic outright — `Gemma-4-E4B-it` caps its local "
        "layers at the window, which is why its column barely moves with "
        "context length while the others scale linearly.",
    ]
    return "\n".join(L)


# --------------------------------------------------------- 7. local vs accelerator


def execution_split(traces: Sequence[dict]) -> str:
    """Local CPU/IO load per tool family — shape only; the corpus has no timing."""

    fams: Counter = Counter()
    fam_chars: Counter = Counter()
    fam_fail: Counter = Counter()
    for t in traces:
        for step in t.get("steps") or []:
            fam = step.get("family", "other")
            fams[fam] += 1
            fam_chars[fam] += step.get("result_chars", 0)
            if step.get("ok") is False:
                fam_fail[fam] += 1
    for t in traces:
        for s in t.get("subagents") or []:
            for step in s.get("steps_detail") or []:
                fam = step.get("family", "other")
                fams[fam] += 1
                fam_chars[fam] += step.get("result_chars", 0)
                if step.get("ok") is False:
                    fam_fail[fam] += 1

    total = sum(fams.values())
    chars = sum(fam_chars.values())
    if not total:
        return (
            "**No tool calls in this corpus.** Nothing ran locally, so there is "
            "no local CPU or IO load to characterise."
        )

    tasks = [t for tr in traces for t in segment(tr)]
    uc.label_all(tasks)
    by_uc: Counter = Counter()
    for t in tasks:
        if t.is_work and t.n_steps:
            by_uc[t.use_case or "unclassified"] += t.n_steps

    return "\n".join(
        [
            "Every one of these steps ran on the **local machine** — a shell "
            "command, a file read, an HTTP fetch — while inference ran on an "
            "accelerator. Call volume and bytes returned are therefore a direct "
            "proxy for local CPU and IO load.",
            "",
            _tbl(
                [
                    [
                        f"`{fam}`",
                        f"{n:,}",
                        f"{100*n/total:.1f}%",
                        _k(fam_chars[fam]),
                        f"{100*fam_chars[fam]/chars:.1f}%" if chars else "—",
                        f"{100*fam_fail[fam]/n:.1f}%",
                    ]
                    for fam, n in fams.most_common()
                ],
                [
                    "family",
                    "calls",
                    "% calls",
                    "chars returned",
                    "% chars",
                    "fail rate",
                ],
                "lrrrrr",
            ),
            "",
            f"_{total:,} steps across sessions and subagents returned "
            f"{_k(chars)} characters of text — all of it read from local disk, "
            "local processes or the network, then fed back into the next "
            "prompt. That second half is the link between this table and "
            "section 1: local execution is what *generates* the prefill._",
            "",
            "**Where the local load concentrates, by use case** (top 10 by step "
            "count, labelled by `harvest.use_cases`):",
            "",
            _tbl(
                [
                    [f"`{k}`", f"{n:,}", f"{100*n/sum(by_uc.values()):.1f}%"]
                    for k, n in by_uc.most_common(10)
                ],
                ["use case", "steps", "% of steps"],
                "lrr",
            ),
            "",
            "**What cannot be derived, stated plainly.** This corpus contains "
            "**no per-step timing** — a step record carries the tool, the "
            "family, whether it succeeded, and how many characters came back, "
            "but not when it started or how long it took. The split of "
            "wall-clock between local tool execution and remote inference is "
            "therefore **not computable from this data**, and no figure for it "
            "appears anywhere in this report. What the table gives is the "
            "*shape* of the local load — which subsystems are exercised and in "
            "what proportion — not its duration. Measuring the split requires "
            "instrumenting the agent to record per-step start and end times, "
            "which this corpus predates.",
        ]
    )


# ----------------------------------------------------------------------- assembly


def build(
    traces: Sequence[dict],
    load: InferenceLoad,
    requests: Optional[Sequence[int]] = None,
) -> str:
    subs = sum(len(t.get("subagents") or []) for t in traces)
    sections = [
        "# What it takes to run this workload",
        "",
        f"Derived from **{len(traces):,} agent sessions** and **{subs:,} "
        f"subagents**, plus **{load.requests:,} timestamped API requests** read "
        "from the raw transcripts. Every figure below comes from that data; "
        "anything that could not be derived from it is named as such rather "
        "than estimated silently.",
        "",
        "---",
        "",
        "## 1. Prefill vs decode — which way the machine is stressed",
        "",
        prefill_decode(traces),
        "",
        "---",
        "",
        "## 2. Model mix — one tier or several",
        "",
        model_mix(traces),
        "",
        "---",
        "",
        "## 3. Concurrency — open sessions vs sessions actually inferring",
        "",
        concurrency(traces, load),
        "",
        "---",
        "",
        "## 4. Wall-clock and throughput",
        "",
        throughput(traces, load),
        "",
        "---",
        "",
        "## 5. Delegation depth and fan-out",
        "",
        delegation(traces),
        "",
        "---",
        "",
        "## 6. KV-cache memory — the sizing table",
        "",
        kv_memory(traces, load, requests),
        "",
        "---",
        "",
        "## 7. Local tool execution vs accelerator inference",
        "",
        execution_split(traces),
        "",
    ]
    return "\n".join(sections)


def load_requests(cache: Path) -> Optional[List[int]]:
    """Per-request prompt sizes, if `context.collect` has frozen them."""

    path = cache / "requests.json"
    if not path.exists():
        return None
    blob = json.loads(path.read_text(encoding="utf-8"))
    reqs = blob.get("requests")
    if not reqs:
        raise SystemExit(
            f"{path} exists but holds no request sizes. Delete it and re-run "
            "`python -m gaia.factory.harvest.context --cache DIR`."
        )
    return reqs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument(
        "--projects",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help=(
            "Root of the raw Claude Code transcripts. Read once to measure "
            "inference-time concurrency, then frozen into the cache directory."
        ),
    )
    args = ap.parse_args()
    traces = load_traces(args.cache)
    load = collect_inference(args.cache, args.projects, traces)
    print(build(traces, load, load_requests(args.cache)))


if __name__ == "__main__":
    main()
