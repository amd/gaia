# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Gaia-agent perf gate-reader (T5 tier, report mode) — CI helper for test_gaia_agent_eval.yml.

Reads the perf bars ONLY from the committed manifest
``tests/fixtures/gaia/perf_gate_thresholds.json`` (no thresholds inlined here),
scans the collected per-category ``gaia eval agent`` scorecards for the perf
observations each scenario carries, writes ``eval-out/perf_gate_report.json``,
and exits non-zero ONLY if the gate's ``should_fail`` is true (= the manifest
has ``enforce: true`` AND a bar breached or a gated metric went unmeasured) or
no scorecard could be read at all. In report mode (``enforce: false``) it never
fails the build; eval_summary.py surfaces the verdict.

A metric the run did not produce (e.g. cache counters, per-turn LLM-call
counts — neither is in the runner's per-scenario ``performance_summary`` yet)
is recorded under ``not_measured`` and printed as ``not measured`` — NEVER
rendered as a pass. Under ``enforce: true`` an unmeasured gated metric fails:
"we could not measure this" must not soften into a green build.

Config comes from the environment so the workflow step stays shell-agnostic:
  GAIA_EVAL_SCORECARDS_DIR   dir scanned recursively for scorecard.json files
                             (default: eval-out — the workflow's collect layout
                             eval-out/<category>/scorecard.json)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# packaging/ -> python/ -> gaia/ -> agents/ -> hub/ -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
THRESHOLDS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "gaia" / "perf_gate_thresholds.json"
)

DEFAULT_SCORECARDS_DIR = Path("eval-out")
OUT_PATH = Path("eval-out") / "perf_gate_report.json"


def load_thresholds() -> dict:
    """Load the committed perf manifest; fail loudly when absent/invalid."""
    if not THRESHOLDS_PATH.exists():
        raise FileNotFoundError(
            f"Perf thresholds manifest not found: {THRESHOLDS_PATH}. "
            f"The committed manifest is the single source for the bars — "
            f"restore it (tests/fixtures/gaia/perf_gate_thresholds.json)."
        )
    data = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "enforce" not in data:
        raise ValueError(
            f"{THRESHOLDS_PATH} lacks the 'enforce' switch — not a valid perf "
            f"gate manifest."
        )
    return data


