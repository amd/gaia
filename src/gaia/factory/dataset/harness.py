# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Present a record to an agent under test, and score what comes back.

The one place that decides what a candidate harness may see. Keeping it here
rather than in each consumer's glue code means every agent is asked the same
question, and that no consumer accidentally hands over ``action`` — which would
make the whole exercise measure nothing.

**Withheld, always:** ``action``, ``observation``, ``outcome``,
``reference_checks``, ``episode_outcome``, ``grading_polarity``.

The trimmed view is the default. Full state averages ~8.5K tokens a record,
mostly file contents and a 12-step history the agent does not need to choose one
next action; trimming halves that with no loss of decision-relevant information.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from gaia.factory.dataset.axes import ALL_AXES
from gaia.factory.dataset.verifiers import STATIC_CHECKS, grade

#: Trimmed-view budgets. Chosen from measured token cost, not taste.
RECENT_STEPS_SHOWN = 6
ARG_CHARS = 300
HEAD_CHARS = 250
KNOWN_PATHS_SHOWN = 120
BINARIES_SHOWN = 60
FILES_SHOWN = 15


def prompt_view(record: Dict[str, Any], trim: bool = True) -> Dict[str, Any]:
    """Exactly what an agent under test is allowed to see."""
    state = record["state"]
    if not trim:
        shown = state
    else:
        shown = {
            "cwd": state["cwd"],
            "git_branch": state["git_branch"],
            "recent_steps": [
                {
                    "step_index": step["step_index"],
                    "reasoning": step["reasoning"][:ARG_CHARS],
                    "calls": [
                        {
                            "tool": call["tool"],
                            "arguments": {
                                k: str(v)[:ARG_CHARS]
                                for k, v in call["arguments"].items()
                            },
                        }
                        for call in step["calls"]
                    ],
                    "outcomes": [
                        {
                            "ok": out["ok"],
                            "error_class": out["error_class"],
                            "chars": out["chars"],
                            "head": out["head"][:HEAD_CHARS],
                        }
                        for out in step["outcomes"]
                    ],
                }
                for step in state["recent_steps"][-RECENT_STEPS_SHOWN:]
            ],
            "episode_actions": state["episode_actions"][-40:],
            "known_paths": state["known_paths"][-KNOWN_PATHS_SHOWN:],
            "observed_binaries": state["observed_binaries"][:BINARIES_SHOWN],
            "environment_binaries": state.get("environment_binaries", [])[
                :BINARIES_SHOWN
            ],
            # Sizes, not contents: the agent needs to know it holds the file, not
            # to re-read it inside the prompt.
            "files_in_context": {
                k: {"chars": v["chars"], "seen_at_step": v["seen_at_step"]}
                for k, v in list(state["files_in_context"].items())[-FILES_SHOWN:]
            },
            "prior_failures": state["prior_failures"],
        }
    return {
        "record_id": record["record_id"],
        "goal": record["goal"],
        "instruction": record["episode_instruction"],
        "depth": record["depth_index"],
        "tools_available": record["tools_available"],
        "reasoning_so_far": record["reasoning"]["visible_text"],
        "state": shown,
    }


def select_pilot(
    records: Sequence[Dict[str, Any]], n: int = 60, seed: str = "pilot"
) -> List[Dict[str, Any]]:
    """A stratified slice: every capability axis represented, deterministically.

    Round-robins over the axes so the rare ones (``delegation`` at 4%,
    ``error_recovery`` at 8%) are present rather than left to chance.
    """
    by_axis: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        for axis in rec["capability_axes"]:
            by_axis[axis].append(rec)
    for axis in by_axis:
        by_axis[axis].sort(
            key=lambda r: hashlib.sha256((seed + r["record_id"]).encode()).hexdigest()
        )
    chosen: Dict[str, Dict[str, Any]] = {}
    cursors = {axis: 0 for axis in by_axis}
    while len(chosen) < n:
        progressed = False
        for axis in ALL_AXES:
            if len(chosen) >= n:
                break
            pool = by_axis.get(axis, [])
            while cursors.get(axis, 0) < len(pool):
                rec = pool[cursors[axis]]
                cursors[axis] += 1
                if rec["record_id"] not in chosen:
                    chosen[rec["record_id"]] = rec
                    progressed = True
                    break
        if not progressed:
            break
    return list(chosen.values())


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_proposals(text: str) -> Dict[str, List[Dict[str, Any]]]:
    """Pull ``{record_id: [calls]}`` out of an agent's reply.

    Tolerant of fenced blocks and prose around the JSON, because fighting an
    agent's formatting is not the experiment. A reply that cannot be parsed
    yields no entry, and the caller counts it as a non-answer rather than as a
    wrong answer — they are different failures.
    """
    candidates = _JSON_BLOCK.findall(text) or [text]
    out: Dict[str, List[Dict[str, Any]]] = {}
    for blob in candidates:
        blob = blob.strip()
        start = blob.find("[")
        brace = blob.find("{")
        if start < 0 or (0 <= brace < start):
            start = brace
        if start < 0:
            continue
        for end in range(len(blob), start, -1):
            try:
                parsed = json.loads(blob[start:end])
            except json.JSONDecodeError:
                continue
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    continue
                rid = item.get("record_id")
                calls = item.get("calls") or item.get("action") or []
                if isinstance(calls, dict):
                    calls = [calls]
                if rid and isinstance(calls, list):
                    out[rid] = [
                        c for c in calls if isinstance(c, dict) and c.get("tool")
                    ]
            break
    return out


