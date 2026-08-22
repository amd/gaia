"""Judge NEEDS_JUDGE transcripts from util/tui_eval.py runs with Claude.

For each per-scenario result whose verdict is NEEDS_JUDGE, builds a judging
prompt from the scenario's own objectives / ground_truth / success_criteria and
the captured TUI transcript, asks ``claude -p`` (rides the Claude Code
subscription) for a strict JSON verdict, and writes ``<sid>.judged.json`` next
to the run result. Deterministic PASSes are left untouched — the judge only
decides what containment could not.

Usage:  python util/tui_judge.py --runs eval/results/gaia-local-tui-validation/runs [--only <sid>]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

JUDGE_TIMEOUT_S = 300


def build_prompt(scenario: dict, result: dict) -> str:
    lines = [
        "You are judging one evaluation scenario transcript for the GAIA flagship agent.",
        "Judge ONLY against the stated criteria. A correct answer phrased differently",
        "than the expected text still passes. An answer that contradicts the expected",
        "facts, ignores a correction, gets arithmetic wrong, or fabricates events fails.",
        "Turns marked 'skipped' were not executed - ignore them.",
        "",
        f"Scenario: {scenario['id']} - {scenario.get('name', '')}",
        f"Description: {scenario.get('description', '').strip()}",
        "",
        "Turns (criteria followed by what actually happened):",
    ]
    by_turn = {t.get("turn"): t for t in result["transcript"]}
    for turn in scenario.get("turns", []):
        n = turn.get("turn")
        lines.append(f"--- turn {n} ---")
        lines.append(f"objective: {turn.get('objective', '')}")
        gt = turn.get("ground_truth") or {}
        for key in ("expected_answer", "expected_behavior", "note"):
            if gt.get(key) is not None:
                lines.append(f"{key}: {gt[key]}")
        if turn.get("success_criteria"):
            lines.append(f"success_criteria: {turn['success_criteria']}")
        got = by_turn.get(n) or {}
        if "answer" in got:
            lines.append(f"AGENT ANSWERED: {got['answer']}")
        elif "skipped" in got:
            lines.append("(turn skipped)")
        elif "error" in got:
            lines.append(f"(turn errored: {got['error']})")
    lines += [
        "",
        "Answer with ONLY this JSON, no prose before or after:",
        '{"turns": [{"turn": <n>, "pass": true|false, "reason": "<short>"}],',
        ' "scenario_pass": true|false, "summary": "<one sentence>"}',
    ]
    return "\n".join(lines)


def extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"judge returned no JSON: {text[:200]!r}")
    return json.loads(match.group(0))


def judge_one(scenario: dict, result: dict, out_path: Path) -> dict:
    prompt = build_prompt(scenario, result)
    # Prompt goes over stdin — cmd.exe truncates argv at the first newline,
    # so a multi-line prompt passed as an argument reaches claude as line 1 only.
    proc = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=JUDGE_TIMEOUT_S,
        shell=True,  # claude is a .cmd shim on Windows
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {proc.stderr[:300]}")
    verdict = extract_json(proc.stdout)
    judged = {
        "scenario": scenario["id"],
        "judged_verdict": "PASS" if verdict.get("scenario_pass") else "FAIL",
        "judge": verdict,
    }
    out_path.write_text(json.dumps(judged, indent=2, ensure_ascii=False), "utf-8")
    return judged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    from gaia.eval.runner import find_scenarios

    results = []
    for path in sorted(args.runs.glob("*.json")):
        if path.name == "summary.json" or path.name.endswith(".judged.json"):
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        sid = result["scenario"]
        if args.only and sid != args.only:
            continue
        if result["verdict"] not in ("NEEDS_JUDGE", "FAIL"):
            results.append((sid, result["verdict"], "deterministic"))
            continue
        # FAIL here means a containment miss from an older runner build —
        # expected_answer is semantic ground truth, so the judge decides it.
        found = find_scenarios(scenario_id=sid)
        if not found:
            print(f"[SKIP] {sid}: scenario YAML not found", file=sys.stderr)
            continue
        scenario = found[0][1]
        print(f"[JUDGE] {sid}", flush=True)
        judged = judge_one(scenario, result, args.runs / f"{sid}.judged.json")
        summary = judged["judge"].get("summary", "")
        print(f"[{judged['judged_verdict']:<4}] {sid}: {summary}", flush=True)
        results.append((sid, judged["judged_verdict"], summary))

    print("\n== combined ==")
    for sid, verdict, note in results:
        print(f"{verdict:<12} {sid}  {note[:80]}")
    fails = sum(1 for _, v, _ in results if v == "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
