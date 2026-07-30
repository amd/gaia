# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Acceptance tests for the triage-scan pagination fix (#2634).

Bug: ``triage_inbox_impl`` issued exactly one ``list_messages`` call and
never followed ``nextPageToken``, so raising ``max_messages`` above one
provider page never widened scan coverage -- ask for 500 and get 100.
Worse, ``scan_truncated`` (``attention_tools.py``) was computed as
``len(results) >= max_messages``, which reports "not truncated" the
instant a request exceeds one page of real mail -- exactly the moment the
scan is MORE incomplete, not less.

These tests exercise ``_list_all_stubs`` (the extracted pagination loop,
``read_tools.py``) directly against the issue's own ``PagingFake`` --
zero classification mocking needed, per the plan's A2 -- plus an Outlook
verbatim-nextLink check via ``LiveOutlookBackend`` and a fail-loud check
for a mid-pagination backend error.

Per the plan's adversarial reflection (R1c): the shared ``FakeGmailBackend``
fixture (used by ~13 test files) is deliberately NOT taught pagination
here. ``PagingFake`` and small test-local doubles cover every acceptance
criterion instead.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.outlook_backend import LiveOutlookBackend  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    _list_all_stubs,
    triage_inbox_impl,
)

from gaia.connectors.errors import ConnectorsError  # noqa: E402

# ---------------------------------------------------------------------------
# PagingFake -- verbatim from the issue body (#2634), mirroring Gmail: it
# honours maxResults up to its own page ceiling, then pages. Extended only
# with a ``calls`` log so tests can assert how many list_messages
# round-trips happened, matching the issue's own "Assertions" checklist.
# ---------------------------------------------------------------------------


