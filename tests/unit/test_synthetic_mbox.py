# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import mailbox
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

# The mbox generator imports the email triage heuristics, which now live in the
# standalone gaia_agent_email hub package — skip when it isn't installed (the
# generic unit-test job doesn't install hub agents).
pytest.importorskip("gaia_agent_email")

from tests.fixtures.email.generate_mbox import (  # noqa: E402
    SEED,
    TARGET_COUNTS,
    generate,
)

EMAIL_DIR = Path("tests/fixtures/email")
MBOX_PATH = EMAIL_DIR / "synthetic_inbox.mbox"
GT_PATH = EMAIL_DIR / "ground_truth.json"
GEN_SCRIPT = EMAIL_DIR / "generate_mbox.py"

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
    """Full ground_truth.json including the ``_meta`` block."""
    _ensure_generated()
    return json.loads(GT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def labels(gt: dict) -> dict:
    """Just the per-message entries (``_meta`` and other ``_`` keys dropped)."""
    return {k: v for k, v in gt.items() if not k.startswith("_")}


def test_fixture_files_exist() -> None:
    assert GEN_SCRIPT.exists()
    _ensure_generated()
    assert MBOX_PATH.exists()
    assert GT_PATH.exists()


def test_mbox_under_1mb() -> None:
    assert MBOX_PATH.stat().st_size < 1024 * 1024


def test_message_count_matches_target(mbox_obj: mailbox.mbox, labels: dict) -> None:
    msg_ids = [msg.get("Message-ID") for msg in mbox_obj if msg.get("Message-ID")]
    assert len(msg_ids) == sum(TARGET_COUNTS.values())
    assert len(labels) == sum(TARGET_COUNTS.values())


def test_category_coverage_and_counts(labels: dict) -> None:
    # Categories use the schema-2.0 five-bucket taxonomy strings (#1615),
    # matching gaia_agent_email.tools.triage_heuristics.ALL_CATEGORIES.
    category_counts = Counter(meta["category"] for meta in labels.values())
    assert category_counts["URGENT"] >= 20
    assert category_counts["NEEDS_RESPONSE"] >= 45
    assert category_counts["FYI"] >= 55
    assert category_counts["PROMOTIONAL"] >= 30

    spam_count = sum(1 for meta in labels.values() if meta["is_spam"])
    phishing_count = sum(1 for meta in labels.values() if meta["is_phishing"])
    assert spam_count >= 20
    assert phishing_count >= 8


def test_ambiguous_messages_have_rationale(labels: dict) -> None:
    ambiguous = [meta for meta in labels.values() if meta["ambiguous"]]
    assert len(ambiguous) >= 15
    assert all(a["rationale"].strip() for a in ambiguous)


def test_malformed_edge_cases_present(mbox_obj: mailbox.mbox) -> None:
    msgs = list(mbox_obj)
    missing_subject = any(msg.get("Subject") is None for msg in msgs)
    missing_from = any(msg.get("From") is None for msg in msgs)
    invalid_date = any(msg.get("Date") == "not-a-real-date" for msg in msgs)
    long_subject = any(
        (msg.get("Subject") or "").count("LongSubject-") > 30 for msg in msgs
    )
    assert missing_subject
    assert missing_from
    assert invalid_date
    assert long_subject


def test_threading_headers_exist(mbox_obj: mailbox.mbox) -> None:
    messages = list(mbox_obj)
    with_reply = [m for m in messages if m.get("In-Reply-To")]
    with_refs = [m for m in messages if m.get("References")]
    assert len(with_reply) >= 10
    assert len(with_refs) >= 10


def test_attachments_and_multipart_coverage(mbox_obj: mailbox.mbox) -> None:
    msgs = list(mbox_obj)
    multipart_count = sum(1 for m in msgs if m.is_multipart())
    attachment_count = 0
    inline_count = 0
    for msg in msgs:
        for part in msg.walk():
            disp = (part.get_content_disposition() or "").lower()
            if disp == "attachment":
                attachment_count += 1
            if disp == "inline":
                inline_count += 1
    assert multipart_count >= 40
    assert attachment_count >= 20
    assert inline_count >= 4


def test_persona_recurrence_range(labels: dict) -> None:
    counts = Counter(meta["sender_persona"] for meta in labels.values())
    recurring = [
        "sarah_chen",
        "alex_kumar",
        "jordan_lee",
        "it_systems",
        "hr_team",
        "maria_santos",
        "devops_bot",
        "newsletter_tech",
        "newsletter_market",
    ]
    for key in recurring:
        # Allows realistic frequency while still guaranteeing recurrence.
        assert 3 <= counts[key] <= 80


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
        # Keys are Gmail-derived ids (16-char hex), NOT raw RFC Message-IDs.
        assert _GMAIL_ID_RE.match(message_id), f"non-gmail-id key: {message_id!r}"
        assert required.issubset(meta.keys())


def test_corpus_vocab_matches_scorer_taxonomy(labels: dict) -> None:
    """Guard against taxonomy drift (#1874): every committed ground-truth label
    must be a valid schema-2.0 category, and the scorer's attention axis must be
    drawn from that same taxonomy. The eval scorer compares case-insensitively,
    so compare on the lower-cased vocabulary.
    """
    from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES

    from gaia.eval.quality_metrics import NEEDS_ATTENTION_CATEGORIES

    taxonomy = {c.lower() for c in ALL_CATEGORIES}
    corpus_vocab = {meta["category"].lower() for meta in labels.values()}
    assert corpus_vocab <= taxonomy, (
        f"corpus labels {sorted(corpus_vocab)} drifted from schema-2.0 taxonomy "
        f"{sorted(taxonomy)} — regenerate ground_truth.json"
    )
    assert NEEDS_ATTENTION_CATEGORIES <= taxonomy, (
        f"scorer attention axis {sorted(NEEDS_ATTENTION_CATEGORIES)} is not a "
        f"subset of the taxonomy {sorted(taxonomy)}"
    )


def test_meta_block_present(gt: dict) -> None:
    assert "_meta" in gt
    meta = gt["_meta"]
    assert meta["fixture"] == "synthetic_inbox.mbox"
    assert meta["fixture_kind"] == "synthetic"
    assert meta["taxonomy"] == [
        "URGENT",
        "NEEDS_RESPONSE",
        "FYI",
        "PROMOTIONAL",
        "PERSONAL",
    ]


def test_generator_determinism_verify_mode() -> None:
    cmd = [sys.executable, str(GEN_SCRIPT), "--verify"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr


def test_generator_accepts_seed() -> None:
    cmd = [sys.executable, str(GEN_SCRIPT), "--seed", str(SEED)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
