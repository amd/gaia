# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Break down what recorded Claude runs spent, and estimate two savings.

Reads ``gaia -p --output-format json --verbose`` records (the last JSON line of
each file) and prices every model call from the per-step usage the agent
records. Reports where the money went:

* ``first``          the first call of each run (its whole prompt);
* ``prefix_reads``   re-reading that first prompt on every later call;
* ``history_reads``  re-reading the conversation that grew after it;
* ``growth_writes``  writing each call's new turns to the cache;
* ``miss_rewrites``  re-writing turns that were already cached, because an
  earlier byte of the prompt changed between calls;
* ``output``.

and two estimates:

* ``stable prefix``  every miss rewrite read from cache instead (an upper
  bound: it assumes nothing else changed the prefix);
* ``readable tool results``  every tool result re-rendered the way the Claude
  provider now sends it, priced as one write plus a read on every later call.
  Token counts are a tokenizer approximation (about 0.4 tokens per character
  plus 0.75 per backslash escape, fitted against Anthropic's token counter),
  so treat it as an estimate.

Usage::

    python util/estimate_claude_cache_cost.py --prices IN,READ,WRITE,OUT runs/

``--prices`` is input, cache read, cache write, output in dollars per million
tokens; there is no default, because a wrong price is a wrong answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

COMPONENTS = (
    "first",
    "prefix_reads",
    "history_reads",
    "growth_writes",
    "miss_rewrites",
    "output",
)

#: Tokenizer approximation, see the module docstring.
_TOKENS_PER_CHAR = 0.404
_TOKENS_PER_ESCAPE = 0.75


@dataclass(frozen=True)
class Prices:
    """Dollars per million tokens."""

    uncached: float
    cache_read: float
    cache_write: float
    output: float

    @classmethod
    def parse(cls, text: str) -> "Prices":
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 4:
            raise ValueError(
                f"--prices needs four comma-separated numbers (input, cache read, "
                f"cache write, output per million tokens); got {text!r}."
            )
        try:
            values = [float(p) for p in parts]
        except ValueError as e:
            raise ValueError(
                f"--prices has a value that is not a number: {text!r}."
            ) from e
        if any(v < 0 for v in values):
            raise ValueError(f"--prices cannot be negative: {text!r}.")
        return cls(*values)


@dataclass(frozen=True)
class Call:
    uncached: int
    cache_read: int
    cache_write: int
    output: int

    @property
    def prompt(self) -> int:
        return self.uncached + self.cache_read + self.cache_write


def load_record(path: Path) -> Dict[str, Any]:
    """The run's record: the last JSON-object line of *path* with a conversation."""
    text = path.read_text(encoding="utf-8", errors="replace")
    saw_record = False
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            if isinstance(record.get("conversation"), list):
                return record
            saw_record = True
    if saw_record:
        raise ValueError(
            f"{path} has no 'conversation': record it with "
            "`gaia -p --output-format json --verbose`."
        )
    raise ValueError(
        f"{path} holds no JSON record from `gaia -p --output-format json`."
    )


def _is_stats(message: Dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "system"
        and isinstance(content, dict)
        and content.get("type") == "stats"
    )


def calls_of(record: Dict[str, Any]) -> List[Call]:
    """One entry per model call, from the per-step usage the agent records."""
    calls = []
    for message in record["conversation"]:
        if not _is_stats(message):
            continue
        stats = message["content"].get("performance_stats") or {}
        calls.append(
            Call(
                uncached=int(stats.get("uncached_input_tokens") or 0),
                cache_read=int(stats.get("cache_read_input_tokens") or 0),
                cache_write=int(stats.get("cache_creation_input_tokens") or 0),
                output=int(stats.get("completion_tokens") or 0),
            )
        )
    return calls


def decompose(calls: Sequence[Call], prices: Prices) -> Dict[str, float]:
    """Dollars per component, plus ``miss_tokens``: tokens re-written."""
    out = {name: 0.0 for name in COMPONENTS}
    out["miss_tokens"] = 0.0
    previous: Optional[int] = None
    first = 0
    for index, call in enumerate(calls):
        new_tokens = call.uncached + call.cache_write
        new_cost = (
            call.uncached * prices.uncached + call.cache_write * prices.cache_write
        )
        if index == 0:
            first = call.prompt
            out["first"] += call.cache_read * prices.cache_read + new_cost
        else:
            out["prefix_reads"] += min(first, call.cache_read) * prices.cache_read
            out["history_reads"] += max(call.cache_read - first, 0) * prices.cache_read
            growth = min(new_tokens, max(call.prompt - previous, 0))
            share = growth / new_tokens if new_tokens else 0.0
            out["growth_writes"] += new_cost * share
            out["miss_rewrites"] += new_cost * (1 - share)
            out["miss_tokens"] += new_tokens - growth
        out["output"] += call.output * prices.output
        previous = call.prompt
    return {k: (v / 1e6 if k != "miss_tokens" else v) for k, v in out.items()}


def approx_tokens(text: str) -> float:
    return len(text) * _TOKENS_PER_CHAR + text.count("\\") * _TOKENS_PER_ESCAPE


def _as_sent(content: Any) -> str:
    """A recorded tool result as the agent serialises it for the model."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return json.dumps(content, ensure_ascii=False)


def readable_savings(record: Dict[str, Any], prices: Prices) -> Dict[str, float]:
    """Tokens and dollars saved by rendering this run's tool results readably."""
    from gaia.llm.providers.claude import (  # pylint: disable=import-outside-toplevel
        _VERBATIM_TOOL_RESULTS,
        _readable_tool_text,
    )

    total_calls = len(calls_of(record))
    steps_done = 0
    tokens = dollars = 0.0
    for message in record["conversation"]:
        if _is_stats(message):
            steps_done += 1
            continue
        if (
            message.get("role") != "tool"
            or message.get("name") in _VERBATIM_TOOL_RESULTS
        ):
            continue
        sent = _as_sent(message.get("content"))
        saved = approx_tokens(sent) - approx_tokens(_readable_tool_text(sent))
        # Produced in step steps_done + 1, first sent by the call after it.
        first_sent = steps_done + 2
        if first_sent > total_calls:
            continue
        later_reads = total_calls - first_sent
        tokens += saved
        dollars += saved * (prices.cache_write + later_reads * prices.cache_read)
    return {"tokens": tokens, "dollars": dollars / 1e6}


def summarize(paths: Iterable[Path], prices: Prices) -> Dict[str, Any]:
    totals = {name: 0.0 for name in COMPONENTS}
    miss_tokens = readable_tokens = readable_dollars = 0.0
    runs = calls = 0
    for path in paths:
        record = load_record(path)
        run_calls = calls_of(record)
        if not run_calls:
            raise ValueError(
                f"{path} records no per-step usage: was it run with --verbose "
                "against a model that reports usage?"
            )
        parts = decompose(run_calls, prices)
        for name in COMPONENTS:
            totals[name] += parts[name]
        miss_tokens += parts["miss_tokens"]
        readable = readable_savings(record, prices)
        readable_tokens += readable["tokens"]
        readable_dollars += readable["dollars"]
        runs += 1
        calls += len(run_calls)
    return {
        "runs": runs,
        "calls": calls,
        "components": totals,
        "total": sum(totals.values()),
        "stable_prefix_savings": miss_tokens
        * (prices.cache_write - prices.cache_read)
        / 1e6,
        "miss_tokens": miss_tokens,
        "readable_tool_result_tokens": readable_tokens,
        "readable_tool_result_savings": readable_dollars,
    }


def _expand(paths: Sequence[str]) -> List[Path]:
    """Files as given; a directory contributes every transcript.json under it."""
    found: List[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found.extend(sorted(path.rglob("transcript.json")))
        elif path.is_file():
            found.append(path)
        else:
            raise FileNotFoundError(f"{raw} does not exist.")
    if not found:
        raise FileNotFoundError(f"No transcript.json under {', '.join(paths)}.")
    return found


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("paths", nargs="+", help="run records, or directories of them")
    parser.add_argument(
        "--prices",
        required=True,
        help="input,cache_read,cache_write,output in $ per million tokens",
    )
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    try:
        report = summarize(_expand(args.paths), Prices.parse(args.prices))
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"{report['runs']} runs, {report['calls']} model calls")
    for name, dollars in report["components"].items():
        print(f"  {name:<15} ${dollars:9.2f}")
    print(f"  {'total':<15} ${report['total']:9.2f}")
    print(
        f"stable prefix:          save up to ${report['stable_prefix_savings']:.2f} "
        f"({report['miss_tokens'] / 1e6:.2f}M tokens re-written)"
    )
    print(
        f"readable tool results:  save ~${report['readable_tool_result_savings']:.2f} "
        f"(~{report['readable_tool_result_tokens'] / 1e6:.2f}M tokens fewer)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
