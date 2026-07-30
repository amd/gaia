# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2643 — ``FakeGmailBackend`` mirrors the live metadata/batch shape.

The whole point of the fake is that a bug in production code that forgets to
re-fetch a full body before decoding it gets caught HERE, hermetically —
not only against live Gmail. So ``FakeGmailBackend.get_message(...,
format="metadata")`` must actually STRIP ``payload.parts`` / ``body.data``,
not just record the call and hand back the same full message regardless of
what was asked for. ``get_messages_batch`` records ONE transport entry per
chunk (not one per message) so a hermetic benchmark's round-trip count means
the same thing it would against live Gmail.
"""

from __future__ import annotations

import base64

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.gmail_backend import METADATA_SCAN_HEADERS  # noqa: E402
from gaia_agent_email.gmail_backend import decode_message_body  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(msg_id: str, *, body_text: str = "Full body content here.") -> dict:
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": ["INBOX"],
        "snippet": body_text[:80],
        "internalDate": "1700000000000",
        "sizeEstimate": len(body_text),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": f"Subject for {msg_id}"},
                {"name": "From", "value": "sender@example.com"},
                {"name": "List-Unsubscribe", "value": "<mailto:unsub@example.com>"},
                {"name": "X-Custom-Header", "value": "not in the metadata set"},
            ],
            "body": {"size": len(body_text), "data": _b64url(body_text)},
        },
    }


class TestMetadataHeaderSetStaysInSync:
    def test_fake_gmail_header_set_matches_gmail_backend(self):
        """fake_gmail.py deliberately DUPLICATES this constant (see its
        module docstring) rather than importing gaia_agent_email at module
        scope -- this guard is what keeps the duplicate honest."""
        from tests.fixtures.email import fake_gmail

        assert set(fake_gmail._METADATA_SCAN_HEADERS) == set(METADATA_SCAN_HEADERS)


class TestGetMessageMetadataFiltering:
    def test_default_format_returns_the_stored_message_unchanged(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_msg("m1"))
        full = gmail.get_message("m1")
        assert full["payload"]["body"]["data"]
        body, _ = decode_message_body(full["payload"])
        assert body == "Full body content here."

    def test_metadata_format_strips_body_data(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_msg("m1"))
        meta = gmail.get_message("m1", format="metadata")
        assert "data" not in meta["payload"]["body"]
        body, _ = decode_message_body(meta["payload"])
        assert body == "", (
            "a metadata-mode message must decode to an EMPTY body -- a "
            "non-empty result here means a real bug (forgetting to "
            "re-fetch format='full' before decoding) would go undetected"
        )

    def test_metadata_format_keeps_only_the_scan_header_set(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_msg("m1"))
        meta = gmail.get_message("m1", format="metadata")
        names = {h["name"] for h in meta["payload"]["headers"]}
        assert names <= set(METADATA_SCAN_HEADERS)
        assert "X-Custom-Header" not in names
        assert "Subject" in names
        assert "List-Unsubscribe" in names

    def test_metadata_format_keeps_snippet_and_label_ids_and_dates(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_msg("m1"))
        meta = gmail.get_message("m1", format="metadata")
        assert meta["snippet"] == "Full body content here."
        assert meta["labelIds"] == ["INBOX"]
        assert meta["internalDate"] == "1700000000000"
        assert meta["threadId"] == "t-m1"

    def test_transport_records_the_format_requested(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_msg("m1"))
        gmail.get_message("m1", format="metadata")
        gmail.get_message("m1", format="full")
        calls = [c for c in gmail.transport.calls if c[0] == "get_message"]
        assert calls[0][1].get("format") == "metadata"
        assert calls[1][1].get("format") == "full"


class TestGetMessagesBatch:
    def test_batch_returns_every_requested_id(self):
        gmail = FakeGmailBackend()
        for i in range(5):
            gmail.add_message(_msg(f"m{i}"))
        out = gmail.get_messages_batch([f"m{i}" for i in range(5)], format="metadata")
        assert set(out) == {f"m{i}" for i in range(5)}
        for msg in out.values():
            assert "data" not in msg["payload"]["body"]

    def test_batch_records_one_transport_entry_not_one_per_message(self):
        gmail = FakeGmailBackend()
        for i in range(10):
            gmail.add_message(_msg(f"m{i}"))
        gmail.get_messages_batch([f"m{i}" for i in range(10)], format="metadata")
        batch_calls = [c for c in gmail.transport.calls if c[0] == "get_messages_batch"]
        get_message_calls = [c for c in gmail.transport.calls if c[0] == "get_message"]
        assert len(batch_calls) == 1, (
            "10 ids in one get_messages_batch call must record ONE transport "
            f"entry (the round-trip), not {len(batch_calls)}"
        )
        assert get_message_calls == []

    def test_batch_chunks_at_the_same_100_boundary_as_live_gmail(self):
        gmail = FakeGmailBackend()
        ids = [f"m{i}" for i in range(150)]
        for mid in ids:
            gmail.add_message(_msg(mid))
        gmail.get_messages_batch(ids, format="full")
        batch_calls = [c for c in gmail.transport.calls if c[0] == "get_messages_batch"]
        assert len(batch_calls) == 2, (
            "150 ids must chunk into 2 round-trips (100 + 50), matching "
            f"LiveGmailBackend's chunking -- got {len(batch_calls)}"
        )
        sizes = sorted(len(c[1]["message_ids"]) for c in batch_calls)
        assert sizes == [50, 100]

    def test_empty_batch_makes_no_call(self):
        gmail = FakeGmailBackend()
        out = gmail.get_messages_batch([])
        assert out == {}
        assert gmail.transport.calls == []
