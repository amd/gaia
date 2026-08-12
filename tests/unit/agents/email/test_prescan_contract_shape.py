# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Failing acceptance tests for the pre-scan contract bump (#2584).

Planned, additive changes to ``gaia_agent_email.contract`` (frozen
request/response models shared by REST + MCP):

- ``PreScanTotals`` gains ``needs_review: int`` (default 0).
- A new model ``MailboxError`` with fields ``mailbox: str`` and ``error: str``.
- ``EmailPreScanResult`` gains ``needs_review: List[PreScanItem]`` (default
  ``[]``), ``scanned: int`` (default 0), ``total_unread: Optional[int]``
  (default ``None``), ``degraded: bool`` (default ``False``), and
  ``mailbox_errors: Optional[List[MailboxError]]`` (default ``None``).

Every model in ``contract.py`` sets ``model_config = ConfigDict(extra="forbid")``,
so an unknown field is a loud ``ValidationError`` -- these tests also confirm
the new models keep that fail-loudly contract.

The whole module is expected to fail at IMPORT time against today's code:
``MailboxError`` does not exist yet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

# NOTE: MailboxError does not exist yet against today's code -- this import
# is expected to raise ImportError, the RED signal for this whole module.
from gaia_agent_email.contract import (  # noqa: E402
    EmailPreScanResult,
    MailboxError,
    PreScanItem,
    PreScanTotals,
)


class TestPreScanTotalsNeedsReview:
    def test_needs_review_field_defaults_to_zero(self):
        totals = PreScanTotals()
        assert totals.needs_review == 0

    def test_needs_review_field_accepts_an_int(self):
        totals = PreScanTotals(needs_review=7)
        assert totals.needs_review == 7


class TestMailboxErrorModel:
    def test_mailbox_error_holds_mailbox_and_error(self):
        me = MailboxError(mailbox="microsoft", error="token expired")
        assert me.mailbox == "microsoft"
        assert me.error == "token expired"

    def test_mailbox_error_forbids_unknown_fields(self):
        with pytest.raises(ValidationError):
            MailboxError(mailbox="microsoft", error="x", extra_field="nope")


class TestEmailPreScanResultNewFields:
    def test_new_fields_have_documented_defaults(self):
        result = EmailPreScanResult()
        assert result.needs_review == []
        assert result.scanned == 0
        assert result.total_unread is None
        assert result.degraded is False
        assert result.mailbox_errors is None

    def test_needs_review_accepts_prescan_items(self):
        item = PreScanItem(
            message_id="m1",
            thread_id="t1",
            sender="a@example.com",
            subject="hi",
            why="heuristic unconfident",
        )
        result = EmailPreScanResult(needs_review=[item])
        assert result.needs_review[0].message_id == "m1"

    def test_mailbox_errors_accepts_a_list_of_mailbox_error(self):
        result = EmailPreScanResult(
            mailbox_errors=[MailboxError(mailbox="microsoft", error="down")]
        )
        assert result.mailbox_errors[0].mailbox == "microsoft"

    def test_scanned_and_total_unread_and_degraded_are_settable(self):
        result = EmailPreScanResult(scanned=25, total_unread=12, degraded=True)
        assert result.scanned == 25
        assert result.total_unread == 12
        assert result.degraded is True

    def test_result_still_forbids_unknown_fields(self):
        with pytest.raises(ValidationError):
            EmailPreScanResult(totally_bogus_field=True)
