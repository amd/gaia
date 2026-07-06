#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Record a real email-triage baseline for the synthetic corpus via the
PRODUCTION heuristic + LLM-assist path (#1107).

This is the harness that produces the #1230 baseline numbers on the demo
models. It is NOT run in CI — it needs a live Lemonade server
(single-tenant; run E4B then E2B SERIALLY, never concurrently). The
orchestrator runs it on the test machine.

It drives the SAME path the integration test gates on, so the recorded
numbers are apples-to-apples:

    triage_inbox_impl(fake_gmail, classifier=make_llm_classifier(agent.chat))

i.e. the heuristic fast path with LLM follow-up for every message the
heuristic isn't confident about (and always urgent-vs-actionable). This
replaces the earlier standalone single-prompt classifier — the baseline
must reflect what the production tool actually does.

Two Gemma-4 demo models are recorded:
- ``Gemma-4-E4B-it-GGUF`` (primary; matches the repo's gemma-4-e4b-*
  baselines and the integration test) -> ``baseline_accuracy.json``
- ``Gemma-4-E2B-it-GGUF`` (smaller second model) ->
  ``baseline_accuracy_e2b.json``

What it does:

1. Loads the committed ``synthetic_inbox.mbox`` through the same
   ``FakeGmailBackend`` the integration test uses (message ids + body
   extraction match production exactly).
2. Runs ``triage_inbox_impl`` with the production LLM-assist classifier on
   the chosen model.
3. Scores per-message category / is_spam / is_phishing accuracy against
   ``ground_truth.json`` (keyed by the Gmail-derived id, so it aligns 1:1).
4. Writes the chosen baseline file (``--out``) with the measured
   ``category_accuracy`` + per-category breakdown + ``is_spam_accuracy`` +
   ``is_phishing_accuracy``.

No silent fallbacks: if the LLM is unreachable or returns an unusable
result, ``make_llm_classifier`` raises ``LLMTriageError`` and this harness
exits non-zero rather than recording a bogus number.

Usage (on the test machine, Lemonade at :13305)::

    export LEMONADE_BASE_URL=http://localhost:13305
    # Primary demo model -> baseline_accuracy.json (the default --out):
    python tests/fixtures/email/score_baseline.py --model Gemma-4-E4B-it-GGUF --write
    # Second demo model -> baseline_accuracy_e2b.json:
    python tests/fixtures/email/score_baseline.py \
        --model Gemma-4-E2B-it-GGUF \
        --out tests/fixtures/email/baseline_accuracy_e2b.json --write

Omit ``--write`` for a dry run that prints the scorecard without writing
the baseline file.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools.llm_triage import make_llm_classifier  # noqa: E402
from gaia_agent_email.tools.read_tools import triage_inbox_impl  # noqa: E402
from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402
from tests.fixtures.email.generate_mbox import ensure_corpus  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
CORPUS_MBOX = FIXTURES_DIR / "synthetic_inbox.mbox"
GROUND_TRUTH = FIXTURES_DIR / "ground_truth.json"
BASELINE_OUT = FIXTURES_DIR / "baseline_accuracy.json"


def score(
    model: str, ctx_size: int | None = None, max_messages: int = 1000
) -> Dict[str, Any]:
    # mbox + ground_truth are generated artifacts; build them from the committed
    # seed if this checkout doesn't have them yet (raises loudly if the seed is
    # missing).
    ensure_corpus(CORPUS_MBOX, GROUND_TRUTH)

    backend = FakeGmailBackend(CORPUS_MBOX)
    ground_truth = json.loads(GROUND_TRUTH.read_text())
    labels = {k: v for k, v in ground_truth.items() if not k.startswith("_")}

    # Build the production LLM-assist classifier from a real agent's chat —
    # the exact path the integration test gates on.
    with tempfile.TemporaryDirectory() as td:
        agent = EmailTriageAgent(
            config=EmailAgentConfig(
                model_id=model,
                gmail_backend=backend,
                db_path=str(Path(td) / "state.db"),
                silent_mode=True,
                ctx_size=ctx_size,
            )
        )
        # Record the ctx pin READ BACK off the constructed agent's client —
        # never echo the requested argument (#1892). None = not pinned.
        recorded_ctx = agent.chat.llm_client._backend.ctx_size_override
        classifier = make_llm_classifier(agent.chat)
        triage = triage_inbox_impl(
            backend, max_messages=max_messages, classifier=classifier
        )

    results_by_id = {r["id"]: r for r in triage["results"]}

    correct = 0
    total = 0
    correct_spam = 0
    correct_phishing = 0
    spam_tp = spam_fp = spam_fn = 0
    per_cat_total: Counter = Counter()
    per_cat_correct: Counter = Counter()
    misses = []

    for msg_id, gt in labels.items():
        result = results_by_id.get(msg_id)
        if result is None:
            continue
        total += 1
        expected = gt["category"]
        per_cat_total[expected] += 1
        if result["category"] == expected:
            correct += 1
            per_cat_correct[expected] += 1
        else:
            misses.append(
                {
                    "id": msg_id,
                    "predicted": result["category"],
                    "expected": expected,
                    "source": result.get("source"),
                }
            )
        if result["is_spam"] == gt["is_spam"]:
            correct_spam += 1
        if result["is_spam"] and gt["is_spam"]:
            spam_tp += 1
        elif result["is_spam"] and not gt["is_spam"]:
            spam_fp += 1
        elif not result["is_spam"] and gt["is_spam"]:
            spam_fn += 1
        if result["is_phishing"] == gt["is_phishing"]:
            correct_phishing += 1

    if total == 0:
        raise RuntimeError(
            "Scored 0 messages — ground_truth ids do not align with the loaded "
            "corpus. Regenerate the corpus (generate_mbox.py) so keys match "
            "FakeGmailBackend's Gmail-derived ids."
        )

    category_accuracy = round(correct / total, 4)
    breakdown = {
        cat: round(per_cat_correct[cat] / per_cat_total[cat], 4)
        for cat in ALL_CATEGORIES
        if per_cat_total[cat]
    }
    # Plain accuracy is misleading for is_spam: spam is a small minority class
    # (~19% of this corpus) so a trivial always-False classifier already scores
    # high. Precision/recall/F1 are the metrics that actually distinguish real
    # detection from doing nothing (#1906).
    spam_precision = (
        round(spam_tp / (spam_tp + spam_fp), 4) if (spam_tp + spam_fp) else 0.0
    )
    spam_recall = (
        round(spam_tp / (spam_tp + spam_fn), 4) if (spam_tp + spam_fn) else 0.0
    )
    spam_f1 = (
        round(2 * spam_precision * spam_recall / (spam_precision + spam_recall), 4)
        if (spam_precision + spam_recall)
        else 0.0
    )
    return {
        "model": model,
        "ctx_size": recorded_ctx,
        "fixture": CORPUS_MBOX.name,
        "category_accuracy": category_accuracy,
        "category_breakdown": breakdown,
        "is_spam_accuracy": round(correct_spam / total, 4),
        "is_spam_precision": spam_precision,
        "is_spam_recall": spam_recall,
        "is_spam_f1": spam_f1,
        "is_spam_tp": spam_tp,
        "is_spam_fp": spam_fp,
        "is_spam_fn": spam_fn,
        "is_phishing_accuracy": round(correct_phishing / total, 4),
        "scored": total,
        "correct": correct,
        "tolerance_pp": 5,
        "_recorded_on": date.today().isoformat(),
        "_recorded_by": (
            f"real measurement on {model} via production heuristic + "
            "LLM-assist triage path (#1107); is_spam now content-based "
            "detection with precision/recall/F1 (#1906)"
        ),
        "_misses": misses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default="Gemma-4-E4B-it-GGUF", help="Lemonade model id"
    )
    parser.add_argument(
        "--max-messages", type=int, default=1000, help="Cap messages scored"
    )
    parser.add_argument(
        "--ctx-size",
        type=int,
        default=None,
        help="Exact ctx window to pin the model load to (#1892 envelope: "
        "16384 target / 32768 max). Omitted = Lemonade's registry floor; "
        "the recorded baseline stamps whichever was used.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the result to the baseline file (see --out)",
    )
    parser.add_argument(
        "--out",
        default=str(BASELINE_OUT),
        help="Baseline file to write when --write is set "
        "(default: baseline_accuracy.json). Use a per-model path to record "
        "more than one demo model.",
    )
    args = parser.parse_args()

    scorecard = score(args.model, ctx_size=args.ctx_size, max_messages=args.max_messages)
    print(json.dumps({k: v for k, v in scorecard.items() if k != "_misses"}, indent=2))
    print(f"\nMisses: {len(scorecard['_misses'])}")

    if args.write:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
        print(f"\nWrote baseline to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
