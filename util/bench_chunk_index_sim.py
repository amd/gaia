# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Replay agent transcripts and measure how much tool-result text is re-sent.

Offline and deterministic: no model, no network. Every ``role: "tool"`` result
in a transcript's ``conversation`` goes through the agent's real
``_handle_large_tool_result`` with a remote model's budget, and each step (a
``role: "system"`` entry whose content has ``type == "stats"``) re-sends every
result that came before it -- the cost the chunk index exists to cut.

    python util/bench_chunk_index_sim.py 'runs.*/*/*/_transcript.json'

Results an earlier run had already cut to a head/tail excerpt are replayed as
that head and tail, so the "after" column understates the saving on them.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# pylint: disable=wrong-import-position
from gaia.agents.base import chunk_index  # noqa: E402
from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.base.artifacts import store_for  # noqa: E402
from gaia.llm.lemonade_client import truncation_budget  # noqa: E402

_OLD_METADATA = ("artifact", "continuation", "total_chars")


class _ReplayAgent(Agent):
    budget = truncation_budget(None)

    def _get_system_prompt(self) -> str:
        return "replay"

    def _register_tools(self) -> None:
        pass

    def _truncation_budget(self) -> tuple:
        return self.budget


def _size(value: Any) -> int:
    return (
        len(value)
        if isinstance(value, str)
        else len(json.dumps(value, ensure_ascii=False, default=str))
    )


def _unexcerpt(value: Any) -> Any:
    """An earlier run's head/tail excerpt, as the text it still holds."""
    if isinstance(value, str) and value.startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, dict) and {"head", "tail", "artifact"} <= set(parsed):
            return parsed["head"] + "\n" + parsed["tail"]
        return value
    if isinstance(value, dict):
        if {"head", "tail", "artifact"} <= set(value):
            return value["head"] + "\n" + value["tail"]
        return {k: _unexcerpt(v) for k, v in value.items() if k not in _OLD_METADATA}
    return value


def _unslice(value: Any) -> Any:
    """A file read an earlier run cut mid-JSON, as the part of the file it holds."""
    if not (
        isinstance(value, dict)
        and set(value) == {"truncated", "original_chars", "content"}
        and str(value["content"]).startswith('{"status"')
    ):
        return value
    sliced = value["content"]
    path = re.search(r'"file_path": "((?:[^"\\]|\\.)*)"', sliced)
    start = sliced.find('"content": "')
    if path is None or start < 0:
        return value
    body = sliced[start + len('"content": "') :]
    end = re.search(r'(?<!\\)(?:\\\\)*"', body)
    body = body[: end.end() - 1] if end else body
    for trim in range(7):
        try:
            text = json.loads('"' + body[: len(body) - trim] + '"')
            break
        except ValueError:
            continue
    else:
        return value
    file_path = json.loads('"' + path.group(1) + '"')
    rebuilt = {"status": "success", "file_path": file_path, "content": text}
    if file_path.endswith(".py"):
        rebuilt["file_type"] = "python"
    return rebuilt


def _is_stats(entry: Dict[str, Any]) -> bool:
    content = entry.get("content")
    return (
        entry.get("role") == "system"
        and isinstance(content, dict)
        and content.get("type") == "stats"
    )


def _classify(agent: Agent, name: str, source: Any, condensed: Any) -> str:
    if isinstance(condensed, dict) and "shown" in condensed:
        body = chunk_index.find_body(name, source, store_for(agent))
        return body.kind if body else "?"
    if isinstance(condensed, dict) and "head" in condensed:
        return "head/tail"
    return "records"


