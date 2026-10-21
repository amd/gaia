# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Re-judge saved scenario transcripts to check a prompt change keeps verdicts.

A change to where the driver's fixed instructions sit (user message vs system
prompt) can save tokens and still move the judge's verdicts, and a moved
verdict invalidates every comparison against the committed baseline. This
re-scores the same recorded conversations under each prompt layout, with the
same model, and reports how often the verdicts agree, against the agreement
of the old layout with itself (the judge's own noise).

Layouts:

* ``legacy`` — everything in the user message, in the order the runner used
  before the fixed prefix moved: intro, rubric, scenario, manifest, task.
* ``cached`` — what the runner sends now: the intro, rubric, manifest and task
  as an appended system prompt (with the CLI's per-machine sections moved out
  of it), and only the scenario and its transcript in the user message.

Only the scenario-level verdict and turn scores are compared; the conversation
is fixed, so nothing here measures the simulator half of the driver.

Usage::

    python -m gaia.eval.rejudge --artifacts DIR --out OUT \\
        --model claude-sonnet-4-6 --sample 40 --arms legacy,cached,legacy

``DIR`` is a downloaded flagship run (any tree of ``scorecard.json`` files).
Its contents are read as data only. Each judged call is appended to
``OUT/calls.jsonl`` as it finishes, so a re-run skips what is done.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gaia.eval.claude_outage import classify_driver_failure
from gaia.eval.claude_usage import add_usage, empty_usage, usage_from_cli
from gaia.eval.runner import (
    DRIVER_INTRO,
    REPO_ROOT,
    _load_merged_manifest,
    apply_rubric,
    find_scenarios,
    manifest_section,
    rubric_sections,
    scenario_section,
)

LAYOUTS = ("legacy", "cached")
_JUDGED = ("PASS", "FAIL")
_DIMENSIONS = (
    "correctness",
    "tool_selection",
    "context_retention",
    "completeness",
    "efficiency",
    "personality",
    "error_recovery",
)

JUDGE_TASK = """## YOUR TASK
The conversation for this scenario has already happened and is recorded under
## RECORDED CONVERSATION. Do not simulate anything and do not call any tool.

1. Judge each recorded turn's agent response, with the tools it used, using the
   PER-TURN JUDGE INSTRUCTIONS section above.
2. Judge the scenario holistically using the SCENARIO-LEVEL JUDGE INSTRUCTIONS
   section above.
3. Return a single JSON object with this structure:
{
  "scenario_id": "...",
  "status": "PASS|FAIL|BLOCKED_BY_ARCHITECTURE",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "scores": {"correctness": 0-10, "tool_selection": 0-10, "context_retention": 0-10,
                 "completeness": 0-10, "efficiency": 0-10, "personality": 0-10, "error_recovery": 0-10},
      "overall_score": 0-10,
      "pass": true,
      "failure_category": null,
      "reasoning": "..."
    }
  ],
  "root_cause": null
}
"""

RECORDED_IN_USER_MESSAGE = (
    "The scenario and its recorded conversation are in the user message."
)

_RESULT_SCHEMA = json.dumps(
    {
        "type": "object",
        "required": ["scenario_id", "status", "overall_score", "turns"],
        "properties": {
            "scenario_id": {"type": "string"},
            "status": {"type": "string"},
            "overall_score": {"type": ["number", "null"]},
            "turns": {"type": "array"},
            "root_cause": {},
        },
    }
)


def transcript_section(result: dict) -> str:
    """The recorded turns, without any of the original judge's scores."""
    lines = ["## RECORDED CONVERSATION"]
    for turn in result.get("turns") or []:
        tools = ", ".join(str(t) for t in turn.get("agent_tools") or []) or "none"
        lines += [
            f"### Turn {turn.get('turn', '?')}",
            "User:",
            str(turn.get("user_message") or ""),
            f"Agent tools used: {tools}",
            "Agent response:",
            str(turn.get("agent_response") or ""),
            "",
        ]
    return "\n".join(lines) + "\n"


def build_judge_prompt(layout: str, scenario_data: dict, recorded: dict, manifest):
    """``(system_prefix or None, user_message)`` for one re-judge call."""
    rubric = rubric_sections()
    scenario = scenario_section(scenario_data)
    manifest_text = manifest_section(manifest)
    transcript = transcript_section(recorded)
    if layout == "legacy":
        user = (
            f"{DRIVER_INTRO}\n\n{rubric}\n{scenario}\n{manifest_text}\n"
            f"{JUDGE_TASK}\n{transcript}"
        )
        return None, user
    if layout == "cached":
        system = (
            f"{DRIVER_INTRO}\n{RECORDED_IN_USER_MESSAGE}\n\n{rubric}\n"
            f"{manifest_text}\n{JUDGE_TASK}"
        )
        return system, f"{scenario}\n{transcript}"
    raise ValueError(f"unknown layout {layout!r}; use one of {LAYOUTS}")


def _judged_results(artifacts: Path) -> dict:
    """Every judged scenario in a downloaded run, keyed by scenario id."""
    found = {}
    for path in sorted(artifacts.rglob("scorecard.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"unreadable scorecard {path}: {e}") from e
        for result in card.get("scenarios") or []:
            if result.get("status") in _JUDGED and result.get("turns"):
                found[result["scenario_id"]] = result
    return found


def pick_sample(results: dict, size: int, seed: int) -> list:
    """Up to *size* ids, round-robin across categories so each is represented."""
    by_category = defaultdict(list)
    for sid, result in sorted(results.items()):
        by_category[result.get("category", "unknown")].append(sid)
    rng = random.Random(seed)
    for ids in by_category.values():
        rng.shuffle(ids)
    picked = []
    queues = [by_category[c] for c in sorted(by_category)]
    while len(picked) < size and any(queues):
        for queue in queues:
            if queue and len(picked) < size:
                picked.append(queue.pop())
    return picked


def _command(model: str, system_file) -> list:
    claude = shutil.which("claude") or "claude"
    cmd = [claude, "-p"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        cmd.append("--bare")
    cmd += [
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        _RESULT_SCHEMA,
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps({"mcpServers": {}}),
        "--tools",
        "",
        "--model",
        model,
    ]
    if system_file is not None:
        cmd += [
            "--append-system-prompt-file",
            str(system_file),
            "--exclude-dynamic-system-prompt-sections",
        ]
    return cmd


def judge_once(layout, scenario_data, recorded, manifest, model, workdir, timeout):
    """Run one judge call; returns a record for ``calls.jsonl``."""
    system, user = build_judge_prompt(layout, scenario_data, recorded, manifest)
    system_file = None
    if system is not None:
        system_file = Path(workdir) / f"system-{layout}.md"
        if not system_file.exists():
            system_file.write_text(system, encoding="utf-8")
    start = time.time()
    proc = subprocess.run(
        _command(model, system_file),
        input=user,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(REPO_ROOT),
        check=False,
    )
    try:
        raw = json.loads(proc.stdout or "")
    except ValueError:
        raw = None
    record = {
        "scenario_id": scenario_data["id"],
        "category": recorded.get("category"),
        "layout": layout,
        "elapsed_s": round(time.time() - start, 1),
        "usage": usage_from_cli(raw),
    }
    outage = classify_driver_failure(raw, proc.returncode, proc.stdout, proc.stderr)
    if outage is not None:
        record["outage"] = outage.as_dict()
        return record
    verdict = raw.get("structured_output") if isinstance(raw, dict) else None
    if not isinstance(verdict, dict) or proc.returncode != 0:
        record["error"] = (proc.stderr or proc.stdout or "")[:500]
        return record
    apply_rubric(verdict, scenario_data["id"])
    record["status"] = verdict.get("status")
    record["overall_score"] = verdict.get("overall_score")
    record["turn_scores"] = [
        {d: (t.get("scores") or {}).get(d) for d in _DIMENSIONS}
        for t in verdict.get("turns") or []
    ]
    return record


def _pair(calls_a: dict, calls_b: dict) -> dict:
    """Agreement between two arms over the scenarios both judged."""
    common = sorted(
        sid
        for sid in set(calls_a) & set(calls_b)
        if calls_a[sid].get("status") in _JUDGED
        and calls_b[sid].get("status") in _JUDGED
    )
    flips = [
        (sid, calls_a[sid]["status"], calls_b[sid]["status"])
        for sid in common
        if calls_a[sid]["status"] != calls_b[sid]["status"]
    ]
    deltas = [
        calls_b[sid]["overall_score"] - calls_a[sid]["overall_score"]
        for sid in common
        if isinstance(calls_a[sid].get("overall_score"), (int, float))
        and isinstance(calls_b[sid].get("overall_score"), (int, float))
    ]
    return {
        "scenarios": len(common),
        "agree": len(common) - len(flips),
        "flips": flips,
        "pass_to_fail": sum(1 for f in flips if f[1] == "PASS"),
        "fail_to_pass": sum(1 for f in flips if f[1] == "FAIL"),
        "mean_score_delta": round(sum(deltas) / len(deltas), 3) if deltas else None,
        "mean_abs_score_delta": (
            round(sum(abs(d) for d in deltas) / len(deltas), 3) if deltas else None
        ),
    }


def summarize(calls: list, arms: list) -> dict:
    """Per-arm pass rate and tokens, and the agreement of every arm pair."""
    by_arm = {}
    for index, layout in enumerate(arms):
        arm = f"{index + 1}:{layout}"
        rows = {c["scenario_id"]: c for c in calls if c.get("arm") == arm}
        usage = empty_usage()
        for row in rows.values():
            add_usage(usage, row.get("usage"))
        judged = [r for r in rows.values() if r.get("status") in _JUDGED]
        by_arm[arm] = {
            "rows": rows,
            "judged": len(judged),
            "passed": sum(1 for r in judged if r["status"] == "PASS"),
            "errors": sum(
                1 for r in rows.values() if r.get("error") or r.get("outage")
            ),
            "usage": usage,
        }
    names = list(by_arm)
    pairs = {
        f"{a} vs {b}": _pair(by_arm[a]["rows"], by_arm[b]["rows"])
        for i, a in enumerate(names)
        for b in names[i + 1 :]
    }
    for arm in by_arm.values():
        arm.pop("rows")
    return {"arms": by_arm, "pairs": pairs}


def _load_calls(path: Path) -> list:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gaia.eval.rejudge", description=__doc__
    )
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--arms",
        default="legacy,cached,legacy",
        help="Comma-separated layouts, run in this order; repeat one to measure noise.",
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = sorted(set(arms) - set(LAYOUTS))
    if unknown:
        parser.error(f"unknown layout(s) {unknown}; use {LAYOUTS}")

    recorded = _judged_results(args.artifacts)
    sample = pick_sample(recorded, args.sample, args.seed)
    scenarios = {d["id"]: d for _p, d in find_scenarios(scenario_id=sample)}
    sample = [sid for sid in sample if sid in scenarios]
    manifest = _load_merged_manifest()

    args.out.mkdir(parents=True, exist_ok=True)
    calls_path = args.out / "calls.jsonl"
    calls = _load_calls(calls_path)
    done = {(c["arm"], c["scenario_id"]) for c in calls if "status" in c}
    print(f"[REJUDGE] {len(sample)} scenarios x {len(arms)} arms; {len(done)} done")

    with tempfile.TemporaryDirectory(prefix="gaia-rejudge-") as workdir:
        for index, layout in enumerate(arms):
            arm = f"{index + 1}:{layout}"
            todo = [sid for sid in sample if (arm, sid) not in done]

            def run(sid, layout=layout):
                return judge_once(
                    layout,
                    scenarios[sid],
                    recorded[sid],
                    manifest,
                    args.model,
                    workdir,
                    args.timeout,
                )

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for record in pool.map(run, todo):
                    record["arm"] = arm
                    calls.append(record)
                    with calls_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(record) + "\n")
                    print(
                        f"[{arm}] {record['scenario_id']}: "
                        f"{record.get('status') or record.get('error', '')[:80] or record.get('outage', {}).get('kind')}"
                    )
                    if record.get("outage"):
                        print(
                            f"[REJUDGE] Stopped: {record['outage']['kind']} "
                            f"({record['outage']['detail']}). Re-run to continue.",
                            file=sys.stderr,
                        )
                        pool.shutdown(wait=True, cancel_futures=True)
                        return 3

    report = summarize(calls, arms)
    report["sample"] = sample
    report["model"] = args.model
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "sample"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
