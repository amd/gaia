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
MBOX_PATH = EMAIL_FIXTURE_DIR / "synthetic_inbox.mbox"
GT_PATH = EMAIL_FIXTURE_DIR / "ground_truth.json"


def _ensure_generated() -> None:
    if MBOX_PATH.exists() and GT_PATH.exists():
        return
    generate(MBOX_PATH, GT_PATH)


@pytest.fixture(scope="session")
def synthetic_mbox() -> mailbox.mbox:
    """Load the pre-built synthetic mbox fixture."""
    _ensure_generated()
    if not MBOX_PATH.exists():
        raise FileNotFoundError(f"Missing fixture: {MBOX_PATH}")
    return mailbox.mbox(str(MBOX_PATH), create=False)


@pytest.fixture(scope="session")
def email_ground_truth() -> dict[str, dict[str, Any]]:
    """Load Message-ID keyed ground truth metadata."""
    _ensure_generated()
    if not GT_PATH.exists():
        raise FileNotFoundError(f"Missing fixture: {GT_PATH}")
    return json.loads(GT_PATH.read_text(encoding="utf-8"))


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
