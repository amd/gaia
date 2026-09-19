# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Fail an email-triage eval that never actually reached the LLM.

``EmailTriageAgent`` has a heuristic classification path that returns a verdict
without calling the model. On that path the triage envelope carries no usage
block, so ``llm_classified_count`` is **absent** from the scorecard -- not zero.
A run that classified every message by rule therefore produces a complete,
green, entirely meaningless scorecard: accuracy numbers that say nothing about
model quality.

That is not hypothetical. A PR-profile run triaged 20 messages in 14 seconds
(0.7s/message) against the full profile's 27.7s/message -- a 40x gap that only
makes sense if the model was never invoked.

This is a GATE, not a reporter: it exits non-zero when coverage is missing or
zero, because the alternative is publishing a card nobody can trust. It reads
the same ``scorecard.json`` the harness already writes -- no new plumbing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_EVAL_DIR = Path("eval-out")


def _emit_summary(line: str) -> None:
    """Append a line to the GitHub job summary when running under Actions."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with open(summary, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _corpus_size(payload: dict) -> int | None:
    """Messages triaged in a single run, from the per-scenario perf summaries.

    Mirrors ``gen_scorecard.py``'s derivation: the harness's ``scorecard.json``
    has no ``emails_per_run`` (that key is added later, on the release card), so
    the denominator has to come from ``scenarios[].performance_summary`` — the
    same corpus each run, hence ``max``.
    """
    sizes = [
        int(ps["total_emails"])
        for s in payload.get("scenarios") or []
        if isinstance(ps := (s or {}).get("performance_summary"), dict)
        and isinstance(ps.get("total_emails"), (int, float))
        and not isinstance(ps["total_emails"], bool)
    ]
    return max(sizes) if sizes else None


def check(eval_dir: Path) -> int:
    scorecard_path = eval_dir / "scorecard.json"
    if not scorecard_path.is_file():
        print(
            f"ERROR: no scorecard.json under {eval_dir}/ -- the benchmark step did "
            f"not produce one, so LLM coverage cannot be verified.\n"
            f"  Check the 'Run email-triage benchmark' step above for a crash.",
            file=sys.stderr,
        )
        return 1

    try:
        payload = json.loads(scorecard_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(
            f"ERROR: {scorecard_path} is not readable JSON: {exc}\n"
            f"  The benchmark step wrote a truncated or corrupt scorecard; check it "
            f"for a mid-write crash.",
            file=sys.stderr,
        )
        return 1

    performance = payload.get("performance") or {}
    classified = performance.get("llm_classified_count")
    emails = _corpus_size(payload)

    if classified is None:
        print(
            "ERROR: the eval never called the LLM. `llm_classified_count` is absent "
            "from scorecard.json, which is what EmailTriageAgent's heuristic-only "
            "path produces (no LLM call means no usage block to report).\n"
            "  This run proves nothing about triage quality -- its accuracy numbers "
            "reflect rules, not the model.\n"
            "  Check that the Lemonade model loaded and that triage is not "
            "short-circuiting to heuristics: "
            "hub/agents/email/python/gaia_agent_email/agent.py",
            file=sys.stderr,
        )
        _emit_summary("- **LLM coverage**: ❌ none — heuristic-only run, results void")
        return 1

    if float(classified) <= 0:
        print(
            f"ERROR: the eval classified 0 of {emails} messages with the LLM.\n"
            f"  `llm_classified_count` is present but zero, so every message took "
            f"the heuristic path. This run proves nothing about triage quality.\n"
            f"  Check the Lemonade model load and EmailTriageAgent classification.",
            file=sys.stderr,
        )
        _emit_summary(
            f"- **LLM coverage**: ❌ 0 / {emails} — heuristic-only run, results void"
        )
        return 1

    if emails:
        pct = round(float(classified) / float(emails) * 100, 1)
        print(f"PASS: LLM coverage {classified} / {emails} messages ({pct}%).")
        _emit_summary(f"- **LLM coverage**: ✅ {classified} / {emails} ({pct}%)")
    else:
        print(f"PASS: LLM coverage {classified} messages.")
        _emit_summary(f"- **LLM coverage**: ✅ {classified} messages")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "eval_dir",
        nargs="?",
        default=str(DEFAULT_EVAL_DIR),
        help="Directory holding the harness's scorecard.json (default: eval-out)",
    )
    args = parser.parse_args(argv)
    return check(Path(args.eval_dir))


if __name__ == "__main__":
    sys.exit(main())
