# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the synthetic email-triage corpus generator (#1230).

These tests assert the *generator contract* independently of any live
service:

- The corpus is exactly the reconciled size (220 messages).
- Every ground-truth label is one of the five schema-2.0 taxonomy categories
  (URGENT / NEEDS_RESPONSE / FYI / PROMOTIONAL / PERSONAL, #1615) — exact
  strings, matching ``gaia_agent_email.tools.triage_heuristics.ALL_CATEGORIES``.
- The realized per-category counts sum to the total (no message is
  unlabelled or double-counted).
- Every ground-truth entry is schema-well-formed (required fields present
  with the right types).
- The ``_meta`` block is present and well-formed.

The generator is deterministic (fixed seed), so these run against freshly
generated output in a temp dir — they do not depend on the checked-in
fixtures being present.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES

from tests.fixtures.email import generate_mbox as gen

# Required ground-truth fields and their expected python types.
_REQUIRED_FIELDS = {
    "category": str,
    "is_spam": bool,
    "is_phishing": bool,
    "is_thread_root": bool,
    "thread_id": str,
    "has_attachment": bool,
    "ambiguous": bool,
    "rationale": str,
    "sender_persona": str,
}


@pytest.fixture(scope="module")
def generated_corpus():
    """Generate the corpus once into a temp dir and return (mbox, gt)."""
    with tempfile.TemporaryDirectory() as td:
        mbox = Path(td) / "synthetic_inbox.mbox"
        gt = Path(td) / "ground_truth.json"
        gen.generate(mbox, gt, seed=gen.SEED)
        ground_truth = json.loads(gt.read_text())
        yield mbox, ground_truth


def _labels(ground_truth: dict) -> dict:
    """Return only the message entries (drop the ``_meta`` block)."""
    return {k: v for k, v in ground_truth.items() if not k.startswith("_")}


def test_corpus_has_reconciled_total(generated_corpus):
    """Corpus size matches the generator's declared TOTAL_MESSAGES."""
    _, ground_truth = generated_corpus
    labels = _labels(ground_truth)
    assert len(labels) == gen.TOTAL_MESSAGES


def test_every_label_is_valid_taxonomy(generated_corpus):
    """AC3/test-AC: every category is one of the 5 schema-2.0 buckets (#1615),
    exact string — matching the production ``ALL_CATEGORIES``.
    """
    _, ground_truth = generated_corpus
    valid = set(ALL_CATEGORIES)
    assert valid == {"URGENT", "NEEDS_RESPONSE", "FYI", "PROMOTIONAL", "PERSONAL"}
    for msg_id, meta in _labels(ground_truth).items():
        assert (
            meta["category"] in valid
        ), f"{msg_id}: category {meta['category']!r} not in {sorted(valid)}"


def test_category_counts_sum_to_total(generated_corpus):
    """The taxonomy buckets account for every message (no orphan,
    no double-count).
    """
    _, ground_truth = generated_corpus
    labels = _labels(ground_truth)
    counts = {c: 0 for c in ALL_CATEGORIES}
    for meta in labels.values():
        counts[meta["category"]] += 1
    assert sum(counts.values()) == len(labels) == gen.TOTAL_MESSAGES
    # Non-degenerate split: the synthetic corpus must spread across every bucket
    # so per-category accuracy stays meaningful. PERSONAL is now represented
    # (#1437) — all five schema-2.0 buckets must be non-empty.
    populated = {c for c, n in counts.items() if n > 0}
    assert populated >= {
        "URGENT",
        "NEEDS_RESPONSE",
        "FYI",
        "PROMOTIONAL",
        "PERSONAL",
    }, f"degenerate corpus split: {counts}"
    # PERSONAL specifically must have a meaningful sample (#1437 coverage gate).
    assert counts["PERSONAL"] >= 20, f"too few PERSONAL examples: {counts}"


def test_ground_truth_schema_well_formed(generated_corpus):
    """test-AC: every entry has the required fields with correct types."""
    _, ground_truth = generated_corpus
    for msg_id, meta in _labels(ground_truth).items():
        for field, typ in _REQUIRED_FIELDS.items():
            assert field in meta, f"{msg_id}: missing field {field!r}"
            assert isinstance(
                meta[field], typ
            ), f"{msg_id}: field {field!r} is {type(meta[field])}, want {typ}"


def test_meta_block_present_and_well_formed(generated_corpus):
    """The ``_meta`` block documents the fixture provenance + schema
    version so consumers can detect drift.
    """
    _, ground_truth = generated_corpus
    assert "_meta" in ground_truth
    meta = ground_truth["_meta"]
    assert meta["fixture"] == "synthetic_inbox.mbox"
    assert meta["fixture_kind"] == "vendor-derived"
    assert isinstance(meta["schema_version"], int)
