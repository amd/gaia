# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Structural + determinism tests for the email-triage corpus.

The corpus is **vendor-derived** (built by ``generate_mbox.py`` from
``vendor_corpus_seed.jsonl``, a balanced subset of the vendor's labelled mailbox
dataset). These tests assert what must hold regardless of source: size matches
the seed, the labels use the schema-2.0 taxonomy, every category (incl. PERSONAL)
is represented, the spam/phishing axes are non-empty, the ground-truth schema is
intact, the keys are Gmail-derived ids, and the build is deterministic. Synthesis
fidelity (malformed edge-cases, threading, attachments) no longer applies — the
emails are real labelled mail, not generated.
"""

from __future__ import annotations

import json
import mailbox
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

# The builder imports the email triage heuristics, which live in the standalone
# gaia_agent_email hub package — skip when it isn't installed.
pytest.importorskip("gaia_agent_email")

from tests.fixtures.email.generate_mbox import (  # noqa: E402
    SEED,
    TOTAL_MESSAGES,
    generate,
)

EMAIL_DIR = Path("tests/fixtures/email")
MBOX_PATH = EMAIL_DIR / "synthetic_inbox.mbox"
GT_PATH = EMAIL_DIR / "ground_truth.json"
GEN_SCRIPT = EMAIL_DIR / "generate_mbox.py"
SEED_JSONL = EMAIL_DIR / "vendor_corpus_seed.jsonl"

# ground_truth.json is keyed by the Gmail-derived id (sha256(Message-ID)[:16]),
# a 16-char lowercase hex string, so it aligns 1:1 with FakeGmailBackend.
_GMAIL_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _ensure_generated() -> None:
    if MBOX_PATH.exists() and GT_PATH.exists():
        return
    generate(MBOX_PATH, GT_PATH)


@pytest.fixture(scope="module")
def mbox_obj() -> mailbox.mbox:
    _ensure_generated()
    return mailbox.mbox(str(MBOX_PATH), create=False)


@pytest.fixture(scope="module")
def gt() -> dict:
    _ensure_generated()
    return json.loads(GT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def labels(gt: dict) -> dict:
    return {k: v for k, v in gt.items() if not k.startswith("_")}


def test_fixture_files_exist() -> None:
    assert GEN_SCRIPT.exists()
    assert SEED_JSONL.exists(), "committed vendor seed must exist"
    _ensure_generated()
    assert MBOX_PATH.exists()
    assert GT_PATH.exists()


def test_mbox_under_1mb() -> None:
    assert MBOX_PATH.stat().st_size < 1024 * 1024


def test_message_count_matches_seed(mbox_obj: mailbox.mbox, labels: dict) -> None:
    msg_ids = [msg.get("Message-ID") for msg in mbox_obj if msg.get("Message-ID")]
    assert len(msg_ids) == TOTAL_MESSAGES
    assert len(labels) == TOTAL_MESSAGES


def test_category_coverage_and_counts(labels: dict) -> None:
    # schema-2.0 five-bucket taxonomy; the subset is balanced so each bucket has
    # a meaningful sample, including PERSONAL (#1437 — the coverage this corpus
    # was switched to the vendor dataset to provide).
    category_counts = Counter(meta["category"] for meta in labels.values())
    for bucket in ("URGENT", "NEEDS_RESPONSE", "FYI", "PROMOTIONAL", "PERSONAL"):
        assert (
            category_counts[bucket] >= 20
        ), f"{bucket} under-represented: {category_counts}"

    spam_count = sum(1 for meta in labels.values() if meta["is_spam"])
    phishing_count = sum(1 for meta in labels.values() if meta["is_phishing"])
    # Genuine spam = only records with promotional_subtype=="spam" (7 in the
    # committed seed). The old >=20 threshold was set against the over-broad
    # ground-truth that swept spamassassin/ling_spam HAM into is_spam=True (#1904).
    assert spam_count >= 1, "spam axis must be non-empty"
    # All spam emails must be PROMOTIONAL — is_spam can only be True for promotional
    # records whose vendor label is promotional_subtype="spam".
    spam_categories = {meta["category"] for meta in labels.values() if meta["is_spam"]}
    assert spam_categories <= {
        "PROMOTIONAL"
    }, f"is_spam=True on non-PROMOTIONAL emails: {spam_categories - {'PROMOTIONAL'}}"
    assert phishing_count >= 8, "phishing axis must be measurable"


def test_ground_truth_required_fields(labels: dict) -> None:
    required = {
        "category",
        "priority",
        "is_thread_root",
        "thread_id",
        "has_attachment",
        "is_spam",
        "is_phishing",
        "ambiguous",
        "rationale",
        "sender_persona",
    }
    for message_id, meta in labels.items():
        assert _GMAIL_ID_RE.match(message_id), f"non-gmail-id key: {message_id!r}"
        assert required.issubset(meta.keys())


def test_corpus_vocab_matches_scorer_taxonomy(labels: dict) -> None:
    """Guard against taxonomy drift (#1874): every committed label must be a valid
    schema-2.0 category, and the scorer's attention axis must be drawn from it."""
    from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES

    from gaia.eval.quality_metrics import NEEDS_ATTENTION_CATEGORIES

    taxonomy = {c.lower() for c in ALL_CATEGORIES}
    corpus_vocab = {meta["category"].lower() for meta in labels.values()}
    assert corpus_vocab <= taxonomy, (
        f"corpus labels {sorted(corpus_vocab)} drifted from taxonomy "
        f"{sorted(taxonomy)} — regenerate ground_truth.json"
    )
    assert NEEDS_ATTENTION_CATEGORIES <= taxonomy


def test_meta_block_present(gt: dict) -> None:
    assert "_meta" in gt
    meta = gt["_meta"]
    assert meta["fixture"] == "synthetic_inbox.mbox"
    assert meta["fixture_kind"] == "vendor-derived"
    assert meta["taxonomy"] == [
        "URGENT",
        "NEEDS_RESPONSE",
        "FYI",
        "PROMOTIONAL",
        "PERSONAL",
    ]


def test_generator_determinism_verify_mode() -> None:
    proc = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--verify"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr


def test_generator_accepts_seed() -> None:
    proc = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--seed", str(SEED)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
