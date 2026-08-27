# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-request prompt size, and what it would cost in local KV-cache memory.

``scan`` aggregates tokens per *trace*.  That hides the number a local
deployment actually has to survive: how large the prompt got on the *single
largest request*, because that is what the KV cache must hold at once.

This module re-reads the raw transcripts for the sessions already in the cache
— the corpus is unchanged, only the measurement is new — and records the prompt
size of every individual API request.  Prompt size is
``input + cache_read + cache_write``: every token the model had to attend over,
regardless of how each one was billed.

Usage::

    python -m gaia.factory.harvest.context [--cache DIR] [--labels FILE]
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gaia.factory.harvest.scan import DEFAULT_OUT, percentile

# KV-cache bytes per token, from each model's shipped ``config.json``:
#   2 (K and V) * n_layers * n_kv_heads * head_dim * bytes_per_element
#
# ``flat_el`` is a constant floor rather than a per-token cost: models with
# sliding-window attention cap their local layers at the window, so those layers
# stop growing.  Applying the uniform formula to Gemma-4-E4B's 42 layers gives
# 43,008 elements/token against a real 9,216 — a 4.7x overstatement.
#
# fp16 = 2 bytes/element.  q8_0 is 1.0625 (a 32-value block carries an fp16
# scale), q4_0 is 0.5625 — the scale is real overhead, not a rounding error.
KV_MODELS: Dict[str, dict] = {
    "Qwen3-8B": {"per_token_el": 2 * 36 * 8 * 128, "flat_el": 0, "weights_q4": 4.68},
    "Qwen3-4B-Instruct-2507": {
        "per_token_el": 2 * 36 * 8 * 128,
        "flat_el": 0,
        "weights_q4": 2.33,
    },
    "Llama-3.1-8B-Instruct": {
        "per_token_el": 2 * 32 * 8 * 128,
        "flat_el": 0,
        "weights_q4": 4.58,
    },
    "Qwen3-14B": {"per_token_el": 2 * 40 * 8 * 128, "flat_el": 0, "weights_q4": 8.38},
    "Qwen3-32B": {"per_token_el": 2 * 64 * 8 * 128, "flat_el": 0, "weights_q4": 18.40},
    # Gemma-4-E4B: 42 layers, but ``num_kv_shared_layers: 18`` means only 0-23
    # allocate.  Of those, 4 are global (full length, head_dim 512 via
    # ``global_head_dim``, which sizes K and V and not just Q) and 20 are
    # sliding at a 512 window.  Layer 22 is the sliding-type *donor* for the 18
    # shared layers, so it must hold full length too — at head_dim 256.  That
    # leaves 19 window-capped layers as the flat floor.
    "Gemma-4-E4B-it": {
        "per_token_el": 2 * 4 * 2 * 512 + 2 * 1 * 2 * 256,
        "flat_el": 2 * 19 * 2 * 256 * 512,
        "weights_q4": 4.63,
    },
}

BYTES_PER_EL = {"fp16": 2.0, "q8_0": 1.0625, "q4_0": 0.5625}
GIB = 1024**3


def kv_bytes(model: str, tokens: int, dtype: str = "fp16") -> float:
    """KV-cache bytes to hold ``tokens`` of context for ``model``."""

    spec = KV_MODELS[model]
    b = BYTES_PER_EL[dtype]
    return (spec["per_token_el"] * tokens + spec["flat_el"]) * b


def _requests(path: Path) -> List[int]:
    """Prompt size of every API request in one raw transcript.

    Claude Code writes one record per content block and repeats the identical
    ``usage`` object on each.  Keying on ``message.id`` collapses them back to
    one entry per actual request; summing records instead double-counts.
    """

    seen: Dict[str, int] = {}
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
            usage = msg.get("usage") or {}
            if not usage:
                continue
            size = (
                usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
            )
            mid = msg.get("id") or rec.get("uuid") or ""
            seen[mid] = max(seen.get(mid, 0), size)
    return [v for v in seen.values() if v > 0]