PROPOSAL_INSTRUCTIONS = """\
You are the agent under test. For each item below you are shown the state of a \
coding agent partway through a task: the goal, the instruction, what it has done \
recently, what it knows, and the tools it can use.

Decide the SINGLE next action you would take. You may dispatch more than one tool \
in the same step if you would genuinely run them together.

Rules:
- Choose only from `tools_available`.
- Give complete, runnable arguments — a real command, a real path.
- Do not explain. Do not use any tools yourself. Just answer.

Reply with ONE json array and nothing else:

```json
[
  {"record_id": "<id>", "calls": [{"tool": "Bash", "arguments": {"command": "..."}}]}
]
```
"""


def score(
    records: Sequence[Dict[str, Any]],
    proposals: Dict[str, List[Dict[str, Any]]],
    read_blob: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Aggregate a run. Deliberately emits no single headline number."""
    by_axis: Dict[str, Counter] = defaultdict(Counter)
    checks: Dict[str, Counter] = defaultdict(Counter)
    totals: Counter = Counter()
    per_record: List[Dict[str, Any]] = []

    for rec in records:
        rid = rec["record_id"]
        calls = proposals.get(rid)
        if not calls:
            totals["no_answer"] += 1
            per_record.append({"record_id": rid, "answered": False})
            continue
        totals["answered"] += 1
        result = grade(rec, calls, read_blob)
        polarity = rec["grading_polarity"]
        totals[f"{polarity}_n"] += 1
        if result["credited"]:
            totals[f"{polarity}_credited"] += 1
        if polarity == "match_reference":
            if result.get("tool_exact"):
                totals["tool_exact"] += 1
            if result.get("tool_same_family"):
                totals["tool_same_family"] += 1
        for axis in rec["capability_axes"]:
            by_axis[axis]["n"] += 1
            by_axis[axis]["credited"] += bool(result["credited"])
        for name in STATIC_CHECKS:
            check = result["checks"][name]
            if check["applicable"] and result["checks_informative"][name]:
                checks[name]["informative"] += 1
                checks[name]["passed"] += check["passed"] is True
        per_record.append(
            {
                "record_id": rid,
                "answered": True,
                "polarity": polarity,
                "credited": result["credited"],
                "tool_exact": result.get("tool_exact"),
                "proposed": [c.get("tool") for c in calls],
                "reference": [c["tool"] for c in rec["action"]["calls"]],
            }
        )

    def pct(num: int, den: int) -> Optional[float]:
        return round(100.0 * num / den, 1) if den else None

    return {
        "records": len(records),
        "answered": totals["answered"],
        "no_answer": totals["no_answer"],
        "match_reference": {
            "n": totals["match_reference_n"],
            "credited_pct": pct(
                totals["match_reference_credited"], totals["match_reference_n"]
            ),
            "tool_exact_pct": pct(totals["tool_exact"], totals["match_reference_n"]),
            "tool_same_family_pct": pct(
                totals["tool_same_family"], totals["match_reference_n"]
            ),
        },
        "avoid_reference": {
            "n": totals["avoid_reference_n"],
            "credited_pct": pct(
                totals["avoid_reference_credited"], totals["avoid_reference_n"]
            ),
        },
        "checks": {
            name: {
                "informative": c["informative"],
                "passed": c["passed"],
                "pass_pct": pct(c["passed"], c["informative"]),
            }
            for name, c in checks.items()
        },
        "by_axis": {
            axis: {
                "n": c["n"],
                "credited": c["credited"],
                "credited_pct": pct(c["credited"], c["n"]),
            }
            for axis, c in sorted(by_axis.items())
        },
        "per_record": per_record,
        "caveats": [
            "Agreement, not accuracy: the reference is what one strong harness did.",
            "match_reference and avoid_reference measure opposite things and are "
            "never merged.",
            "Checks are counted only where the reference itself passed them.",
            "No claim about task success is made or implied.",
        ],
    }


def load_records(dataset: Path, partition: str = "pool") -> List[Dict[str, Any]]:
    path = dataset / partition / "records.jsonl"
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def blob_reader(dataset: Path) -> Callable[[str], Optional[str]]:
    def read(digest: str) -> Optional[str]:
        path = dataset / "blobs" / f"{digest}.txt"
        return (
            path.read_text(encoding="utf-8", errors="replace")
            if path.is_file()
            else None
        )

    return read


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export prompt-only batches, or score replies."
    )
    parser.add_argument(
        "--dataset", type=Path, default=Path.home() / ".gaia/cache/factory/dataset"
    )
    parser.add_argument("--partition", default="pool", choices=("pool", "oracle"))
    parser.add_argument("--n", type=int, default=60)
    parser.add_argument("--batch", type=int, default=6)
    parser.add_argument(
        "--out", type=Path, required=True, help="Directory for batch files."
    )
    parser.add_argument("--full-state", action="store_true", help="~2x the tokens.")
    args = parser.parse_args(argv)

    records = load_records(args.dataset, args.partition)
    pilot = select_pilot(records, args.n)
    args.out.mkdir(parents=True, exist_ok=True)

    (args.out / "selected.json").write_text(
        json.dumps([r["record_id"] for r in pilot], indent=2), encoding="utf-8"
    )
    batches = [pilot[i : i + args.batch] for i in range(0, len(pilot), args.batch)]
    for i, batch in enumerate(batches):
        payload = [prompt_view(r, trim=not args.full_state) for r in batch]
        (args.out / f"batch_{i:02d}.json").write_text(
            PROPOSAL_INSTRUCTIONS + "\n\n" + json.dumps(payload, indent=1),
            encoding="utf-8",
        )
    chars = sum(
        (args.out / f"batch_{i:02d}.json").stat().st_size for i in range(len(batches))
    )
    print(
        f"wrote {len(batches)} batches covering {len(pilot)} records to {args.out}\n"
        f"~{chars // 4:,} input tokens total (excluding subagent system prompts)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