class PagingFake:
    """Mirrors Gmail: honours maxResults up to a page ceiling, then pages."""

    PAGE = 100

    def __init__(self, total: int = 250) -> None:
        self.ids = [f"m{i:03d}" for i in range(total)]
        self.calls: List[Dict[str, Any]] = []

    def list_messages(
        self,
        *,
        label_ids: Optional[List[str]] = None,
        max_results: int = 25,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.calls.append(
            {
                "label_ids": label_ids,
                "max_results": max_results,
                "page_token": page_token,
            }
        )
        start = int(page_token or 0)
        end = min(start + min(max_results, self.PAGE), len(self.ids))
        return {
            "messages": [{"id": i, "threadId": i} for i in self.ids[start:end]],
            "nextPageToken": str(end) if end < len(self.ids) else None,
            "resultSizeEstimate": len(self.ids),
        }


class _RaisingAfterNCallsFake(PagingFake):
    """Like ``PagingFake`` but raises ``ConnectorsError`` once the call
    count reaches ``fail_on_call`` -- proves a mid-pagination backend
    failure propagates instead of returning a silent partial result.
    """

    def __init__(self, total: int = 250, *, fail_on_call: int = 2) -> None:
        super().__init__(total)
        self._fail_on_call = fail_on_call

    def list_messages(self, **kwargs: Any) -> Dict[str, Any]:
        if len(self.calls) + 1 >= self._fail_on_call:
            self.calls.append({"failed": True, **kwargs})
            raise ConnectorsError("simulated mid-pagination backend failure")
        return super().list_messages(**kwargs)


# ---------------------------------------------------------------------------
# The truth table: the issue's own three rows, plus the adversarial
# reflection's fourth row (C1) -- the only one that falsifies a fix which
# still derives scan_truncated from len(results) >= max_messages.
# ---------------------------------------------------------------------------


class TestTruthTable:
    def test_request_200_of_250_scanned_200_truncated_true(self):
        fake = PagingFake(total=250)
        out = _list_all_stubs(fake, label_ids=["INBOX"], max_messages=200)
        assert out["scanned"] == 200
        assert out["scan_truncated"] is True

    def test_request_500_of_250_scanned_250_truncated_false(self):
        """The issue's own regression guard: today's formula reports False
        here for the WRONG reason (100 >= 500 is False by arithmetic
        coincidence on a single unpaginated page, not because the mailbox
        was ever confirmed exhausted)."""
        fake = PagingFake(total=250)
        out = _list_all_stubs(fake, label_ids=["INBOX"], max_messages=500)
        assert out["scanned"] == 250
        assert out["scan_truncated"] is False

    def test_request_50_of_250_scanned_50_truncated_true(self):
        fake = PagingFake(total=250)
        out = _list_all_stubs(fake, label_ids=["INBOX"], max_messages=50)
        assert out["scanned"] == 50
        assert out["scan_truncated"] is True

    def test_request_200_of_exactly_200_scanned_200_truncated_false(self):
        """THE regression-guard row (adversarial reflection C1). Two full
        100-message pages exhaust a 200-message mailbox exactly at the
        ceiling. The naive ``len(results) >= max_messages`` formula
        returns True here (200 >= 200) -- wrong, since nothing was
        missed: the second page's own ``nextPageToken`` is None. Only a
        formula driven by the last page's own cursor gets this right.
        Every other row in this table passes under BOTH formulas -- this
        is the only one that falsifies a fix that paginates correctly but
        leaves the length-only truncation check in place.
        """
        fake = PagingFake(total=200)
        out = _list_all_stubs(fake, label_ids=["INBOX"], max_messages=200)
        assert out["scanned"] == 200
        assert out["scan_truncated"] is False, (
            "scan_truncated must be False when the mailbox is exhausted "
            "exactly at max_messages -- got True, which means the "
            "signal is still driven by len(results) >= max_messages "
            "instead of the backend's own paging cursor"
        )


# ---------------------------------------------------------------------------
# Pagination actually happens (and stops issuing calls once it should)
# ---------------------------------------------------------------------------


class TestPaginationHappens:
    def test_at_least_two_list_messages_calls_for_200_of_250(self):
        fake = PagingFake(total=250)
        _list_all_stubs(fake, label_ids=["INBOX"], max_messages=200)
        assert len(fake.calls) >= 2, (
            f"expected >=2 list_messages calls to collect 200 of 250 "
            f"messages at 100/page; got {len(fake.calls)}"
        )

    def test_single_call_when_mailbox_fits_in_one_page(self):
        """A mailbox smaller than both max_messages and the page ceiling
        must not trigger a second call -- the single-page path stays
        exactly as cheap as before."""
        fake = PagingFake(total=30)
        out = _list_all_stubs(fake, label_ids=["INBOX"], max_messages=100)
        assert len(fake.calls) == 1
        assert out["scanned"] == 30
        assert out["scan_truncated"] is False

    def test_requested_max_results_shrinks_never_a_fixed_page_size(self):
        """Each call requests exactly how many messages are still wanted,
        never a fixed page-size constant -- a fixed constant would also
        regress test_pre_scan_budget_reclaim.py's literal
        max_results_seen == 20 assertion (#2634 C3)."""
        fake = PagingFake(total=250)
        _list_all_stubs(fake, label_ids=["INBOX"], max_messages=150)
        requested = [c["max_results"] for c in fake.calls]
        assert requested == [150, 50], (
            f"expected each call to request the REMAINING budget (150, "
            f"then 50 after the first page's 100 messages), not a fixed "
            f"page-size constant; got {requested}"
        )


# ---------------------------------------------------------------------------
# Dedup across pages (#2634 C2)
# ---------------------------------------------------------------------------


class TestDedupAcrossPages:
    def test_message_reappearing_on_a_later_page_is_counted_once(self):
        """A mailbox has no snapshot isolation -- the same id can
        legitimately reappear on two pages if the mailbox mutates
        mid-scan. A naive concat-across-pages loop would classify and
        count it twice."""

        class _OverlappingPagesFake:
            def __init__(self) -> None:
                self.calls = 0

            def list_messages(
                self, *, label_ids=None, max_results=25, page_token=None
            ):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "messages": [{"id": "m1"}, {"id": "m2"}],
                        "nextPageToken": "p2",
                        "resultSizeEstimate": 3,
                    }
                # Page 2 re-returns m2 (mid-scan mutation) plus one
                # genuinely new id.
                return {
                    "messages": [{"id": "m2"}, {"id": "m3"}],
                    "nextPageToken": None,
                    "resultSizeEstimate": 3,
                }

        fake = _OverlappingPagesFake()
        out = _list_all_stubs(fake, label_ids=["INBOX"], max_messages=10)
        ids = [s["id"] for s in out["stubs"]]
        assert ids == ["m1", "m2", "m3"], f"expected unique ids in order, got {ids}"
        assert out["scanned"] == 3


# ---------------------------------------------------------------------------
# Client-side clamp -- never trust the backend to honour the request
# (#2634 C3, Outlook's continuation ignores max_results entirely)
# ---------------------------------------------------------------------------