def collect(
    cache: Path, projects_root: Path, freeze: bool = True
) -> Tuple[List[dict], List[int]]:
    """Per-session request sizes, plus the flat list across the whole corpus.

    The result is frozen into ``requests.json`` on first run and re-read
    thereafter.  Without that, every figure derived here drifts between runs:
    the raw transcripts are live and grow while the analysis is running, so a
    published median would not reproduce.  Delete the file to re-measure.
    """

    frozen = cache / "requests.json"
    if freeze and frozen.exists():
        blob = json.loads(frozen.read_text(encoding="utf-8"))
        return blob["sessions"], blob["requests"]

    sessions: List[dict] = []
    everything: List[int] = []
    with (cache / "traces.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            t = json.loads(line)
            root = projects_root / t["project"]
            main = root / f"{t['session_id']}.jsonl"
            # The main transcript's series must stay separate from its
            # subagents'.  Concatenated, every subagent start reads as a huge
            # drop in prompt size, and anything looking for context resets sees
            # a boundary that is not one.
            main_series = _requests(main) if main.exists() else []
            reqs = list(main_series)
            subdir = root / t["session_id"] / "subagents"
            if subdir.is_dir():
                for sub in sorted(subdir.glob("*.jsonl")):
                    reqs.extend(_requests(sub))
            if not reqs:
                continue
            sessions.append(
                {
                    "session_id": t["session_id"],
                    "project": t["project"],
                    "requests": len(reqs),
                    "peak": max(reqs),
                    "p50": int(percentile(reqs, 50)),
                    "mean": int(sum(reqs) / len(reqs)),
                    # Ordered main-transcript series, so ``savings`` can see
                    # context resets. Subagent requests are excluded on
                    # purpose — see above.
                    "series": main_series,
                }
            )
            everything.extend(reqs)
    if freeze:
        frozen.write_text(
            json.dumps({"sessions": sessions, "requests": everything}),
            encoding="utf-8",
        )
    return sessions, everything


def load_labels(path: Optional[Path]) -> Dict[str, str]:
    if not path or not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def _fmt_k(n: float) -> str:
    return f"{n / 1000:.1f}K"


def distribution_table(reqs: List[int]) -> str:
    reqs = sorted(reqs)
    n = len(reqs)
    rows = [
        ("p50 (median request)", percentile(reqs, 50)),
        ("p75", percentile(reqs, 75)),
        ("p90", percentile(reqs, 90)),
        ("p99", percentile(reqs, 99)),
        ("max (largest single request)", reqs[-1]),
    ]
    out = [
        f"_{n:,} individual API requests across the corpus._",
        "",
        "| Percentile | Prompt tokens | vs a 32K local window | vs 64K |",
        "|---|---:|---:|---:|",
    ]
    for name, v in rows:
        out.append(f"| {name} | {int(v):,} | {v / 32768:.2f}x | {v / 65536:.2f}x |")
    over32 = 100 * sum(1 for r in reqs if r > 32768) / n
    over64 = 100 * sum(1 for r in reqs if r > 65536) / n
    over128 = 100 * sum(1 for r in reqs if r > 131072) / n
    out += [
        "",
        f"_{over32:.1f}% of requests exceed 32K, {over64:.1f}% exceed 64K, "
        f"{over128:.1f}% exceed 128K._",
    ]
    return "\n".join(out)


def kv_table(reqs: List[int]) -> str:
    """What the observed prompt sizes cost in KV-cache memory, per model."""

    p50 = int(percentile(sorted(reqs), 50))
    p90 = int(percentile(sorted(reqs), 90))
    peak = max(reqs)
    cols = [("median", p50), ("p90", p90), ("peak", peak)]
    out = [
        "| Model | KV/token (fp16) | "
        + " | ".join(f"{n} ({_fmt_k(v)})" for n, v in cols)
        + " | Weights Q4_K_M |",
        "|---|---:|" + "---:|" * len(cols) + "---:|",
    ]
    for name, spec in KV_MODELS.items():
        per_tok = spec["per_token_el"] * 2
        cells = [f"{kv_bytes(name, v) / GIB:.1f} GB" for _, v in cols]
        flat = " + flat" if spec["flat_el"] else ""
        out.append(
            f"| {name} | {per_tok / 1024:.0f} KiB{flat} | "
            + " | ".join(cells)
            + f" | {spec['weights_q4']:.2f} GB |"
        )
    return "\n".join(out)


def by_usecase(sessions: List[dict], labels: Dict[str, str]) -> str:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for s in sessions:
        key = labels.get(s["session_id"][:8])
        if key:
            groups[key].append(s)
    out = [
        "| Use-case | Sessions | Median request | Median peak | Largest peak |",
        "|---|---:|---:|---:|---:|",
    ]
    rows = sorted(
        groups.items(), key=lambda kv: -percentile([x["peak"] for x in kv[1]], 50)
    )
    for name, ss in rows:
        peaks = [x["peak"] for x in ss]
        meds = [x["p50"] for x in ss]
        out.append(
            f"| {name} | {len(ss)} | {int(percentile(meds, 50)):,} | "
            f"{int(percentile(peaks, 50)):,} | {max(peaks):,} |"
        )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--projects",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Root of the raw Claude Code transcripts.",
    )
    ap.add_argument("--labels", type=Path, default=None)
    args = ap.parse_args()

    sessions, reqs = collect(args.cache, args.projects)
    if not reqs:
        raise SystemExit(
            f"No requests found. Checked {args.projects} for the sessions in "
            f"{args.cache / 'traces.jsonl'}. Pass --projects if the transcripts "
            "live elsewhere."
        )

    print("## Prompt size per request\n")
    print(distribution_table(reqs))
    print("\n## KV-cache memory these prompts require locally\n")
    print(kv_table(reqs))
    labels = load_labels(args.labels)
    if labels:
        print("\n## Peak context by use-case\n")
        print(by_usecase(sessions, labels))


if __name__ == "__main__":
    main()
