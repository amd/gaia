# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Informational-tail auditability tests (#2633, second half).

Bug: ``pre_scan_inbox_impl`` already builds the full list of messages it
files as informational (id/sender/subject/why) internally, then discards
everything but the count. A user has no way to tell a correctly-filtered
newsletter from a miscategorized message that actually needed a reply — the
bucket has no contents, only a number ("95 informational, not listed").

Fix under test: an additive ``include_informational`` flag threads through
``pre_scan_inbox_impl`` -> ``merge_pre_scan_backends`` so a caller can ask
for the full list instead of just the count, at no extra scan cost (the
list was already computed by the same call). Default is False so the
day-to-day card stays lean; ``informational_count`` is unchanged either way.

Hermetic: FakeGmailBackend only, no classifier wired in (every message here
is heuristically confident via a Gmail system label), no Lemonade.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    merge_pre_scan_backends,
    pre_scan_inbox_impl,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str,
    sender: str,
    label_ids: List[str],
    body: str = "Body text for the fixture message.",
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids),
        "snippet": body[:120],
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"size": len(body), "data": _b64(body)},
        },
        "sizeEstimate": len(body),
    }


def _updates_msg(msg_id: str, subject: str) -> Dict[str, Any]:
    """A confidently-FYI message via Gmail's CATEGORY_UPDATES label — lands
    in ``informational`` with no LLM call needed."""
    return _msg(
        msg_id,
        subject=subject,
        sender=f"Newsletter <news+{msg_id}@example.com>",
        label_ids=["INBOX", "UNREAD", "CATEGORY_UPDATES"],
    )


class TestDefaultOmitsTheList:
    def test_informational_list_empty_by_default(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_updates_msg("m1", "Weekly digest #1"))
        gmail.add_message(_updates_msg("m2", "Weekly digest #2"))

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        assert out["informational"] == []
        assert out["informational_count"] == 2


class TestIncludeInformationalReturnsTheFullList:
    def test_full_list_returned_when_requested(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_updates_msg("m1", "Weekly digest #1"))
        gmail.add_message(_updates_msg("m2", "Weekly digest #2"))

        out = pre_scan_inbox_impl(gmail, max_messages=25, include_informational=True)

        ids = {item["message_id"] for item in out["informational"]}
        assert ids == {"m1", "m2"}
        # Count is identical regardless of the flag -- the flag only
        # controls whether the list is populated, never what's counted.
        assert out["informational_count"] == 2

    def test_returned_items_carry_the_same_row_shape_as_other_sections(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_updates_msg("m1", "Weekly digest #1"))

        out = pre_scan_inbox_impl(gmail, max_messages=25, include_informational=True)

        assert len(out["informational"]) == 1
        item = out["informational"][0]
        assert item["message_id"] == "m1"
        assert item["thread_id"] == "m1"
        assert item["subject"] == "Weekly digest #1"
        assert "why" in item and item["why"]

    def test_informational_list_still_empties_under_category_default_archive(self):
        """When the user has asked to auto-archive FYI mail, the
        informational bucket is genuinely empty (everything moved to
        suggested_archives) -- include_informational=True must not
        resurrect items that were promoted out of the bucket.
        """
        gmail = FakeGmailBackend()
        gmail.add_message(_updates_msg("m1", "Weekly digest #1"))
        prefs = {
            "priority_senders": set(),
            "low_priority_senders": set(),
            "category_defaults": {"FYI": "archive"},
        }

        out = pre_scan_inbox_impl(
            gmail,
            max_messages=25,
            session_preferences=prefs,
            include_informational=True,
        )

        assert out["informational"] == []
        assert out["informational_count"] == 0
        archive_ids = {item["message_id"] for item in out["suggested_archives"]}
        assert "m1" in archive_ids


class TestMergePreScanBackendsPropagatesTheFlag:
    def test_merged_informational_list_tags_each_item_with_its_mailbox(self):
        google = FakeGmailBackend()
        google.add_message(_updates_msg("g1", "Google digest"))
        microsoft = FakeGmailBackend()
        microsoft.add_message(_updates_msg("m1", "Microsoft digest"))

        out = merge_pre_scan_backends(
            {"google": google, "microsoft": microsoft},
            max_messages=25,
            include_informational=True,
        )

        by_id = {item["message_id"]: item for item in out["informational"]}
        assert by_id["g1"]["mailbox"] == "google"
        assert by_id["m1"]["mailbox"] == "microsoft"
        assert out["informational_count"] == 2

    def test_merged_informational_list_empty_when_flag_omitted(self):
        google = FakeGmailBackend()
        google.add_message(_updates_msg("g1", "Google digest"))

        out = merge_pre_scan_backends({"google": google}, max_messages=25)

        assert out["informational"] == []
        assert out["informational_count"] == 1
