# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import mailbox
import tempfile
from collections import Counter
from pathlib import Path

import pytest

from tests.fixtures.email.generate_mbox import SEED, TARGET_COUNTS, generate

EMAIL_DIR = Path("tests/fixtures/email")
GEN_SCRIPT = EMAIL_DIR / "generate_mbox.py"


def _ensure_generated_to(out_mbox: Path, out_gt: Path) -> None:
    if out_mbox.exists() and out_gt.exists():
        return
    generate(out_mbox, out_gt)


@pytest.fixture(scope="module")
def mbox_obj(tmp_path_factory) -> mailbox.mbox:
    td = tmp_path_factory.mktemp("email_fixtures_unit")
    out_mbox = td / "synthetic_inbox.mbox"
    out_gt = td / "ground_truth.json"
    _ensure_generated_to(out_mbox, out_gt)
    return mailbox.mbox(str(out_mbox), create=False)


@pytest.fixture(scope="module")
def gt(tmp_path_factory) -> dict:
    td = tmp_path_factory.mktemp("email_fixtures_unit")
    out_mbox = td / "synthetic_inbox.mbox"
    out_gt = td / "ground_truth.json"
    _ensure_generated_to(out_mbox, out_gt)
    return json.loads(out_gt.read_text(encoding="utf-8"))


def test_fixture_files_exist(tmp_path_factory) -> None:
    # Generator script should exist and be runnable; we don't require
    # checked-in artifacts — generator may produce fixtures into a tmp dir.
    assert GEN_SCRIPT.exists()
    td = tmp_path_factory.mktemp("email_fixtures_exist")
    out_mbox = td / "synthetic_inbox.mbox"
    out_gt = td / "ground_truth.json"
    _ensure_generated_to(out_mbox, out_gt)
    assert out_mbox.exists()
    assert out_gt.exists()


def test_mbox_under_1mb() -> None:
    # Generate into a temp location and assert output size is under 1 MB
    with tempfile.TemporaryDirectory() as td:
        out_mbox = Path(td) / "synthetic_inbox.mbox"
        out_gt = Path(td) / "ground_truth.json"
        mbox_hash, gt_hash = generate(out_mbox, out_gt, seed=SEED)
        assert out_mbox.stat().st_size < 1024 * 1024


def test_message_count_matches_target(mbox_obj: mailbox.mbox, gt: dict) -> None:
    msg_ids = [msg.get("Message-ID") for msg in mbox_obj if msg.get("Message-ID")]
    assert len(msg_ids) == sum(TARGET_COUNTS.values())
    assert len(gt) == sum(TARGET_COUNTS.values())


def test_category_coverage_and_counts(gt: dict) -> None:
    category_counts = Counter(meta["category"] for meta in gt.values())
    assert category_counts["urgent"] >= 20
    assert category_counts["actionable"] >= 45
    assert category_counts["informational"] >= 55
    assert category_counts["low_priority"] >= 30

    spam_count = sum(1 for meta in gt.values() if meta["is_spam"])
    phishing_count = sum(1 for meta in gt.values() if meta["is_phishing"])
    assert spam_count >= 20
    assert phishing_count >= 8


def test_ambiguous_messages_have_rationale(gt: dict) -> None:
    ambiguous = [meta for meta in gt.values() if meta["ambiguous"]]
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


def test_persona_recurrence_range(gt: dict) -> None:
    counts = Counter(meta["sender_persona"] for meta in gt.values())
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


def test_ground_truth_required_fields(gt: dict) -> None:
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
    for message_id, meta in gt.items():
        assert message_id.startswith("<") and message_id.endswith(">")
        assert required.issubset(meta.keys())


def test_generator_determinism_verify_mode() -> None:
    # Generate twice into independent temp dirs and compare deterministic
    # ground-truth JSON hashes and message-id sets. Raw mbox binary can
    # differ due to stdlib multipart boundary generation, so avoid binary
    # equality checks.
    with tempfile.TemporaryDirectory() as td:
        a_mbox = Path(td) / "a.mbox"
        a_gt = Path(td) / "a_gt.json"
        b_mbox = Path(td) / "b.mbox"
        b_gt = Path(td) / "b_gt.json"
        h1_mbox, h1_gt = generate(a_mbox, a_gt, seed=SEED)
        h2_mbox, h2_gt = generate(b_mbox, b_gt, seed=SEED)
        # Ground-truth JSON should be identical
        assert h1_gt == h2_gt
        # And the set of Message-IDs in each mbox should match
        import mailbox as _mb

        ids1 = {
            m.get("Message-ID") for m in _mb.mbox(str(a_mbox)) if m.get("Message-ID")
        }
        ids2 = {
            m.get("Message-ID") for m in _mb.mbox(str(b_mbox)) if m.get("Message-ID")
        }
        assert ids1 == ids2


def test_generator_accepts_seed() -> None:
    # Ensure generator runs with an explicit seed and produces outputs
    with tempfile.TemporaryDirectory() as td:
        out_mbox = Path(td) / "synthetic_inbox.mbox"
        out_gt = Path(td) / "ground_truth.json"
        mbox_hash, gt_hash = generate(out_mbox, out_gt, seed=SEED)
        assert out_mbox.exists() and out_gt.exists()
        assert len(mbox_hash) == 64 and len(gt_hash) == 64
