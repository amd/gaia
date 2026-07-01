#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Select the committed eval corpus seed from the vendor mailbox dataset.

The vendor ships a large (~1800-record) labelled mailbox dataset in the
schema-2.0 triage taxonomy. This script deterministically selects a small,
**category-balanced** subset and writes it to ``vendor_corpus_seed.jsonl`` — the
committed source of truth the corpus builder (``generate_mbox.py``) converts into
``synthetic_inbox.mbox`` + ``ground_truth.json``.

Selection is deterministic (fixed seed + stable id sort) and PII-conscious:

- Only ``origin_type`` ∈ {synthetic, public_llm_labeled} is eligible, and the raw
  real-person source corpora (Enron, Hillary-Clinton) are excluded — so no real
  personal correspondence lands in a committed fixture. The public spam corpora
  (SpamAssassin / ling_spam) are kept: they carry no personal PII (the vendor has
  already wrapped them in synthetic sender/recipient envelopes) and are needed for
  the spam axis.
- Per category a fixed quota is taken (all available PERSONAL, since it is the
  scarcest), with phishing/spam records prioritised so those axes stay measurable.

The vendor source file is NOT committed (size + provenance); pass its path:

    python tests/fixtures/email/select_vendor_subset.py --source /path/to/mailbox.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
import random
from pathlib import Path

SEED = 23023
OUT_DIR = Path(__file__).resolve().parent
OUT_SEED = OUT_DIR / "vendor_corpus_seed.jsonl"

# Eligible provenance (PII-conscious): synthetic + LLM-labelled-public, never the
# raw real-person corpora.
_ELIGIBLE_ORIGINS = {"synthetic", "public_llm_labeled"}
_EXCLUDED_SOURCES = {"enron", "hillary_clinton_emails"}
_SPAM_SOURCES = {"spamassassin", "ling_spam"}

# Per-category quota. PERSONAL is the scarcest bucket, so take all available.
_PER_CATEGORY = {
    "URGENT": 54,
    "NEEDS_RESPONSE": 54,
    "FYI": 54,
    "PROMOTIONAL": 54,
    "PERSONAL": 10**9,
}

# Fields kept in the committed seed: exactly the ones ``generate_mbox.py``
# consumes to build the mbox + ground_truth. Bulky generation/variation metadata
# and fields the builder never reads (message_id/thread_id/in_reply_to/references
# — the builder derives Message-ID/threadId from ``id``; mailbox_owner/category_v1
# — unused) are dropped so the committed fixture carries only what the corpus
# actually needs.
_KEEP_FIELDS = [
    "id",
    "sender",
    "to",
    "cc",
    "subject",
    "date",
    "body",
    "category",
    "suggestedAction",
    "is_phishing",
    "promotional_subtype",
    "source_dataset",
    "mailbox_persona",
]


def _is_spam_source(rec: dict) -> bool:
    # Selection priority heuristic only — NOT ground-truth spam.
    # Treats spamassassin/ling_spam records (which contain both spam and HAM)
    # as "special" so they fill the spam-axis quota in the selected subset.
    # Do NOT use this to set is_spam labels; use generate_mbox._is_spam for that.
    return (
        rec.get("promotional_subtype") == "spam"
        or rec.get("source_dataset") in _SPAM_SOURCES
    )


def select(source: Path) -> list[dict]:
    records = [
        json.loads(line) for line in source.read_text().splitlines() if line.strip()
    ]
    pool = [
        r
        for r in records
        if r.get("origin_type") in _ELIGIBLE_ORIGINS
        and r.get("source_dataset") not in _EXCLUDED_SOURCES
    ]
    by_cat: dict[str, list[dict]] = collections.defaultdict(list)
    for r in pool:
        by_cat[r["category"]].append(r)

    rng = random.Random(SEED)
    selected: list[dict] = []
    for category, recs in sorted(by_cat.items()):
        recs = sorted(recs, key=lambda r: r["id"])  # stable, deterministic
        special = [r for r in recs if r.get("is_phishing") or _is_spam_source(r)]
        normal = [r for r in recs if not (r.get("is_phishing") or _is_spam_source(r))]
        rng.shuffle(special)
        rng.shuffle(normal)
        selected.extend((special + normal)[: _PER_CATEGORY.get(category, 40)])

    selected.sort(key=lambda r: r["id"])
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        required=True,
        help="Path to the vendor mailbox JSONL (not committed; provided by the vendor).",
    )
    parser.add_argument("--out", default=str(OUT_SEED), help="Output seed path.")
    args = parser.parse_args()

    selected = select(Path(args.source))
    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for r in selected:
            f.write(
                json.dumps({k: r.get(k) for k in _KEEP_FIELDS}, ensure_ascii=False)
                + "\n"
            )

    cats = collections.Counter(r["category"] for r in selected)
    print(f"Wrote {len(selected)} records -> {out}")
    print(f"  category: {dict(cats)}")
    print(f"  phishing: {sum(1 for r in selected if r.get('is_phishing'))}")
    print(f"  spam:     {sum(1 for r in selected if _is_spam_source(r))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
