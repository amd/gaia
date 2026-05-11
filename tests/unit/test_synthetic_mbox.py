# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import mailbox
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from tests.fixtures.email.generate_mbox import SEED, TARGET_COUNTS, generate

EMAIL_DIR = Path("tests/fixtures/email")
MBOX_PATH = EMAIL_DIR / "synthetic_inbox.mbox"
GT_PATH = EMAIL_DIR / "ground_truth.json"
GEN_SCRIPT = EMAIL_DIR / "generate_mbox.py"


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


def test_fixture_files_exist() -> None:
    assert GEN_SCRIPT.exists()
    _ensure_generated()
    assert MBOX_PATH.exists()
    assert GT_PATH.exists()


def test_mbox_under_1mb() -> None:
    assert MBOX_PATH.stat().st_size < 1024 * 1024


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
    cmd = [sys.executable, str(GEN_SCRIPT), "--verify"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr


def test_generator_accepts_seed() -> None:
    cmd = [sys.executable, str(GEN_SCRIPT), "--seed", str(SEED)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
