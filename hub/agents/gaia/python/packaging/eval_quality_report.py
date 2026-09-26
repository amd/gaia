# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Gaia-agent quality gate-reader (report mode) — CI helper for test_gaia_agent_eval.yml.

Reads the quality bars ONLY from the committed manifest
``tests/fixtures/gaia/quality_gate_thresholds.json`` (no thresholds inlined
here), aggregates ``judged_pass_rate`` / ``avg_score`` across the collected
per-category ``gaia eval agent`` scorecards, writes
``eval-out/quality_gate_report.json``, and exits non-zero ONLY if the gate's
``should_fail`` is true (= the manifest has ``enforce: true`` AND a bar
breached) or nothing judged could be read at all. In report mode
(``enforce: false``) a breached bar never fails the build; eval_summary.py
surfaces the verdict as a ``::warning::``.

Zero judged scenarios always exits 1 regardless of ``enforce`` — report mode
softens "we measured and it got worse", never "we did not measure".

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
    _REPO_ROOT / "tests" / "fixtures" / "gaia" / "quality_gate_thresholds.json"
)

DEFAULT_SCORECARDS_DIR = Path("eval-out")
OUT_PATH = Path("eval-out") / "quality_gate_report.json"

# Judged = the eval agent produced a verdict; mirrors
# gaia.eval.scorecard._JUDGED_STATUSES (pinned by test_gaia_scorecard_adapter).
_JUDGED_KEYS = ("passed", "failed", "blocked")


def load_thresholds() -> dict:
    """Load the committed quality manifest; fail loudly when absent/invalid."""
    if not THRESHOLDS_PATH.exists():
        raise FileNotFoundError(
            f"Quality thresholds manifest not found: {THRESHOLDS_PATH}. "
            f"The committed manifest is the single source for the bars — "
            f"restore it (tests/fixtures/gaia/quality_gate_thresholds.json)."
        )
    data = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "enforce" not in data:
        raise ValueError(
            f"{THRESHOLDS_PATH} lacks the 'enforce' switch — not a valid "
            f"quality gate manifest."
        )
    return data


def collect_summaries(scorecards_dir: Path) -> list[dict]:
    """Return every scorecard's summary block under ``scorecards_dir``."""
    summaries: list[dict] = []
    for path in sorted(scorecards_dir.rglob("scorecard.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        summary = data.get("summary")
        if not isinstance(summary, dict):
            raise ValueError(
                f"{path} has no 'summary' block — not a 'gaia eval agent' "
                f"scorecard. Remove stray JSON from the scan dir."
            )
        summaries.append(summary)
    return summaries


def aggregate(summaries: list[dict]) -> dict:
    """Pool judged counts + score mass across the per-category scorecards."""
    passed = judged = 0
    score_mass = 0.0
    score_count = 0
    for s in summaries:
        s_judged = sum(int(s.get(k, 0) or 0) for k in _JUDGED_KEYS)
        passed += int(s.get("passed", 0) or 0)
        judged += s_judged
        # avg_score is per-card over its judged scenarios; re-weight by count
        # so the pooled mean is the true across-category mean.
        avg = s.get("avg_score")
        if isinstance(avg, (int, float)) and not isinstance(avg, bool) and s_judged:
            score_mass += float(avg) * s_judged
            score_count += s_judged
    return {
        "judged": judged,
        "passed": passed,
        "judged_pass_rate": (passed / judged) if judged else None,
        "avg_score": (score_mass / score_count) if score_count else None,
    }


def evaluate(thresholds: dict, pooled: dict) -> dict:
    """Compare the pooled rates to the manifest's bars."""
    enforce = bool(thresholds.get("enforce"))
    breaches: list[str] = []
    min_rate = thresholds.get("min_judged_pass_rate")
    if isinstance(min_rate, (int, float)) and pooled["judged_pass_rate"] < min_rate:
        breaches.append(
            f"judged_pass_rate {pooled['judged_pass_rate']:.4f} < bar {min_rate}"
        )
    min_score = thresholds.get("min_avg_score")
    if isinstance(min_score, (int, float)) and pooled["avg_score"] < min_score:
        breaches.append(f"avg_score {pooled['avg_score']:.2f} < bar {min_score}")
    passed = not breaches
    return {
        "passed": passed,
        "enforce": enforce,
        "should_fail": enforce and not passed,
        "breaches": breaches,
        "measured": pooled,
    }


def main() -> int:
    scorecards_dir = Path(
        os.environ.get("GAIA_EVAL_SCORECARDS_DIR", str(DEFAULT_SCORECARDS_DIR))
    )
    thresholds = load_thresholds()
    print(f"[GATE] quality manifest: {THRESHOLDS_PATH}")
    print(
        f"[GATE] enforce={thresholds.get('enforce')} "
        f"min_judged_pass_rate={thresholds.get('min_judged_pass_rate')} "
        f"min_avg_score={thresholds.get('min_avg_score')}"
    )

    if not scorecards_dir.is_dir():
        print(
            f"[GATE] ERROR: scorecards dir not found: {scorecards_dir}. "
            f"Run the eval + collect step first (or set GAIA_EVAL_SCORECARDS_DIR)."
        )
        return 1
    summaries = collect_summaries(scorecards_dir)
    pooled = aggregate(summaries)
    if not pooled["judged"]:
        # Integrity, not a bar: an unjudged run proves nothing and must be red
        # on every trigger, enforce or not (CLAUDE.md fail-loudly).
        print(
            f"[GATE] ERROR: zero judged scenarios across {len(summaries)} "
            f"scorecard(s) under {scorecards_dir} — the eval measured nothing."
        )
        return 1

    quality_gate = evaluate(thresholds, pooled)

    print("\n=============== GAIA QUALITY GATE REPORT ===============")
    print(
        f"  judged={pooled['judged']} passed={pooled['passed']} "
        f"judged_pass_rate={pooled['judged_pass_rate']:.4f} "
        f"avg_score={pooled['avg_score']:.2f}"
    )
    print(
        f"  gate: passed={quality_gate['passed']} "
        f"breaches={len(quality_gate['breaches'])} "
        f"enforce={quality_gate['enforce']} "
        f"should_fail={quality_gate['should_fail']}"
    )
    for b in quality_gate["breaches"]:
        print(f"    BREACH: {b}")
    print("========================================================\n")

    report = {
        "scorecards_dir": str(scorecards_dir),
        "scorecard_count": len(summaries),
        "quality_gate": quality_gate,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[OUT] wrote {OUT_PATH}")

    if quality_gate["should_fail"]:
        print("[GATE] enforced quality gate breach — failing the build.")
        return 1
    print("[GATE] report mode (or no breach) — quality gate does not block the build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
