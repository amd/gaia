#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Record a real LLM categorization baseline for the synthetic email corpus.

This is the harness that produces the #1230 ``category_accuracy`` numbers
on the demo models. It is NOT run in CI — it needs a live Lemonade server
(single-tenant; run serially). The orchestrator runs it on the test
machine.

Two Gemma-4 demo models are recorded:
- ``Gemma-4-E4B-it-GGUF`` (primary; matches the repo's gemma-4-e4b-*
  baselines and the integration test) -> ``baseline_accuracy.json``
- ``Gemma-4-E2B-it-GGUF`` (smaller second model) ->
  ``baseline_accuracy_e2b.json``

What it does:

1. Loads the committed ``synthetic_inbox.mbox`` through the same
   ``FakeGmailBackend`` the integration test uses, so message ids and body
   extraction match production exactly.
2. For each message, asks the LLM to classify it into exactly one of the
   four v0.20 taxonomy categories (``ALL_CATEGORIES``) — the same taxonomy
   the agent emits and the eval scores against.
3. Scores per-message category accuracy against ``ground_truth.json``
   (which is keyed by the Gmail-derived id, so it aligns 1:1).
4. Writes the chosen baseline file (``--out``) with the model, the
   measured ``category_accuracy``, and a per-category breakdown.

Usage (on the test machine, Lemonade at :13305)::

    export LEMONADE_BASE_URL=http://localhost:13305
    # Primary demo model -> baseline_accuracy.json (the default --out):
    python tests/fixtures/email/score_baseline.py \
        --model Gemma-4-E4B-it-GGUF --write
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
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.agents.email.gmail_backend import decode_message_body  # noqa: E402
from gaia.agents.email.tools.triage_heuristics import ALL_CATEGORIES  # noqa: E402
from gaia.llm.lemonade_client import LemonadeClient  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
CORPUS_MBOX = FIXTURES_DIR / "synthetic_inbox.mbox"
GROUND_TRUTH = FIXTURES_DIR / "ground_truth.json"
BASELINE_OUT = FIXTURES_DIR / "baseline_accuracy.json"

_CATEGORY_LIST = ", ".join(f'"{c}"' for c in ALL_CATEGORIES)

_SYSTEM = (
    "You are an email triage classifier. Classify the email into exactly "
    f"one of these categories: {_CATEGORY_LIST}. "
    "Definitions: urgent = needs action now / hard same-day deadline / "
    "incident; actionable = needs a reply or decision but not same-day; "
    "informational = FYI, notifications, receipts, no action; "
    '"low priority" = promotions, newsletters, social, bulk. '
    "Reply with ONLY the category string, nothing else."
)


def _headers(msg: Dict[str, Any]) -> Dict[str, str]:
    return {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in (msg.get("payload") or {}).get("headers", [])
    }


def _normalize(raw: str) -> str:
    """Map a free-form model reply onto one of the taxonomy strings."""
    text = (raw or "").strip().strip(".").strip('"').lower()
    # Direct hit on a taxonomy string.
    for cat in ALL_CATEGORIES:
        if text == cat:
            return cat
    # Tolerate "low_priority" / "low-priority" and substring leakage.
    if "low" in text and ("prior" in text or "_" in text or "-" in text):
        return "low priority"
    for cat in ALL_CATEGORIES:
        if cat in text:
            return cat
    return "informational"  # explicit, recorded as a miss if wrong


def _classify(client: LemonadeClient, model: str, subject: str, body: str) -> str:
    user = f"Subject: {subject}\n\nBody:\n{body[:2000]}"
    resp = client.chat_completions(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_completion_tokens=16,
        stream=False,
    )
    content = resp["choices"][0]["message"]["content"]
    return _normalize(content)


def score(model: str, max_messages: int = 1000) -> Dict[str, Any]:
    if not CORPUS_MBOX.exists() or not GROUND_TRUTH.exists():
        raise FileNotFoundError(
            "Corpus not generated. Run tests/fixtures/email/generate_mbox.py first. "
            f"Looked for {CORPUS_MBOX} and {GROUND_TRUTH}."
        )

    backend = FakeGmailBackend(CORPUS_MBOX)
    ground_truth = json.loads(GROUND_TRUTH.read_text())
    labels = {k: v for k, v in ground_truth.items() if not k.startswith("_")}

    client = LemonadeClient(model=model)

    listing = backend.list_messages(label_ids=["INBOX"], max_results=max_messages)
    correct = 0
    total = 0
    per_cat_total: Counter = Counter()
    per_cat_correct: Counter = Counter()
    misses = []

    for stub in listing.get("messages", []):
        gt = labels.get(stub["id"])
        if gt is None:
            continue
        msg = backend.get_message(stub["id"])
        hdrs = _headers(msg)
        body, _ = decode_message_body(msg.get("payload") or {})
        predicted = _classify(client, model, hdrs.get("subject", ""), body)
        expected = gt["category"]
        total += 1
        per_cat_total[expected] += 1
        if predicted == expected:
            correct += 1
            per_cat_correct[expected] += 1
        else:
            misses.append(
                {"id": stub["id"], "predicted": predicted, "expected": expected}
            )

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
    return {
        "model": model,
        "fixture": CORPUS_MBOX.name,
        "category_accuracy": category_accuracy,
        "category_breakdown": breakdown,
        "scored": total,
        "correct": correct,
        "tolerance_pp": 5,
        "_recorded_on": date.today().isoformat(),
        "_recorded_by": f"real LLM measurement on {model}",
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

    scorecard = score(args.model, max_messages=args.max_messages)
    print(json.dumps({k: v for k, v in scorecard.items() if k != "_misses"}, indent=2))
    print(f"\nMisses: {len(scorecard['_misses'])}")

    if args.write:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
        print(f"\nWrote baseline to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
