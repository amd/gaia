# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Failing acceptance tests for the pre-scan budget-reclaim fix (#2584).

Bug: ``merge_pre_scan_backends`` splits ``max_messages`` evenly across ALL
connected mailboxes UP FRONT (``per_backend = max(1, max_messages //
len(backends))``), before any backend has actually been tried. When one
mailbox fails immediately (e.g. a revoked OAuth grant), its share of the
budget is simply lost -- the surviving mailbox still only gets its original
even split, not the reclaimed full allowance.

These tests also lock the planned ``degraded`` field (True whenever at least
one connected mailbox failed to scan, False when every backend succeeded)
and re-confirm the existing "every backend failed -> raise" guard, which
already holds today (non-regression, not a new-behavior assertion).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.tools.read_tools import merge_pre_scan_backends  # noqa: E402

from gaia.connectors.errors import ConnectorsError  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


class _BrokenBackend:
    """A mailbox backend whose very first call raises immediately -- models a
    revoked OAuth grant discovered the moment the scan starts.
    """

    def list_messages(self, **kwargs: Any) -> Any:
        raise ConnectorsError(
            "microsoft: invalid_grant -- token revoked; reconnect Outlook"
        )


# ---------------------------------------------------------------------------
# Budget reclaim: the surviving backend gets the FULL max_messages, not half
# ---------------------------------------------------------------------------


class TestBudgetReclaim:
    def test_surviving_backend_gets_the_full_budget_not_a_per_backend_share(self):
        broken = _BrokenBackend()
        good = FakeGmailBackend()  # empty store; we only inspect the call args

        result = merge_pre_scan_backends(
            {"google": broken, "microsoft": good},
            max_messages=20,
        )

        assert result["kind"] == "email_pre_scan"
        assert any(
            e["mailbox"] == "google" for e in result.get("mailbox_errors", [])
        ), (
            "expected a mailbox_errors entry for the broken 'google' backend; "
            f"got {result.get('mailbox_errors')!r}"
        )

        list_calls = [c for c in good.transport.calls if c[0] == "list_messages"]
        assert list_calls, "the surviving backend was never asked to list messages"
        max_results_seen = list_calls[0][1]["max_results"]
        assert max_results_seen == 20, (
            "the surviving backend must receive the FULL max_messages budget "
            "after the other backend failed (budget reclaim); got "
            f"max_results={max_results_seen} (expected 20, not an even "
            "per-backend split of 10)"
        )


# ---------------------------------------------------------------------------
# Non-regression guard: every backend failing must still raise loudly.
# This already holds today (read_tools.py:1279-1285) -- it is included here
# to lock the behavior, not to encode new functionality.
# ---------------------------------------------------------------------------


class TestAllBackendsFailingGuard:
    def test_all_backends_failing_still_raises_connectorserror(self):
        broken1 = _BrokenBackend()
        broken2 = _BrokenBackend()
        with pytest.raises(ConnectorsError):
            merge_pre_scan_backends(
                {"google": broken1, "microsoft": broken2}, max_messages=20
            )


# ---------------------------------------------------------------------------
# degraded: honest signal for "at least one mailbox could not be scanned"
# ---------------------------------------------------------------------------


class TestDegradedField:
    def test_degraded_true_when_one_backend_fails(self):
        broken = _BrokenBackend()
        good = FakeGmailBackend()
        result = merge_pre_scan_backends(
            {"google": broken, "microsoft": good}, max_messages=20
        )
        assert result["degraded"] is True

    def test_degraded_false_when_all_backends_succeed(self):
        good1 = FakeGmailBackend()
        good2 = FakeGmailBackend()
        result = merge_pre_scan_backends(
            {"google": good1, "microsoft": good2}, max_messages=20
        )
        assert result["degraded"] is False