class TestClientSideClamp:
    def test_overlarge_page_is_clamped_to_max_messages(self):
        class _IgnoresMaxResultsFake:
            def __init__(self) -> None:
                self.calls: List[int] = []

            def list_messages(
                self, *, label_ids=None, max_results=25, page_token=None
            ):
                self.calls.append(max_results)
                # Always returns 30, regardless of what max_results asked
                # for -- mirrors Outlook's $top-baked-into-page-1 behavior
                # on the continuation link.
                return {
                    "messages": [{"id": f"m{i}"} for i in range(30)],
                    "nextPageToken": None,
                    "resultSizeEstimate": 30,
                }

        fake = _IgnoresMaxResultsFake()
        out = _list_all_stubs(fake, label_ids=["INBOX"], max_messages=10)
        assert len(out["stubs"]) == 10, (
            f"expected the accumulator clamped to max_messages=10 even "
            f"though the backend returned 30; got {len(out['stubs'])}"
        )
        assert out["scanned"] == 10
        assert fake.calls == [10]


# ---------------------------------------------------------------------------
# Outlook: the continuation token is an absolute @odata.nextLink URL and
# must be followed verbatim, never re-derived (#2634 C5)
# ---------------------------------------------------------------------------


class TestOutlookVerbatimContinuation:
    def test_second_call_hits_the_literal_nextlink_from_page_one(self):
        """"Params are not re-derived" is intent, not a test on its own --
        a test could pass on "a second page was fetched" while still
        reconstructing (and corrupting) the query. Assert the literal URL.
        """
        next_link = (
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
            "?$skiptoken=abcDEF123"
        )

        requests_seen: List[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_seen.append(request)
            if str(request.url) == next_link:
                return httpx.Response(
                    200, json={"value": [{"id": "m2", "conversationId": "c2"}]}
                )
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "m1", "conversationId": "c1"}],
                    "@odata.nextLink": next_link,
                },
            )

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        backend = LiveOutlookBackend(lambda: "GRAPH-TOKEN-1", http_client=client)

        out = _list_all_stubs(backend, label_ids=["INBOX"], max_messages=5)

        assert out["scanned"] == 2
        assert [s["id"] for s in out["stubs"]] == ["m1", "m2"]
        assert len(requests_seen) == 2
        assert str(requests_seen[1].url) == next_link, (
            "the second request must hit the literal nextLink URL "
            f"returned by page 1, not a re-derived query; got "
            f"{requests_seen[1].url!r}"
        )
        assert out["scan_truncated"] is False  # page 2 carries no further link


# ---------------------------------------------------------------------------
# Fail-loud: a mid-pagination backend error must never yield a partial
# result (#2634 A1)
# ---------------------------------------------------------------------------


class TestMidPaginationFailureIsNeverSilent:
    def test_failure_on_second_page_propagates_not_a_partial_result(self):
        fake = _RaisingAfterNCallsFake(total=250, fail_on_call=2)
        with pytest.raises(ConnectorsError):
            _list_all_stubs(fake, label_ids=["INBOX"], max_messages=200)
        # Page 1 succeeded (100 messages); page 2 raised. Nothing about
        # that partial success is returned to the caller.
        assert len(fake.calls) == 2

    def test_failure_on_second_page_means_zero_classification_calls(self):
        """triage_inbox_impl collects every stub across every page BEFORE
        classifying any of them, so a page-2 failure means get_message is
        never called at all -- not even for page 1's messages."""

        class _GetMessageRecorder(_RaisingAfterNCallsFake):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.get_message_calls: List[str] = []

            def get_message(self, message_id: str) -> Dict[str, Any]:
                self.get_message_calls.append(message_id)
                raise AssertionError(
                    "get_message must never be called when pagination "
                    "itself fails before classification starts"
                )

        fake = _GetMessageRecorder(total=250, fail_on_call=2)
        with pytest.raises(ConnectorsError):
            triage_inbox_impl(fake, max_messages=200)
        assert fake.get_message_calls == []


# ---------------------------------------------------------------------------
# Wiring check: triage_inbox_impl (not just the extracted helper) actually
# uses the paginated listing end to end.
# ---------------------------------------------------------------------------


class _PagingGmailWithBodies(PagingFake):
    """``PagingFake`` plus a minimal ``get_message`` so the full
    ``triage_inbox_impl`` classify loop can run against it -- proves the
    extracted ``_list_all_stubs`` helper is actually wired into the tool,
    not just correct in isolation."""

    def get_message(self, message_id: str) -> Dict[str, Any]:
        return {
            "id": message_id,
            "threadId": message_id,
            "labelIds": ["INBOX"],
            "snippet": "",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": f"Subject {message_id}"},
                    {"name": "From", "value": "sender@example.com"},
                ],
                "body": {},
            },
        }


class TestTriageInboxImplWiring:
    def test_triage_inbox_impl_scans_past_one_page(self):
        fake = _PagingGmailWithBodies(total=250)
        out = triage_inbox_impl(fake, max_messages=200)
        assert len(out["results"]) == 200
        assert out["scan_truncated"] is True
        assert len(fake.calls) >= 2
