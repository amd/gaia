# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import mailbox
from pathlib import Path
from typing import Any, Callable

import pytest

from tests.fixtures.email.generate_mbox import generate

EMAIL_FIXTURE_DIR = Path(__file__).resolve().parent
CHECKIN_MBOX = EMAIL_FIXTURE_DIR / "synthetic_inbox.mbox"
CHECKIN_GT = EMAIL_FIXTURE_DIR / "ground_truth.json"


def _ensure_generated_to(out_mbox: Path, out_gt: Path) -> None:
    """Generate fixtures deterministically into the provided paths."""
    if out_mbox.exists() and out_gt.exists():
        return
    generate(out_mbox, out_gt)


@pytest.fixture(scope="session")
def synthetic_mbox(tmp_path_factory) -> mailbox.mbox:
    """Load the synthetic mbox fixture.

    If checked-in fixtures exist under the fixtures directory, use them.
    Otherwise generate deterministic fixtures into a temporary directory so
    test runs do not mutate the repository working tree.
    """
    if CHECKIN_MBOX.exists() and CHECKIN_GT.exists():
        return mailbox.mbox(str(CHECKIN_MBOX), create=False)

    td = tmp_path_factory.mktemp("email_fixtures")
    out_mbox = td / "synthetic_inbox.mbox"
    out_gt = td / "ground_truth.json"
    _ensure_generated_to(out_mbox, out_gt)
    return mailbox.mbox(str(out_mbox), create=False)


@pytest.fixture(scope="session")
def email_ground_truth(tmp_path_factory) -> dict[str, dict[str, Any]]:
    """Load Message-ID keyed ground truth metadata.

    Mirrors the generation strategy used by ``synthetic_mbox`` so the
    mbox and ground-truth remain paired when generated into a temp dir.
    """
    if CHECKIN_MBOX.exists() and CHECKIN_GT.exists():
        return json.loads(CHECKIN_GT.read_text(encoding="utf-8"))

    td = tmp_path_factory.mktemp("email_fixtures")
    out_mbox = td / "synthetic_inbox.mbox"
    out_gt = td / "ground_truth.json"
    _ensure_generated_to(out_mbox, out_gt)
    return json.loads(out_gt.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def _messages_with_ids(
    synthetic_mbox: mailbox.mbox,
) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for msg in synthetic_mbox:
        msg_id = msg.get("Message-ID")
        if msg_id:
            rows.append((msg_id, msg))
    return rows


@pytest.fixture()
def single_email(
    _messages_with_ids: list[tuple[str, Any]],
    email_ground_truth: dict[str, dict[str, Any]],
) -> Callable[[str], Any]:
    """Return a callable that fetches one message by triage category."""

    def _get(category: str) -> Any:
        for msg_id, msg in _messages_with_ids:
            meta = email_ground_truth.get(msg_id)
            if meta and meta.get("category") == category:
                return msg
        raise KeyError(f"No email found for category: {category}")

    return _get


@pytest.fixture()
def spam_emails(
    _messages_with_ids: list[tuple[str, Any]],
    email_ground_truth: dict[str, dict[str, Any]],
) -> list[Any]:
    """Return all spam/phishing emails for filter testing."""
    out = []
    for msg_id, msg in _messages_with_ids:
        meta = email_ground_truth.get(msg_id)
        if not meta:
            continue
        if meta.get("is_spam") or meta.get("is_phishing"):
            out.append(msg)
    return out


@pytest.fixture()
def ambiguous_emails(
    _messages_with_ids: list[tuple[str, Any]],
    email_ground_truth: dict[str, dict[str, Any]],
) -> list[Any]:
    """Return ambiguous/borderline emails for decision-boundary tests."""
    out = []
    for msg_id, msg in _messages_with_ids:
        meta = email_ground_truth.get(msg_id)
        if meta and meta.get("ambiguous"):
            out.append(msg)
    return out
