# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Action-item extraction eval (report mode) — CI helper.

Precision / recall / F1 of the agent's extracted action items vs a hand-labeled
corpus (``tests/fixtures/email/action_items_ground_truth.json``, hard negatives
included). Generation drives the REAL triage path over a FakeGmailBackend
(Lemonade — nothing is ever sent). The Claude equivalence judge resolves
borderline description pairs and is REQUIRED: ``ANTHROPIC_API_KEY`` MUST be
present, and if the judge cannot run this FAILS LOUDLY. There is NO fallback to
fuzzy-only matching — a missing or broken judge is an error, never a silent
degradation to a weaker scorer (CLAUDE.md: No Silent Fallbacks — Fail Loudly).

The aggregate is scored against the committed manifest
``tests/fixtures/email/action_items_gate_thresholds.json`` (enforce:false) via
``gaia.eval.action_item_quality`` — same single-source rule as the other gates:
no thresholds inlined here. Flip ``enforce`` in the manifest (data, not code) to
make this gate block once a baseline confirms the bars.

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

from gaia.eval.action_item_quality import (
    default_extraction_thresholds_path,
    generate_extractions,
    load_action_item_corpus,
    load_default_extraction_thresholds,
    make_claude_judge,
    score_generations,
    summarize_extraction,
)

CORPUS_PATH = "tests/fixtures/email/action_items_ground_truth.json"


def main() -> int:
    model = os.environ["EMAIL_EVAL_MODEL"]
    out = Path("eval-out")
    out.mkdir(parents=True, exist_ok=True)

    thresholds = load_default_extraction_thresholds()
    print(f"[EXTRACT-EVAL] manifest: {default_extraction_thresholds_path()}")
    print(
        f"[EXTRACT-EVAL] enforce={thresholds.enforce} "
        f"f1_min={thresholds.f1_min} recall_min={thresholds.recall_min} "
        f"precision_min={thresholds.precision_min} (#1949 target, REPORTED)"
    )

    # The Claude equivalence judge is REQUIRED — no fuzzy-only fallback. If the
    # credential is absent we FAIL LOUDLY here rather than silently scoring with
    # a weaker matcher (CLAUDE.md: No Silent Fallbacks). A judge that errors
    # mid-run also propagates and fails the step.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "[EXTRACT-EVAL] ERROR: ANTHROPIC_API_KEY is not set. The "
            "action-item extraction eval requires the Claude equivalence "
            "judge and does NOT fall back to fuzzy-only matching. Set the "
            "ANTHROPIC_API_KEY secret on this runner and re-run.",
            file=sys.stderr,
        )
        return 1
    judge_fn = make_claude_judge()
    print("[EXTRACT-EVAL] match mode: fuzzy-primary + REQUIRED Claude judge")

    # Generation: drives the REAL triage/extraction path per case (Lemonade —
    # this job holds the serial lemonade-eval slot).
    generations = generate_extractions(model, corpus_path=CORPUS_PATH)
    produced = sum(1 for g in generations if g.get("predicted") is not None)
    print(f"[EXTRACT-EVAL] extracted for {produced}/{len(generations)} cases")

    corpus = load_action_item_corpus(CORPUS_PATH)
    results = score_generations(corpus, generations, model_id=model, judge_fn=judge_fn)
    summary = summarize_extraction(
        results,
        run_id=f"email-action-item-eval-{model.replace('/', '-').lower()}",
        thresholds=thresholds,
    )

    gate = summary.get("extraction_gate", {})
    agg = summary.get("extraction", {})
    print("\n=========== EMAIL ACTION-ITEM EVAL REPORT (report mode) ===========")
    print(
        f"  Precision / Recall / F1 : {agg.get('precision')} / "
        f"{agg.get('recall')} / {agg.get('f1')}   "
        f"(F1 target >={thresholds.f1_min} #1949, REPORTED)"
    )
    print(
        f"  Hard-negative correct   : {agg.get('hard_negatives_correct')}/"
        f"{agg.get('hard_negatives_total')} "
        f"(rate {agg.get('hard_negative_correct_rate')})"
    )
    print(
        f"  Cases scored/errored    : {agg.get('cases_scored')}/"
        f"{agg.get('cases_errored')}"
    )
    print("  Match mode              : fuzzy-primary + REQUIRED Claude judge")
    print(
        f"  Extraction gate         : passed={gate.get('passed')} "
        f"enforce={gate.get('enforce')} "
        f"should_fail={gate.get('should_fail')} "
        f"skipped={gate.get('skipped', False)}"
    )
    print("==================================================================\n")

    (out / "action_items_report.json").write_text(
        json.dumps(
            {
                "model": model,
                "corpus": CORPUS_PATH,
                "match_mode": "fuzzy-primary+required-claude-judge",
                "summary": summary,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("[OUT] wrote eval-out/action_items_report.json")

    # Same hook contract as the other gates: report mode never fails.
    if gate.get("should_fail"):
        print("[EXTRACT-EVAL] enforced gate breach — failing the build.")
        return 1
    print("[EXTRACT-EVAL] report mode (or no breach) — gate does not block the build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
