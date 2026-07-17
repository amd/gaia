# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Daily-briefing summary-quality eval (judge-scored, enforcing gate) — CI helper.

Judge-scored briefing quality: the REAL scheduled-briefing path
(``gaia_agent_email.briefing.run_briefing_job`` -> ``pre_scan_inbox_impl``)
produces the ``email_pre_scan`` envelope over the committed briefing seed corpus
(FakeGmailBackend — read-only, nothing sent/archived), and a Claude judge scores
each briefing against the case inbox + rubric on faithfulness / must-include
recall / hallucination-free / grouping. The aggregate is compared to the
committed manifest ``tests/fixtures/email/briefing_gate_thresholds.json``
(enforce:true) via ``gaia.eval.briefing_quality`` — same single-source rule as
the other gates: no thresholds inlined here; tune the bars in the manifest.

This gate BLOCKS: a quality breach, any errored/unjudged case, a missing judge
credential, or a judge transport error all fail the build. There is deliberately
NO report-mode fallback and NO silent skip — if the eval cannot prove the
briefing is good, the pipeline goes red (CLAUDE.md: No Silent Fallbacks).

Config comes from the environment (shell-agnostic):
  EMAIL_EVAL_MODEL   Lemonade model id (required)
  ANTHROPIC_API_KEY  Claude judge credential (REQUIRED; absence -> loud failure)

Extracted verbatim from the former inline ``python - <<'PY'`` step so the eval
can run on the Windows ``stx`` runner pool (PowerShell, no heredocs).
"""

import json
import os
import sys
from pathlib import Path

from gaia.eval.briefing_quality import (
    default_briefing_thresholds_path,
    generate_briefings,
    judge_briefings,
    load_briefing_corpus,
    load_default_briefing_thresholds,
    make_claude_judge,
    summarize_briefings,
)

CORPUS_PATH = "tests/fixtures/email/briefing_ground_truth.json"


def main() -> int:
    model = os.environ["EMAIL_EVAL_MODEL"]
    out = Path("eval-out")
    out.mkdir(parents=True, exist_ok=True)

    thresholds = load_default_briefing_thresholds()
    print(f"[BRIEF-EVAL] manifest: {default_briefing_thresholds_path()}")
    print(
        f"[BRIEF-EVAL] enforce={thresholds.enforce} "
        f"approval_min={thresholds.approval_min} "
        f"recall_min={thresholds.recall_min} "
        f"hallucination_free_min={thresholds.hallucination_free_min} "
        f"(#1951 ENFORCED gate)"
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # No fallback, no skip: the judge credential is required to prove the
        # briefing is good. Its absence FAILS the build (actionable error), it
        # does not quietly pass.
        print(
            "[BRIEF-EVAL] ERROR: ANTHROPIC_API_KEY is not set on this runner — "
            "the Claude judge cannot score briefings. Configure the "
            "ANTHROPIC_API_KEY secret for this workflow. Failing the build "
            "(this gate has no report-mode fallback).",
            file=sys.stderr,
        )
        return 1

    # Generation: drives the REAL scheduled-briefing path per case (heuristic
    # classification — read-only, nothing sent/archived).
    generations = generate_briefings(model, corpus_path=CORPUS_PATH)
    produced = sum(1 for g in generations if g["briefing"])
    print(f"[BRIEF-EVAL] generated {produced}/{len(generations)} briefings")

    if produced == 0:
        # A total generation outage is NOT a tolerable report-mode breach: with
        # nothing generated there is nothing to judge, so the eval cannot prove
        # the briefing is any good. Fail loudly regardless of the manifest's
        # enforce flag — same contract as the missing-judge-credential path
        # above (module docstring: no report-mode fallback, no silent skip).
        print(
            f"[BRIEF-EVAL] ERROR: 0/{len(generations)} briefings were generated "
            f"from {CORPUS_PATH} — nothing to judge. The generation path produced "
            "no envelope for any case (check the corpus loads and the email agent "
            "is installed). Failing the build; a zero-case run must never pass as "
            "a report.",
            file=sys.stderr,
        )
        return 1

    results = judge_briefings(
        load_briefing_corpus(CORPUS_PATH),
        generations,
        make_claude_judge(),
        model_id=model,
    )
    summary = summarize_briefings(
        results,
        run_id=f"email-briefing-eval-{model.replace('/', '-').lower()}",
        thresholds=thresholds,
    )

    gate = summary.get("briefing_gate", {})
    agg = summary.get("briefing", {})
    print("\n========== EMAIL BRIEFING EVAL REPORT (enforcing gate) ==========")
    print(
        f"  Briefing-approval rate : {agg.get('briefing_approval_rate')}   "
        f"(target >={thresholds.approval_min} #1951, ENFORCED)"
    )
    print(f"  Must-include recall    : {agg.get('must_include_recall_mean')}")
    print(f"  Faithful rate          : {agg.get('faithful_rate')}")
    print(f"  Hallucination-free rate: {agg.get('hallucination_free_rate')}")
    print(
        f"  Cases judged/errored   : {agg.get('cases_judged')}/"
        f"{agg.get('cases_errored')}"
    )
    print(
        f"  Briefing gate          : passed={gate.get('passed')} "
        f"enforce={gate.get('enforce')} "
        f"should_fail={gate.get('should_fail')} "
        f"skipped={gate.get('skipped', False)}"
    )
    print("===============================================================\n")

    if gate.get("skipped"):
        # No case carried a judge verdict even though briefings were generated
        # (e.g. every judge reply was unparseable). Like a total outage, the
        # eval could not prove anything — fail loudly regardless of enforce and
        # do NOT emit a report the scorecard would treat as a real judged run.
        print(
            "[BRIEF-EVAL] ERROR: no case was judged — "
            f"{gate.get('reason', 'the briefing-quality gate could not be evaluated')}."
            " Failing the build regardless of report mode (no silent skip).",
            file=sys.stderr,
        )
        return 1

    (out / "briefing_gate_report.json").write_text(
        json.dumps(
            {"model": model, "corpus": CORPUS_PATH, "summary": summary},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("[OUT] wrote eval-out/briefing_gate_report.json")

    # Enforcing gate: any breach (quality below bar, or an errored/unjudged
    # case) fails the build. No report-mode fallback.
    if gate.get("should_fail"):
        print(f"[BRIEF-EVAL] gate breach {gate.get('breaches')} — failing the build.")
        return 1
    print("[BRIEF-EVAL] gate passed — briefing quality is above the bars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