def _one_entry_read(agent: Agent, fitted: Any) -> int:
    """Size of reading the median-length index entry by number, every page."""
    meta = json.loads(fitted) if isinstance(fitted, str) else fitted
    meta = meta[-1] if isinstance(meta, list) and meta else meta
    index = meta.get("index", []) if isinstance(meta, dict) else []
    if not index:
        return 0
    entry = sorted(index, key=lambda e: e["length"])[len(index) // 2]
    store = store_for(agent)
    page = store.read(meta["artifact"], entry=entry["n"])
    size = _size(page)
    while "remaining" in page:
        page = store.read(meta["artifact"], page["next_offset"], page["remaining"])
        size += _size(page)
    return size


def replay(path: str) -> Dict[str, Any]:
    conversation = json.loads(Path(path).read_text(encoding="utf-8"))["conversation"]
    agent = _ReplayAgent(silent_mode=True, skip_lemonade=True)
    raw_total = new_total = refetch_total = 0
    raw_resent = new_resent = refetch_resent = 0
    kinds: Counter = Counter()
    condensed_count = 0
    for entry in conversation:
        if entry.get("role") == "tool":
            content = entry.get("content")
            raw = _size(content)
            source = _unslice(_unexcerpt(content))
            fitted = agent._handle_large_tool_result(
                entry.get("name", ""), source, [], entry.get("tool_args")
            )
            new = _size(fitted)
            raw_total += raw
            new_total += new
            refetch_total += new
            if fitted is not source:
                condensed_count += 1
                kinds[_classify(agent, entry.get("name", ""), source, fitted)] += raw
                refetch_total += _one_entry_read(agent, fitted)
        elif _is_stats(entry):
            raw_resent += raw_total
            new_resent += new_total
            refetch_resent += refetch_total
    return {
        "task": "/".join(Path(path).parts[-4:-1]),
        "results": sum(1 for e in conversation if e.get("role") == "tool"),
        "condensed": condensed_count,
        "raw": raw_resent,
        "index": new_resent,
        "index_refetch": refetch_resent,
        "kinds": kinds,
    }


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "   n/a"


def report(rows: Iterable[Dict[str, Any]]) -> Tuple[str, Dict[str, int]]:
    rows = list(rows)
    lines = [
        f"{'task':58} {'results':>7} {'cut':>4} {'raw re-sent':>12} {'indexed':>12} {'saved':>7} {'+1 read':>12} {'saved':>7}"
    ]
    totals = {"raw": 0, "index": 0, "index_refetch": 0}
    kinds: Counter = Counter()
    for row in rows:
        for key in totals:
            totals[key] += row[key]
        kinds.update(row["kinds"])
        lines.append(
            f"{row['task']:58} {row['results']:7} {row['condensed']:4} {row['raw']:12,} "
            f"{row['index']:12,} {_pct(row['raw'] - row['index'], row['raw']):>7} "
            f"{row['index_refetch']:12,} {_pct(row['raw'] - row['index_refetch'], row['raw']):>7}"
        )
    lines.append(
        f"{'TOTAL':58} {'':7} {'':4} {totals['raw']:12,} {totals['index']:12,} "
        f"{_pct(totals['raw'] - totals['index'], totals['raw']):>7} "
        f"{totals['index_refetch']:12,} {_pct(totals['raw'] - totals['index_refetch'], totals['raw']):>7}"
    )
    covered = sum(kinds.values())
    lines.append("")
    lines.append("Bytes of condensed results, by how they were split:")
    for kind, size in kinds.most_common():
        lines.append(f"  {kind:10} {size:10,} {_pct(size, covered)}")
    return "\n".join(lines), totals


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("patterns", nargs="+", help="glob(s) of _transcript.json files")
    parser.add_argument(
        "--budget",
        nargs=2,
        type=int,
        metavar=("THRESHOLD", "TARGET"),
        default=truncation_budget(None),
        help="chars (default: a remote model's budget %(default)s)",
    )
    args = parser.parse_args(argv)
    _ReplayAgent.budget = tuple(args.budget)
    paths = sorted({p for pattern in args.patterns for p in glob.glob(pattern)})
    if not paths:
        parser.error(f"no transcripts match {args.patterns}")
    text, _ = report(replay(p) for p in paths)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
