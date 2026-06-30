# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration test: the committed corpus loads cleanly and its
ground-truth ids align 1:1 with the mbox messages (#1230 test-AC).

This is the contract that the heuristic/LLM triage scoring depends on:
``ground_truth.json`` is keyed by the *Gmail-derived id*
(``sha256(Message-ID)[:16]``) — the same id ``FakeGmailBackend`` produces
when it loads the mbox. If the keys ever drift back to raw RFC
``Message-ID`` headers, every message is silently skipped during scoring
(``results_by_id.get(msg_id)`` -> ``None``), so this test guards against a
silent inert-corpus regression.

No live services required — this runs purely against the checked-in
fixtures and the in-memory fake backend.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytestmark = pytest.mark.integration

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402
from tests.fixtures.email.generate_mbox import (  # noqa: E402
    TOTAL_MESSAGES as _EXPECTED_TOTAL,
)

FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "email"
CORPUS_MBOX = FIXTURES_DIR / "synthetic_inbox.mbox"
GROUND_TRUTH = FIXTURES_DIR / "ground_truth.json"


def _message_labels(ground_truth: dict) -> dict:
    return {k: v for k, v in ground_truth.items() if not k.startswith("_")}


def test_committed_corpus_exists():
    assert CORPUS_MBOX.exists(), f"missing committed corpus: {CORPUS_MBOX}"
    assert GROUND_TRUTH.exists(), f"missing ground truth: {GROUND_TRUTH}"


def test_ground_truth_aligns_1to1_with_mbox():
    """Every loaded message has exactly one GT entry and vice versa — no
    orphans, no dupes, no missing.
    """
    backend = FakeGmailBackend(CORPUS_MBOX)
    loaded_ids = set(backend._messages.keys())  # noqa: SLF001 — test introspection

    ground_truth = json.loads(GROUND_TRUTH.read_text())
    gt_ids = set(_message_labels(ground_truth).keys())

    # No raw-Message-ID keys leaked in (those contain '@' and '<>').
    bad = [k for k in gt_ids if "@" in k or k.startswith("<")]
    assert (
        not bad
    ), f"ground_truth has raw Message-ID keys (must be Gmail ids): {bad[:3]}"

    orphan_gt = gt_ids - loaded_ids
    missing_gt = loaded_ids - gt_ids
    assert (
        not orphan_gt
    ), f"{len(orphan_gt)} GT keys have no mbox message: {sorted(orphan_gt)[:3]}"
    assert (
        not missing_gt
    ), f"{len(missing_gt)} mbox messages have no GT entry: {sorted(missing_gt)[:3]}"
    assert gt_ids == loaded_ids
    assert len(loaded_ids) == _EXPECTED_TOTAL


def test_no_duplicate_messages_in_mbox():
    """Loading the mbox must not collapse two messages onto one id (which
    would mean a Message-ID collision and silent message loss).
    """
    import mailbox

    from tests.fixtures.email.fake_gmail import mbox_message_to_gmail_payload

    box = mailbox.mbox(str(CORPUS_MBOX))
    ids = []
    try:
        for msg in box:
            ids.append(mbox_message_to_gmail_payload(msg)["id"])
    finally:
        box.close()

    assert len(ids) == _EXPECTED_TOTAL
    assert len(set(ids)) == len(ids), "duplicate Gmail ids -> Message-ID collision"
