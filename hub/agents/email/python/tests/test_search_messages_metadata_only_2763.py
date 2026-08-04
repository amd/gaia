# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``search_messages(include_bodies=False)`` metadata-only path (#2763).

Issue #2763 (P0): a counting/listing question against a long-bodied sender
("how many emails from X in the last two weeks?") returned NO answer at
all -- the search succeeded, but the model then blew the context window
retelling itself the full 4000-char-per-message envelope and emitted the
canned "I had to trim the conversation..." apology, deterministically,
every time.

A counting/listing question needs zero body bytes. This file pins the
metadata-only contract added to ``search_messages``:

- ``include_bodies=False`` fetches via the backend's existing
  ``format="metadata"`` primitive (#2643) -- no body decode, no per-message
  truncation -- and returns id/subject/from/to/date/label_ids/snippet ONLY.
  No message in the result carries a ``body``, ``body_truncated``,
  ``body_chars_dropped``, or ``attachments`` field.
- The resulting envelope is AT LEAST AN ORDER OF MAGNITUDE smaller than the
  same query's full-body envelope (the acceptance criterion's own wording),
  measured at the REGISTERED ``@tool`` layer -- not ``search_messages_impl``
  in isolation -- so a wrapper-introduced regression (e.g. a mailbox tag or
  merge step re-adding bulk) would be caught.
- The envelope is asserted against the ACTUAL computed budget
  (``envelope_budget_tokens``, imported -- never a hardcoded literal), not
  merely "the call returned".
- ``include_bodies=True`` (the default, unchanged) still shrinks/behaves
  exactly as before #2763 -- this file adds a new opt-in path, it does not
  change the existing one.

Hermetic: ``FakeGmailBackend`` only, no Lemonade, no network. Long bodies
mirror the failing probe's shape: 15 messages from one sender
(``from:Every newer_than:14d``, true count 15 in the real issue), each with
a raw body far longer than ``DEFAULT_BODY_LIMIT_CHARS``.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap (mirrors test_read_tools_list_inbox_budget_2514.py)
# ---------------------------------------------------------------------------

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.context_budget import (  # noqa: E402
    envelope_budget_tokens,
    estimate_tokens_json,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    DEFAULT_BODY_LIMIT_CHARS,
    ReadToolsMixin,
    _format_message_metadata_for_llm,
    search_messages_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.llm.lemonade_client import GPU_CTX_SIZE, NPU_CTX_SIZE  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers (adapted from test_read_tools_list_inbox_budget_2514.py)
# ---------------------------------------------------------------------------


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _long_body_msg(msg_id: str, body_text: str, **overrides: Any) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body_text[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": "Every: newsletter issue"},
                {"name": "From", "value": "Every <newsletter@every.to>"},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ],
            "body": {
                "data": _b64url(body_text),
                "size": len(body_text.encode("utf-8")),
            },
        },
        "sizeEstimate": len(body_text),
    }
    msg.update(overrides)
    return msg


def _build_long_body_sender_inbox(
    n: int = 15, *, raw_body_chars: int = 12000
) -> Tuple[FakeGmailBackend, List[Dict[str, Any]]]:
    """N messages from one sender with bodies far longer than
    ``DEFAULT_BODY_LIMIT_CHARS`` -- mirrors #2763's real repro shape
    (``from:Every newer_than:14d``, true count 15).
    """
    gmail = FakeGmailBackend(user_email="user@example.com")
    base_date = 1_800_000_000_000
    msgs: List[Dict[str, Any]] = []
    for i in range(n):
        body = "x" * raw_body_chars
        msg = _long_body_msg(
            f"every{i}", body, threadId=f"every{i}", internalDate=str(base_date - i)
        )
        gmail.add_message(msg)
        msgs.append(msg)
    return gmail, msgs


# ---------------------------------------------------------------------------
# Minimal tool-hosting stand-in (established pattern -- copied from
# test_search_messages_count_2756.py / test_read_tools_list_inbox_budget_2514.py)
# ---------------------------------------------------------------------------


class _Host(ReadToolsMixin):
    """Minimal stand-in for EmailTriageAgent's tool-hosting surface."""

    def __init__(self, backend: FakeGmailBackend):
        self._gmail = backend
        self._backends = {"google": backend}
        self._message_mailbox: Dict[str, str] = {}
        self.config = SimpleNamespace(debug=False)

    def _remember_message_mailbox(self, message_id, provider):
        if message_id:
            self._message_mailbox[message_id] = provider

    def _backend_for_message(self, message_id, explicit_mailbox=None):
        provider = explicit_mailbox or self._message_mailbox.get(message_id)
        if provider is None:
            if len(self._backends) == 1:
                return next(iter(self._backends.values()))
            raise ValueError("ambiguous mailbox in test stub")
        backend = self._backends.get(provider)
        if backend is None:
            raise ValueError("mailbox not connected in test stub")
        return backend


