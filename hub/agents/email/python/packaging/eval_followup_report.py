# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Follow-up detection eval (report mode) — CI helper (#2040 / #1950).

Precision / recall / F1 of the agent's ``awaits_reply`` flag vs a hand-labeled
corpus of sent threads (``tests/fixtures/email/followups_ground_truth.json``,
courtesy/FYI hard negatives included). Generation replays each thread into a
``FakeGmailBackend`` and runs the REAL ``check_followups_impl`` detector over its
Sent folder — fully offline: no Lemonade, no network, and NO judge credential
(the follow-up detector is deterministic; there is nothing for a judge to score).

The aggregate is compared to the committed manifest
``tests/fixtures/email/followups_gate_thresholds.json`` (enforce:false) via
``gaia.eval.followup_quality`` — same single-source rule as the other gates: no
thresholds are inlined here. Flip ``enforce`` in the manifest (data, not code) to
make this gate block once a real baseline confirms the bars (#1950).

The absence of a judge credential is therefore NOT a failure here, unlike the
drafting / action-item / briefing suites: this eval never needs one. What still
fails loudly is a harness problem — an unavailable email agent package, a
malformed corpus, or a missing thresholds manifest all raise (CLAUDE.md: no
silent fallbacks).

Config comes from the environment (shell-agnostic):
  EMAIL_EVAL_MODEL         Label recorded on the scorecard rows. Optional —
                           detection is deterministic, so it defaults to
                           ``deterministic-followup-detector`` rather than
                           borrowing the Lemonade model id the judged evals need.

Mirrors ``eval_action_item_report.py`` / ``eval_drafting_report.py``'s shape so
CI invokes it the same way as its siblings.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from gaia.eval.followup_quality import (
    default_followup_thresholds_path,
    generate_detections,
    load_default_followup_thresholds,
    load_followup_corpus,
    score_generations,
    summarize_followups,
)
from gaia.eval.judge_outage import run_with_outage_guard

CORPUS_PATH = "tests/fixtures/email/followups_ground_truth.json"

# Detection is deterministic, so the model id is only a scorecard label. Kept
# distinct from the Lemonade model ids the judged suites record so a reader of
# the artifact never mistakes this row set for a model-produced one.
DEFAULT_MODEL_ID = "deterministic-followup-detector"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Follow-up detection eval (#1606 feature / #1950 tracking / #2040 "
            "wiring) in report mode: scores the deterministic awaits_reply "
            "detector against the committed labeled corpus and the committed "
            "threshold manifest. Offline — no Lemonade, no judge credential."
        )
    )
    parser.add_argument(
        "--corpus",
        default=CORPUS_PATH,
        help=f"Labeled follow-up corpus (default: {CORPUS_PATH}).",
    )
    parser.add_argument(
        "--out-dir",
        default="eval-out",
        help="Directory for followups_report.json (default: eval-out).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Scorecard label for this run. Defaults to $EMAIL_EVAL_MODEL, else "
            f"{DEFAULT_MODEL_ID} (detection is deterministic)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    corpus_path = args.corpus
    model = args.model or os.environ.get("EMAIL_EVAL_MODEL") or DEFAULT_MODEL_ID
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    thresholds = load_default_followup_thresholds()
    print(f"[FOLLOWUP-EVAL] manifest: {default_followup_thresholds_path()}")
    print(
        f"[FOLLOWUP-EVAL] enforce={thresholds.enforce} "
        f"f1_min={thresholds.f1_min} recall_min={thresholds.recall_min} "
        f"precision_min={thresholds.precision_min} (#1950 target, REPORTED)"
    )
    print("[FOLLOWUP-EVAL] detector: deterministic latest-outbound heuristic (no LLM)")

    # Generation: replays each corpus thread into a FakeGmailBackend and runs
    # the REAL check_followups_impl. Offline — this job does not need the
    # Lemonade slot.
    generations = generate_detections(corpus_path=corpus_path)
    produced = sum(1 for g in generations if g.get("predicted") is not None)
    print(f"[FOLLOWUP-EVAL] detected for {produced}/{len(generations)} cases")

    if produced == 0:
        # A total generation outage is NOT a tolerable report-mode breach: with
        # nothing detected there is nothing to score, so the eval cannot prove
        # the detector is any good. Fail loudly regardless of the manifest's
        # enforce flag — same contract as eval_briefing_report.py:90-104.
        print(
            f"[FOLLOWUP-EVAL] ERROR: 0/{len(generations)} cases produced a verdict "
            f"from {corpus_path} — nothing to score. Check the email agent is "
            "installed and the corpus loads. Failing the build; a zero-case run "
            "must never pass as a report.",
            file=sys.stderr,
        )
        return 1

    corpus = load_followup_corpus(corpus_path)
    results = score_generations(corpus, generations, model_id=model)
    summary = summarize_followups(
        results,
        run_id=f"email-followup-eval-{model.replace('/', '-').lower()}",
        thresholds=thresholds,
    )

    gate = summary.get("followup_gate", {})
    agg = summary.get("followups", {})
    print("\n============= EMAIL FOLLOW-UP EVAL REPORT (report mode) =============")
    print(
        f"  Precision / Recall / F1 : {agg.get('precision')} / "
        f"{agg.get('recall')} / {agg.get('f1')}   "
        f"(F1 target >={thresholds.f1_min} #1950, REPORTED)"
    )
    print(
        f"  Confusion (TP/FP/FN/TN) : {agg.get('tp')} / {agg.get('fp')} / "
        f"{agg.get('fn')} / {agg.get('tn')}"
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
    print(
        f"  Follow-up gate          : passed={gate.get('passed')} "
        f"enforce={gate.get('enforce')} "
        f"should_fail={gate.get('should_fail')} "
        f"skipped={gate.get('skipped', False)}"
    )
    print("====================================================================\n")

    if gate.get("skipped"):
        # No case carried a follow-up verdict even though detections were
        # produced. Like a total outage, the eval could not prove anything —
        # fail loudly regardless of enforce and do NOT emit a report the
        # scorecard would treat as a real scored run.
        print(
            "[FOLLOWUP-EVAL] ERROR: no case carried a verdict — "
            f"{gate.get('reason', 'the follow-up gate could not be evaluated')}."
            " Failing the build regardless of report mode (no silent skip).",
            file=sys.stderr,
        )
        return 1

    (out / "followups_report.json").write_text(
        json.dumps(
            {
                "model": model,
                "corpus": corpus_path,
                "detector": "deterministic-latest-outbound",
                "summary": summary,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("[OUT] wrote eval-out/followups_report.json")

    # Same hook contract as the other gates: report mode never fails the build.
    if gate.get("should_fail"):
        print("[FOLLOWUP-EVAL] enforced gate breach — failing the build.")
        return 1
    print("[FOLLOWUP-EVAL] report mode (or no breach) — gate does not block the build.")
    return 0


if __name__ == "__main__":
    sys.exit(run_with_outage_guard(main))
