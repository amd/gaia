# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Voice-drafting quality eval (judge-scored, enforcing) — CI helper.

Drives the #1607 voice-profile drafting path over the committed drafting seed
corpus (Lemonade + FakeGmailBackend — drafting only, nothing is ever sent) and
scores each draft with a Claude judge against the case rubric. The aggregate
``draft_approval_rate`` is compared to the committed manifest
``tests/fixtures/email/drafting_gate_thresholds.json`` (enforce:true) via
``gaia.eval.draft_quality`` — same single-source rule as the other gates: no
thresholds inlined here. The manifest owns the enforce switch (data, not code).

``ANTHROPIC_API_KEY`` is REQUIRED — the Claude judge cannot score drafts without
it, and per CLAUDE.md's fail-loudly rule there is NO silent skip: its absence is
a HARD FAILURE (exit 1), never a skip report and never an invented pass. The
workflows that run this (release_agent_email.yml, test_email_agent_eval.yml,
email_scorecard_refresh.yml) inject the key from ``secrets.ANTHROPIC_API_KEY``
and never run on fork PRs, so the key is always present in a legitimate run.

Config comes from the environment (shell-agnostic):
  EMAIL_EVAL_MODEL   Lemonade model id (required)
  ANTHROPIC_API_KEY  Claude judge credential (REQUIRED; absence -> exit 1)

Extracted verbatim from the former inline ``python - <<'PY'`` step so the eval
can run on the Windows ``stx`` runner pool (PowerShell, no heredocs).
"""

import json
import os
import sys
from pathlib import Path

from gaia.eval.draft_quality import (
    default_drafting_thresholds_path,
    generate_drafts,
    judge_drafts,
    load_default_drafting_thresholds,
    load_drafting_corpus,
    make_claude_judge,
    summarize_drafting,
)

CORPUS_PATH = "tests/fixtures/email/drafting_ground_truth.json"


def main() -> int:
    model = os.environ["EMAIL_EVAL_MODEL"]
    out = Path("eval-out")
    out.mkdir(parents=True, exist_ok=True)

    thresholds = load_default_drafting_thresholds()
    print(f"[DRAFT-EVAL] manifest: {default_drafting_thresholds_path()}")
    print(
        f"[DRAFT-EVAL] enforce={thresholds.enforce} "
        f"approval_min={thresholds.approval_min} (#1269 target, REPORTED)"
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Fail loudly (CLAUDE.md: No Silent Fallbacks — Fail Loudly). The judge
        # credential is REQUIRED to score drafts; its absence is a hard failure,
        # never a skip — regardless of the manifest's enforce flag. No skip report
        # is written and no pass is invented, so the release / nightly cannot ship
        # a silently un-judged drafting gate.
        print(
            "[DRAFT-EVAL] ERROR: ANTHROPIC_API_KEY is not set — the Claude judge "
            "cannot score the voice-drafting eval.\n"
            "  What to do: set ANTHROPIC_API_KEY on this runner. The workflow step "
            "injects it from secrets.ANTHROPIC_API_KEY; add/repair that repo (or "
            "org) secret.\n"
            "  Where: the 'Voice-drafting quality eval' step in "
            ".github/workflows/{release_agent_email,test_email_agent_eval,"
            "email_scorecard_refresh}.yml.",
            file=sys.stderr,
        )
        return 1

    # Generation: drives the #1607 voice-profile drafting path per case
    # (Lemonade — this job holds the serial lemonade-eval slot).
    generations = generate_drafts(model, corpus_path=CORPUS_PATH)
    produced = sum(1 for g in generations if g["draft"])
    print(f"[DRAFT-EVAL] generated {produced}/{len(generations)} drafts")

    results = judge_drafts(
        load_drafting_corpus(CORPUS_PATH),
        generations,
        make_claude_judge(),
        model_id=model,
    )
    summary = summarize_drafting(
        results,
        run_id=f"email-draft-eval-{model.replace('/', '-').lower()}",
        thresholds=thresholds,
    )

    gate = summary.get("drafting_gate", {})
    agg = summary.get("drafting", {})
    print("\n============ EMAIL DRAFTING EVAL REPORT (enforcing) ============")
    print(
        f"  Draft-approval rate : {agg.get('draft_approval_rate')}   "
        f"(target >={thresholds.approval_min} #1269, REPORTED)"
    )
    print(f"  Voice-match mean    : {agg.get('voice_match_mean')}")
    print(f"  Grounded rate       : {agg.get('grounded_rate')}")
    print(
        f"  Cases judged/errored: {agg.get('cases_judged')}/"
        f"{agg.get('cases_errored')}"
    )
    print(
        f"  Drafting gate       : passed={gate.get('passed')} "
        f"enforce={gate.get('enforce')} "
        f"should_fail={gate.get('should_fail')} "
        f"skipped={gate.get('skipped', False)}"
    )
    print("==================================================================\n")

    (out / "drafting_gate_report.json").write_text(
        json.dumps(
            {"model": model, "corpus": CORPUS_PATH, "summary": summary},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("[OUT] wrote eval-out/drafting_gate_report.json")

    # Same hook contract as the other gates: with enforce:true a breach blocks.
    if gate.get("should_fail"):
        print("[DRAFT-EVAL] enforced gate breach — failing the build.")
        return 1
    print("[DRAFT-EVAL] drafting gate passed — no breach.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