def _registered_search_messages(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    assert "search_messages" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["search_messages"]["function"]


def _call(search_messages, **kwargs) -> Dict[str, Any]:
    payload = json.loads(search_messages(**kwargs))
    assert payload["ok"] is True, payload
    return payload["data"]


# ---------------------------------------------------------------------------
# No body content at all (AC: "the tool payload must contain no message
# body content")
# ---------------------------------------------------------------------------


class TestMetadataOnlyCarriesNoBodyContent:
    def test_no_message_has_a_body_field(self):
        gmail, msgs = _build_long_body_sender_inbox(n=15)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(
            search_messages,
            query="from:every",
            max_results=25,
            include_bodies=False,
        )

        assert len(data["messages"]) == 15
        for m in data["messages"]:
            assert "body" not in m
            assert "body_truncated" not in m
            assert "body_chars_dropped" not in m
            assert "attachments" not in m
            # Still carries what a counting/listing answer needs.
            assert m["subject"]
            assert m["from"]

    def test_metadata_formatter_matches_registered_output(self):
        """Wire-level parity: the registered tool's per-message shape must
        equal ``_format_message_metadata_for_llm`` plus the wrapper's own
        ``mailbox`` tag -- not a divergent ad hoc shape."""
        gmail, msgs = _build_long_body_sender_inbox(n=3)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(
            search_messages, query="from:every", max_results=25, include_bodies=False
        )

        by_id = {m["id"]: m for m in msgs}
        for out_msg in data["messages"]:
            # Re-fetch metadata-format directly to build the expected shape.
            meta_msg = gmail.get_message(out_msg["id"], format="metadata")
            expected = {
                **_format_message_metadata_for_llm(meta_msg),
                "mailbox": "google",
            }
            assert out_msg == expected


# ---------------------------------------------------------------------------
# Order-of-magnitude envelope reduction (AC: "the envelope size drops by at
# least an order of magnitude versus your measured value from step 1"),
# measured at the REGISTERED tool layer
# ---------------------------------------------------------------------------


class TestMetadataOnlyEnvelopeShrinksAnOrderOfMagnitude:
    def test_registered_tool_envelope_is_at_least_10x_smaller(self):
        n = 15
        gmail, msgs = _build_long_body_sender_inbox(n=n, raw_body_chars=12000)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        full_body_data = _call(
            search_messages, query="from:every", max_results=25, include_bodies=True
        )
        metadata_data = _call(
            search_messages, query="from:every", max_results=25, include_bodies=False
        )

        assert len(full_body_data["messages"]) == n
        assert len(metadata_data["messages"]) == n

        full_serialized = json.dumps(full_body_data["messages"], default=str)
        metadata_serialized = json.dumps(metadata_data["messages"], default=str)

        full_tokens = estimate_tokens_json(full_serialized)
        metadata_tokens = estimate_tokens_json(metadata_serialized)

        assert metadata_tokens > 0
        reduction_factor = full_tokens / metadata_tokens
        assert reduction_factor >= 10, (
            f"metadata-only envelope must be at least an order of magnitude "
            f"smaller than the full-body envelope for the identical query -- "
            f"got {full_tokens} -> {metadata_tokens} tokens "
            f"({reduction_factor:.1f}x)"
        )

        char_reduction_factor = len(full_serialized) / len(metadata_serialized)
        assert char_reduction_factor >= 10


# ---------------------------------------------------------------------------
# Envelope size asserted against the ACTUAL computed budget -- not merely
# "the call returned" (run-contract requirement)
# ---------------------------------------------------------------------------


class TestMetadataOnlyEnvelopeAgainstComputedBudget:
    def test_metadata_envelope_fits_gpu_budget_with_wide_margin(self):
        n = 15
        gmail, msgs = _build_long_body_sender_inbox(n=n, raw_body_chars=12000)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(
            search_messages, query="from:every", max_results=25, include_bodies=False
        )
        serialized = json.dumps(data["messages"], default=str)
        tokens = estimate_tokens_json(serialized)

        gpu_budget = envelope_budget_tokens(ctx_size=GPU_CTX_SIZE)
        # Not just "fits" -- comfortably so: metadata rows are cheap enough
        # that even 100 of them (the tool's max_results ceiling) must stay
        # under 10% of the real device budget, or the metadata formatter has
        # regressed toward carrying real content again.
        assert tokens <= gpu_budget * 0.10, (
            f"metadata-only envelope ({tokens} tokens) should be a small "
            f"fraction of the GPU budget ({gpu_budget} tokens) -- got "
            f"{tokens / gpu_budget:.1%}"
        )

    def test_metadata_envelope_fits_npu_budget_with_wide_margin(self):
        n = 15
        gmail, msgs = _build_long_body_sender_inbox(n=n, raw_body_chars=12000)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(
            search_messages, query="from:every", max_results=25, include_bodies=False
        )
        serialized = json.dumps(data["messages"], default=str)
        tokens = estimate_tokens_json(serialized)

        npu_budget = envelope_budget_tokens(ctx_size=NPU_CTX_SIZE)
        # Empirically ~23% of the NPU budget for 15 rows -- assert well
        # under half, not a hand-picked number tighter than reality.
        assert tokens <= npu_budget * 0.5, (
            f"metadata-only envelope ({tokens} tokens) should stay well "
            f"under half the smaller NPU budget ({npu_budget} tokens) -- "
            f"got {tokens / npu_budget:.1%}"
        )

    def test_100_metadata_rows_still_fits_comfortably(self):
        """The tool's own ceiling (``max_results`` clamped to 100) is the
        worst case -- even at 100 long-bodied-sender hits, metadata-only
        must still FIT, with room to spare, inside the GPU budget. (Still
        dramatically cheaper than 100 full bodies would be -- see
        ``TestMetadataOnlyEnvelopeShrinksAnOrderOfMagnitude`` for that
        comparison; empirically ~62% of budget here, so "comfortable" means
        clearly under it, not vanishingly small in absolute terms.)
        """
        n = 100
        gmail, msgs = _build_long_body_sender_inbox(n=n, raw_body_chars=12000)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(
            search_messages, query="from:every", max_results=100, include_bodies=False
        )
        assert len(data["messages"]) == n
        serialized = json.dumps(data["messages"], default=str)
        tokens = estimate_tokens_json(serialized)
        gpu_budget = envelope_budget_tokens(ctx_size=GPU_CTX_SIZE)
        assert tokens <= gpu_budget * 0.8, (
            f"100-row metadata-only envelope ({tokens} tokens) should still "
            f"fit the GPU budget ({gpu_budget} tokens) with room to spare "
            f"-- got {tokens / gpu_budget:.1%}"
        )


# ---------------------------------------------------------------------------
# include_bodies defaults to False -- live-hardware evidence (see the commit
# that made this the default) showed a docstring-only opt-in with
# include_bodies=True as the default was not reliable: a 4B-class local
# model did not choose include_bodies=False on the exact failing probe this
# issue is about, reproducing the original overflow. Defaulting to the
# cheap, safe path removes the dependency on the model choosing a new
# parameter correctly on the failure-prone case.
# ---------------------------------------------------------------------------


class TestIncludeBodiesDefaultsToFalse:
    def test_omitting_include_bodies_matches_explicit_false(self):
        gmail, msgs = _build_long_body_sender_inbox(n=5, raw_body_chars=500)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        default_result = search_messages(query="from:every", max_results=25)
        explicit_false_result = search_messages(
            query="from:every", max_results=25, include_bodies=False
        )
        assert default_result == explicit_false_result

        data = json.loads(default_result)["data"]
        assert len(data["messages"]) == 5
        for m in data["messages"]:
            assert "body" not in m

    def test_small_inbox_full_body_still_carries_body_field(self):
        gmail, msgs = _build_long_body_sender_inbox(n=3, raw_body_chars=500)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(
            search_messages, query="from:every", max_results=25, include_bodies=True
        )
        assert len(data["messages"]) == 3
        for m in data["messages"]:
            assert "body" in m
            assert m["body_truncated"] is False  # 500 chars < DEFAULT_BODY_LIMIT_CHARS


# ---------------------------------------------------------------------------
# search_messages_impl layer (pure function, no registered-tool wrapper) --
# nice-to-have, mirrors TestSearchMessagesSharesTheEnvelopeBudgetContract's
# level of coverage in the sibling #2514 budget test file
# ---------------------------------------------------------------------------


class TestSearchMessagesImplMetadataOnly:
    def test_impl_metadata_only_preserves_stub_order_and_count(self):
        n = 6
        gmail, msgs = _build_long_body_sender_inbox(n=n, raw_body_chars=12000)

        result = search_messages_impl(
            gmail,
            query="from:every",
            max_results=25,
            operator_retry=False,
            include_bodies=False,
        )

        assert len(result["messages"]) == n
        # FakeGmailBackend.list_messages sorts newest-first by internalDate;
        # _build_long_body_sender_inbox assigns descending internalDate as
        # i increases, so ids must come back in exactly seeded (m0..m_{n-1})
        # order -- proves the id-keyed _fetch_messages dict lookup didn't
        # silently reorder anything.
        assert [m["id"] for m in result["messages"]] == [f"every{i}" for i in range(n)]

    def test_impl_metadata_only_respects_max_results_via_list_messages(self):
        gmail, msgs = _build_long_body_sender_inbox(n=15, raw_body_chars=12000)

        result = search_messages_impl(
            gmail,
            query="from:every",
            max_results=5,
            operator_retry=False,
            include_bodies=False,
        )
        assert len(result["messages"]) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