def collect_scenarios(scorecards_dir: Path) -> list[dict]:
    """Return every scenario result dict across all scorecard.json files."""
    scenarios: list[dict] = []
    for path in sorted(scorecards_dir.rglob("scorecard.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        found = data.get("scenarios")
        if not isinstance(found, list):
            raise ValueError(
                f"{path} has no 'scenarios' list — not a 'gaia eval agent' "
                f"scorecard. Remove stray JSON from the scan dir."
            )
        scenarios.extend(found)
    return scenarios


def _num(value):
    """Return value as float when it is a real number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def measure(scenarios: list[dict]) -> tuple[dict, list[str]]:
    """Compute the worst-case observation per gated metric.

    Returns ``(observed, not_measured)``: observed maps metric key -> worst
    value with the offending scenario id; not_measured lists metric keys the
    run produced no data for.
    """
    observed: dict = {}
    not_measured: list[str] = []

    def _worst(key: str, per_scenario, larger_is_worse=True):
        vals = [(v, sid) for v, sid in per_scenario if v is not None]
        if not vals:
            not_measured.append(key)
            return
        worst = max(vals) if larger_is_worse else min(vals)
        observed[key] = {"value": worst[0], "scenario": worst[1]}

    def _perf(s: dict, field: str):
        ps = s.get("performance_summary")
        return _num(ps.get(field)) if isinstance(ps, dict) else None

    ids = [s.get("scenario_id", "?") for s in scenarios]
    _worst(
        "max_elapsed_s",
        [(_num(s.get("elapsed_s")), sid) for s, sid in zip(scenarios, ids)],
    )
    _worst(
        "max_input_tokens_per_scenario",
        [(_perf(s, "total_input_tokens"), sid) for s, sid in zip(scenarios, ids)],
    )
    _worst(
        "max_output_tokens_per_scenario",
        [(_perf(s, "total_output_tokens"), sid) for s, sid in zip(scenarios, ids)],
    )

    # Tool calls per turn: the runner records turns[].agent_tools.
    tool_calls = []
    for s, sid in zip(scenarios, ids):
        per_turn = [
            len(t.get("agent_tools") or [])
            for t in s.get("turns") or []
            if isinstance(t, dict)
        ]
        tool_calls.append((float(max(per_turn)) if per_turn else None, sid))
    _worst("max_tool_calls_per_turn", tool_calls)

    # Recorded as unmeasured — never fabricated. Verified sources, so nobody
    # spends a day "just plumbing it through" and finds nothing at the far end:
    #
    #   cache_hit_ratio — the PRODUCT path has no counter to read. Lemonade
    #     exposes no prefill/cache field at all, so on the Gemma runner this is
    #     a missing SOURCE, not a missing pipe. ClaudeProvider does compute real
    #     numbers (`cache_read_input_tokens` / `cache_creation_input_tokens`,
    #     providers/claude.py) but they stop at `_last_usage` and the eval's
    #     per-turn schema carries no cache field. Measuring it therefore needs a
    #     Lemonade-side counter first, then a judge-schema field — and changing
    #     that schema is an LLM-affecting change that owes an eval run.
    #   llm_calls_per_turn — the judge reports the tools it observed, not how
    #     many times the agent called the model, and no other layer records it.
    for absent in ("min_cache_hit_ratio", "max_llm_calls_per_turn"):
        not_measured.append(absent)

    return observed, not_measured


def evaluate(thresholds: dict, observed: dict, not_measured: list[str]) -> dict:
    """Compare observations to the manifest's bars and build the gate object."""
    enforce = bool(thresholds.get("enforce"))
    breaches: list[str] = []
    for key, obs in observed.items():
        bar = _num(thresholds.get(key))
        if bar is None:
            continue
        value = obs["value"]
        lower_is_bar = key.startswith("min_")
        breached = value < bar if lower_is_bar else value > bar
        if breached:
            breaches.append(
                f"{key}: observed {value} (scenario {obs['scenario']}) vs bar {bar}"
            )

    # Only unmeasured metrics the manifest actually gates on matter here.
    gated_unmeasured = [k for k in not_measured if k in thresholds]
    passed = not breaches
    # Enforce mode: an unmeasured gated bar is a failure — "couldn't measure"
    # must never read as a pass (CLAUDE.md fail-loudly).
    should_fail = enforce and (bool(breaches) or bool(gated_unmeasured))
    return {
        "passed": passed,
        "enforce": enforce,
        "should_fail": should_fail,
        "breaches": breaches,
        "not_measured": gated_unmeasured,
        "observed": observed,
    }


def main() -> int:
    scorecards_dir = Path(
        os.environ.get("GAIA_EVAL_SCORECARDS_DIR", str(DEFAULT_SCORECARDS_DIR))
    )
    thresholds = load_thresholds()
    print(f"[GATE] perf manifest: {THRESHOLDS_PATH}")
    print(f"[GATE] enforce={thresholds.get('enforce')}")

    if not scorecards_dir.is_dir():
        print(
            f"[GATE] ERROR: scorecards dir not found: {scorecards_dir}. "
            f"Run the eval + collect step first (or set GAIA_EVAL_SCORECARDS_DIR)."
        )
        return 1
    scenarios = collect_scenarios(scorecards_dir)
    if not scenarios:
        print(
            f"[GATE] ERROR: no scorecard.json under {scorecards_dir} — the eval "
            f"measured nothing, so there is no perf evidence to report."
        )
        return 1

    observed, not_measured = measure(scenarios)
    perf_gate = evaluate(thresholds, observed, not_measured)

    print("\n================ GAIA PERF GATE REPORT ================")
    for key, obs in sorted(perf_gate["observed"].items()):
        print(f"  {key:34s}: worst {obs['value']} ({obs['scenario']})")
    for key in perf_gate["not_measured"]:
        print(f"  {key:34s}: not measured")
    print(
        f"  gate: passed={perf_gate['passed']} breaches={len(perf_gate['breaches'])} "
        f"enforce={perf_gate['enforce']} should_fail={perf_gate['should_fail']}"
    )
    for b in perf_gate["breaches"]:
        print(f"    BREACH: {b}")
    print("=======================================================\n")

    report = {
        "scorecards_dir": str(scorecards_dir),
        "scenario_count": len(scenarios),
        "perf_gate": perf_gate,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[OUT] wrote {OUT_PATH}")

    if perf_gate["should_fail"]:
        print("[GATE] enforced perf gate breach/unmeasured — failing the build.")
        return 1
    print("[GATE] report mode (or no breach) — perf gate does not block the build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
