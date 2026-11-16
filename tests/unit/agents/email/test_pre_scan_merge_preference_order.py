# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Sender-preference order survives the multi-mailbox pre-scan merge (#3623).

Each mailbox sorted its own sections by sender preference (#2777), but the
merge concatenated them in connection order and re-capped. A priority sender
in the second mailbox could be cut while neutral mail from the first stayed,
and the archive list kept the first mailbox's priority senders ahead of the
second mailbox's low-priority ones.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.tools import read_tools  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    extract_sender_email,
    merge_pre_scan_backends,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

_MBOX = _REPO_ROOT / "tests" / "fixtures" / "email" / "_stub_inbox.mbox"
_PRIORITY = {"orders@store.example", "boss@company.example"}
_LOW = {"noreply@github.example"}
_PREFS = {
    "priority_senders": _PRIORITY,
    "low_priority_senders": _LOW,
    "category_defaults": {"FYI": "archive"},
}


def _rank(item) -> int:
    sender = extract_sender_email(item["sender"])
    return 0 if sender in _PRIORITY else 2 if sender in _LOW else 1


def _merge():
    return merge_pre_scan_backends(
        {"google": FakeGmailBackend(_MBOX), "microsoft": FakeGmailBackend(_MBOX)},
        max_messages=100,
        session_preferences=_PREFS,
    )


def test_archive_suggestions_put_every_mailboxs_low_priority_senders_first():
    archives = _merge()["suggested_archives"]
    ranks = [_rank(item) for item in archives]
    assert {item["mailbox"] for item in archives} == {"google", "microsoft"}
    assert ranks == sorted(ranks, reverse=True), [
        (item["mailbox"], extract_sender_email(item["sender"])) for item in archives
    ]


def test_actionable_puts_every_mailboxs_priority_senders_first():
    actionable = _merge()["actionable"]
    ranks = [_rank(item) for item in actionable]
    assert 0 in ranks
    assert ranks == sorted(ranks), [
        (item["mailbox"], extract_sender_email(item["sender"])) for item in actionable
    ]


def test_merged_archive_cap_keeps_the_safest_candidates(monkeypatch):
    monkeypatch.setattr(read_tools, "PRE_SCAN_ARCHIVE_CAP", 2)
    archives = _merge()["suggested_archives"]
    assert [extract_sender_email(i["sender"]) for i in archives] == [
        "noreply@github.example",
        "noreply@github.example",
    ]
    assert [i["mailbox"] for i in archives] == ["google", "microsoft"]


def test_sort_tag_never_reaches_the_merged_envelope():
    out = _merge()
    for section in (
        "urgent",
        "actionable",
        "informational",
        "suggested_archives",
        "needs_review",
        "suspicious",
    ):
        for item in out[section]:
            assert "preference_applied" not in item, section
