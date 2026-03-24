# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Scorecard generator — builds scorecard.json + summary.md from scenario results.
"""

from datetime import datetime, timezone

# Statuses where the scenario was actually judged by the eval agent.
# Infrastructure failures (TIMEOUT, BUDGET_EXCEEDED, ERRORED) are excluded
# from avg_score to avoid diluting quality metrics with infra noise.
_JUDGED_STATUSES = {"PASS", "FAIL", "BLOCKED_BY_ARCHITECTURE"}


def build_scorecard(run_id, results, config):
    """Build scorecard dict from list of scenario result dicts."""
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "PASS")
    failed = sum(1 for r in results if r.get("status") == "FAIL")
    blocked = sum(1 for r in results if r.get("status") == "BLOCKED_BY_ARCHITECTURE")
    timeout = sum(1 for r in results if r.get("status") == "TIMEOUT")
    budget_exceeded = sum(1 for r in results if r.get("status") == "BUDGET_EXCEEDED")
    infra_error = sum(
        1 for r in results if r.get("status") in ("INFRA_ERROR", "SETUP_ERROR")
    )
    # SKIPPED_NO_DOCUMENT: corpus file absent from disk (e.g. real-world docs not committed)
    skipped = sum(1 for r in results if r.get("status") == "SKIPPED_NO_DOCUMENT")
    errored = sum(
        1
        for r in results
        if r.get("status")
        not in (
            "PASS",
            "FAIL",
            "BLOCKED_BY_ARCHITECTURE",
            "TIMEOUT",
            "BUDGET_EXCEEDED",
            "INFRA_ERROR",
            "SETUP_ERROR",
            "SKIPPED_NO_DOCUMENT",
        )
    )

    # avg_score only counts judged scenarios (not infra failures with score=0).
    # FAIL scores are capped at 5.99 for averaging — a score ≥ 6.0 implies PASS by
    # rubric definition, so letting FAIL scenarios inflate avg_score is misleading.
    # The original score is preserved in each result dict (not mutated here).
    scores = [
        (
            min(r["overall_score"], 5.99)
            if r.get("status") == "FAIL"
            else r["overall_score"]
        )
        for r in results
        if r.get("status") in _JUDGED_STATUSES
        and isinstance(r.get("overall_score"), (int, float))
    ]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    # By category — mirrors the same judged-only filter for avg_score
    by_category = {}
    for r in results:
        cat = r.get("category", "unknown")
        if cat not in by_category:
            by_category[cat] = {
                "passed": 0,
                "failed": 0,
                "blocked": 0,
                "timeout": 0,
                "budget_exceeded": 0,
                "infra_error": 0,
                "skipped": 0,
                "errored": 0,
                "scores": [],
            }
        status = r.get("status", "ERRORED")
        if status == "PASS":
            by_category[cat]["passed"] += 1
        elif status == "FAIL":
            by_category[cat]["failed"] += 1
        elif status == "BLOCKED_BY_ARCHITECTURE":
            by_category[cat]["blocked"] += 1
        elif status == "TIMEOUT":
            by_category[cat]["timeout"] += 1
        elif status == "BUDGET_EXCEEDED":
            by_category[cat]["budget_exceeded"] += 1
        elif status in ("INFRA_ERROR", "SETUP_ERROR"):
            by_category[cat]["infra_error"] += 1
        elif status == "SKIPPED_NO_DOCUMENT":
            by_category[cat]["skipped"] += 1
        else:
            by_category[cat]["errored"] += 1
        # Only accumulate scores for judged scenarios; cap FAIL scores at 5.99
        if status in _JUDGED_STATUSES and isinstance(
            r.get("overall_score"), (int, float)
        ):
            sc = r["overall_score"]
            by_category[cat]["scores"].append(min(sc, 5.99) if status == "FAIL" else sc)

    for cat in by_category:
        cat_scores = by_category[cat].pop("scores", [])
        by_category[cat]["avg_score"] = (
            sum(cat_scores) / len(cat_scores) if cat_scores else 0.0
        )

    total_cost = sum(
        r.get("cost_estimate", {}).get("estimated_usd", 0) for r in results
    )

    # Collect any statuses not in the known set — these indicate runner bugs or new status codes.
    # ERRORED is produced by the runner when the eval agent itself fails (parse error, crash, etc.)
    # and is correctly bucketed in the errored counter above.
    known_statuses = {
        "PASS",
        "FAIL",
        "BLOCKED_BY_ARCHITECTURE",
        "TIMEOUT",
        "BUDGET_EXCEEDED",
        "INFRA_ERROR",
        "SETUP_ERROR",
        "SKIPPED_NO_DOCUMENT",
        "ERRORED",
    }
    unrecognized = sorted(
        {r.get("status") for r in results if r.get("status") not in known_statuses},
        key=lambda x: str(x) if x is not None else "",
    )

    scorecard = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "summary": {
            "total_scenarios": total,
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "timeout": timeout,
            "budget_exceeded": budget_exceeded,
            "infra_error": infra_error,
            "skipped": skipped,
            "errored": errored,
            "pass_rate": passed / total if total > 0 else 0.0,
            # judged_pass_rate uses only scenarios the eval agent actually judged
            # (excludes infra failures), consistent with avg_score denominator.
            # Denominator is judged count (not scores list) so PASS with null score still counts.
            "judged_pass_rate": (
                passed / sum(1 for r in results if r.get("status") in _JUDGED_STATUSES)
                if any(r.get("status") in _JUDGED_STATUSES for r in results)
                else 0.0
            ),
            "avg_score": round(avg_score, 2),
            "by_category": by_category,
        },
        "scenarios": results,
        "cost": {
            "estimated_total_usd": round(total_cost, 4),
        },
    }
    if unrecognized:
        import sys

        print(
            f"[WARN] scorecard: unrecognized status(es) bucketed as 'errored': {unrecognized}",
            file=sys.stderr,
        )
        scorecard["warnings"] = [
            f"Unrecognized status(es) bucketed as 'errored': {unrecognized}"
        ]
    return scorecard


def write_summary_md(scorecard):
    """Generate human-readable summary markdown."""
    s = scorecard.get("summary", {})
    run_id = scorecard.get("run_id", "unknown")
    ts = scorecard.get("timestamp", "")

    lines = [
        f"# GAIA Agent Eval — {run_id}",
        f"**Date:** {ts}",
        f"**Model:** {scorecard.get('config', {}).get('model', 'unknown')}",
        "",
        "## Summary",
        f"- **Total:** {s.get('total_scenarios', 0)} scenarios",
        f"- **Passed:** {s.get('passed', 0)} \u2705",
        f"- **Failed:** {s.get('failed', 0)} \u274c",
        f"- **Blocked:** {s.get('blocked', 0)} \U0001f6ab",
        f"- **Timeout:** {s.get('timeout', 0)} \u23f1",
        f"- **Budget exceeded:** {s.get('budget_exceeded', 0)} \U0001f4b8",
        f"- **Infra error:** {s.get('infra_error', 0)} \U0001f527",
        f"- **Skipped (no doc):** {s.get('skipped', 0)} \u23ed",
        f"- **Errored:** {s.get('errored', 0)} \u26a0\ufe0f",
        f"- **Pass rate (all):** {s.get('pass_rate', 0)*100:.0f}%",
        f"- **Pass rate (judged):** {s.get('judged_pass_rate', 0)*100:.0f}%",
        f"- **Avg score (judged):** {s.get('avg_score', 0):.1f}/10",
        "",
        "## By Category",
        "| Category | Pass | Fail | Blocked | Infra | Skipped | Avg Score |",
        "|----------|------|------|---------|-------|---------|-----------|",
    ]

    for cat, data in s.get("by_category", {}).items():
        infra = (
            data.get("timeout", 0)
            + data.get("budget_exceeded", 0)
            + data.get("infra_error", 0)
            + data.get("errored", 0)
        )
        lines.append(
            f"| {cat} | {data.get('passed', 0)} | {data.get('failed', 0)} | "
            f"{data.get('blocked', 0)} | {infra} | {data.get('skipped', 0)} | "
            f"{data.get('avg_score', 0):.1f} |"
        )

    lines += ["", "## Scenarios"]
    for r in scorecard.get("scenarios", []):
        icon = {
            "PASS": "\u2705",
            "FAIL": "\u274c",
            "BLOCKED_BY_ARCHITECTURE": "\U0001f6ab",
        }.get(r.get("status"), "\u26a0\ufe0f")
        score = r.get("overall_score")
        score_str = f"{score:.1f}/10" if isinstance(score, (int, float)) else "n/a"
        lines.append(
            f"- {icon} **{r.get('scenario_id', '?')}** — {r.get('status', '?')} ({score_str})"
        )
        if r.get("root_cause"):
            lines.append(f"  - Root cause: {r['root_cause']}")

    lines += [
        "",
        f"**Cost:** ${scorecard.get('cost', {}).get('estimated_total_usd', 0):.4f}",
    ]

    return "\n".join(lines) + "\n"
