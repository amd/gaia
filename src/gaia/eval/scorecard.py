"""
Scorecard generator — builds scorecard.json + summary.md from scenario results.
"""

from datetime import datetime

WEIGHTS = {
    "correctness": 0.25,
    "tool_selection": 0.20,
    "context_retention": 0.20,
    "completeness": 0.15,
    "efficiency": 0.10,
    "personality": 0.05,
    "error_recovery": 0.05,
}

# Statuses where the scenario was actually judged (not an infrastructure failure)
_JUDGED_STATUSES = {"PASS", "FAIL", "BLOCKED_BY_ARCHITECTURE"}


def build_scorecard(run_id, results, config):
    """Build scorecard dict from list of scenario result dicts."""
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "PASS")
    failed = sum(1 for r in results if r.get("status") == "FAIL")
    blocked = sum(1 for r in results if r.get("status") == "BLOCKED_BY_ARCHITECTURE")
    timeout = sum(1 for r in results if r.get("status") == "TIMEOUT")
    budget_exceeded = sum(1 for r in results if r.get("status") == "BUDGET_EXCEEDED")
    errored = total - passed - failed - blocked - timeout - budget_exceeded

    # avg_score only counts judged scenarios (not infra failures with score=0)
    scores = [
        r["overall_score"]
        for r in results
        if r.get("status") in _JUDGED_STATUSES and r.get("overall_score") is not None
    ]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    # By category
    by_category = {}
    for r in results:
        cat = r.get("category", "unknown")
        if cat not in by_category:
            by_category[cat] = {
                "passed": 0,
                "failed": 0,
                "blocked": 0,
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
        else:
            by_category[cat]["errored"] += 1
        if r.get("overall_score") is not None:
            by_category[cat]["scores"].append(r["overall_score"])

    for cat in by_category:
        cat_scores = by_category[cat].pop("scores", [])
        by_category[cat]["avg_score"] = (
            sum(cat_scores) / len(cat_scores) if cat_scores else 0.0
        )

    total_cost = sum(
        r.get("cost_estimate", {}).get("estimated_usd", 0) for r in results
    )

    return {
        "run_id": run_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "config": config,
        "summary": {
            "total_scenarios": total,
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "timeout": timeout,
            "budget_exceeded": budget_exceeded,
            "errored": errored,
            "pass_rate": passed / total if total > 0 else 0.0,
            "avg_score": round(avg_score, 2),
            "by_category": by_category,
        },
        "scenarios": results,
        "cost": {
            "estimated_total_usd": round(total_cost, 4),
        },
    }


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
        f"- **Errored:** {s.get('errored', 0)} \u26a0\ufe0f",
        f"- **Pass rate:** {s.get('pass_rate', 0)*100:.0f}%",
        f"- **Avg score:** {s.get('avg_score', 0):.1f}/10",
        "",
        "## By Category",
        "| Category | Pass | Fail | Blocked | Avg Score |",
        "|----------|------|------|---------|-----------|",
    ]

    for cat, data in s.get("by_category", {}).items():
        lines.append(
            f"| {cat} | {data.get('passed', 0)} | {data.get('failed', 0)} | "
            f"{data.get('blocked', 0)} | {data.get('avg_score', 0):.1f} |"
        )

    lines += ["", "## Scenarios"]
    for r in scorecard.get("scenarios", []):
        icon = {
            "PASS": "\u2705",
            "FAIL": "\u274c",
            "BLOCKED_BY_ARCHITECTURE": "\U0001f6ab",
        }.get(r.get("status"), "\u26a0\ufe0f")
        lines.append(
            f"- {icon} **{r.get('scenario_id', '?')}** — {r.get('status', '?')} "
            f"({r.get('overall_score', 0):.1f}/10)"
        )
        if r.get("root_cause"):
            lines.append(f"  - Root cause: {r['root_cause']}")

    lines += [
        "",
        f"**Cost:** ${scorecard.get('cost', {}).get('estimated_total_usd', 0):.4f}",
    ]

    return "\n".join(lines) + "\n"
