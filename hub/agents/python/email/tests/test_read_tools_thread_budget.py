# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``get_thread`` combined transcript-budget tests for the email agent (#2073).

Acceptance criteria covered (from the accepted plan):

- AC1: a combined transcript budget (reusing ``DEFAULT_THREAD_TRANSCRIPT_CHARS``
  + the per-message floor semantics of ``_format_thread_for_summary``) applies
  to ``get_thread_impl``'s output.
- AC2: over-budget messages are truncated with an explicit ``[truncated]``
  marker; messages are never silently dropped.
- AC3: threads under the budget produce byte-identical output to today (a
  differential test against the current formatter).
- AC4: ``get_thread``'s tool docstring tells the calling LLM results may be
  ``[truncated]``.
- AC5: unit tests cover the fits/over-budget branches and the marker.
- AC6 (capability-matrix drift guard) is NOT covered here — it is already
  owned by ``test_capability_matrix.py``.

TDD split (red/green): the target combined-budget behavior does not exist in
current ``main`` yet — ``get_thread_impl`` unconditionally formats every
message at ``DEFAULT_BODY_LIMIT_CHARS`` with no total-transcript check. So:

- D2, D3, W1, and the first half of DB (the exact-boundary "unchanged" case)
  pin TODAY's behavior and are expected GREEN before the implementation
  lands — under-budget threads already produce output identical to
  per-message-only formatting, since there is no total check to diverge on.
- B1, B2, B3, the second half of DB (the boundary+1 re-format), and S1's
  ``[truncated]``-docstring assertion are the RED half: they encode behavior
  that only exists once the combined budget is implemented, and MUST fail
  against current ``read_tools.py``.

All tests are hermetic: ``FakeGmailBackend`` only, no Lemonade, no network.
Every expected number is derived from the imported module constants
(``DEFAULT_THREAD_TRANSCRIPT_CHARS``, ``THREAD_MIN_PER_MESSAGE_CHARS``,
``DEFAULT_BODY_LIMIT_CHARS``) and the dynamically measured wrapper overhead —
never a hardcoded literal.
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
# Path / import bootstrap
# ---------------------------------------------------------------------------

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    DEFAULT_BODY_LIMIT_CHARS,
    DEFAULT_THREAD_TRANSCRIPT_CHARS,
    THREAD_MIN_PER_MESSAGE_CHARS,
    UNTRUSTED_BODY_CLOSE,
    UNTRUSTED_BODY_OPEN,
    ReadToolsMixin,
    _format_message_for_llm,
    get_thread_impl,
    wrap_untrusted_body,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers (copied/adapted from test_read_tools_body_limit.py)
# ---------------------------------------------------------------------------


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg_with_body(msg_id: str, body_text: str, **overrides: Any) -> Dict[str, Any]:
    """Minimal Gmail API v1 message dict with a single-part text/plain body.

    ``body_text`` should use a whitespace-free filler character (e.g.
    ``"x" * n``) so the production decoder's ``.strip()`` on the decoded
    body is a no-op and the intended length survives round-tripping through
    the fake backend.
    """
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
                {"name": "Subject", "value": "Test"},
                {"name": "From", "value": "alice@example.com"},
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


def _thread_messages(
    bodies: List[str], *, thread_id: str = "t1"
) -> Tuple[FakeGmailBackend, List[Dict[str, Any]]]:
    """Build a ``FakeGmailBackend`` seeded with one thread of messages.

    Messages share ``threadId=thread_id`` so ``FakeGmailBackend.get_thread``
    groups them; insertion order is preserved (dict insertion order), which
    is the order ``get_thread_impl`` must return (it does NOT sort — that is
    ``_format_thread_for_summary``'s job, not this one).

    ``internalDate`` is assigned in DESCENDING order as insertion proceeds
    (m0 newest, m_last oldest) — deliberately the REVERSE of insertion
    order. If a future change to ``get_thread_impl`` accidentally imported
    the chronological (``_thread_message_sort_key``) sort used by
    ``_format_thread_for_summary`` / ``summarize_thread_impl``, these tests'
    insertion-order assertions would flip and fail, catching the regression.
    """
    gmail = FakeGmailBackend(user_email="user@example.com")
    msgs: List[Dict[str, Any]] = []
    base_date = 1_800_000_000_000
    for i, body in enumerate(bodies):
        msg_id = f"m{i}"
        msg = _msg_with_body(
            msg_id,
            body,
            threadId=thread_id,
            internalDate=str(base_date - i),
        )
        gmail.add_message(msg)
        msgs.append(msg)
    return gmail, msgs


def _measure_wrap_overhead() -> int:
    """Dynamically measure the untrusted-body wrapper's per-message overhead.

    ``len(_format_message_for_llm(msg)["body"]) - len(raw_body)`` for a body
    well under the default limit (so it is never truncated). This equals
    ``len(wrap_untrusted_body(""))`` but is derived from the real formatter
    rather than hand-computed from the delimiter constants.
    """
    raw = "x" * 50
    msg = _msg_with_body("overhead-probe", raw, threadId="overhead-probe-thread")
    formatted = _format_message_for_llm(msg)
    assert formatted["body_truncated"] is False
    return len(formatted["body"]) - len(raw)


def _truncated_marker_overhead() -> int:
    """Wrapper overhead PLUS the ``\\n...[truncated]`` marker's own length.

    This is the per-message "worst case" byte cost a truncated message adds
    on top of its (possibly shrunk) body limit — used by B2's soft total
    bound.
    """
    return _measure_wrap_overhead() + len("\n...[truncated]")


# ---------------------------------------------------------------------------
# Minimal tool-hosting stand-in (mirrors EmailTriageAgent's tool surface)
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


def _registered_get_thread(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    assert "get_thread" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["get_thread"]["function"]


# ---------------------------------------------------------------------------
# D2 / D3 — differential ("fits under budget") tests (AC3) — GREEN today
# ---------------------------------------------------------------------------


class TestFitsUnderBudgetDifferential:
    """Threads that fit under the combined budget must format identically
    to plain per-message formatting — both today (no budget exists) and
    after the budget lands (the fits-branch is a no-op)."""

    def test_d2_uniform_bodies_at_fair_share_are_unshrunk(self):
        """6 messages x exactly-4000-char bodies.

        fair_share = max(THREAD_MIN_PER_MESSAGE_CHARS,
        DEFAULT_THREAD_TRANSCRIPT_CHARS // 6) == DEFAULT_BODY_LIMIT_CHARS
        (4000) exactly — NOT strictly less than the default per-message
        limit, so the soft-target "only shrink when fair_share < current
        limit" branch must not fire. Output stays byte-identical to plain
        per-message formatting.
        """
        n = 6
        body = "x" * DEFAULT_BODY_LIMIT_CHARS
        fair_share = max(
            THREAD_MIN_PER_MESSAGE_CHARS, DEFAULT_THREAD_TRANSCRIPT_CHARS // n
        )
        assert fair_share == DEFAULT_BODY_LIMIT_CHARS, (
            "test setup assumption violated: fair_share must equal "
            "DEFAULT_BODY_LIMIT_CHARS for this case to pin the no-shrink branch"
        )

        gmail, msgs = _thread_messages([body] * n)
        result = get_thread_impl(gmail, thread_id="t1")
        expected = [_format_message_for_llm(m) for m in msgs]

        assert result["thread_id"] == "t1"
        assert [m["id"] for m in result["messages"]] == [m["id"] for m in msgs]
        assert result["messages"] == expected
        assert all(m["body_truncated"] is False for m in result["messages"])

    def test_d3_one_large_body_among_many_tiny_ones_is_not_count_blind(self):
        """10 messages: one 4000-char body + nine tiny (10-char) ones.

        The count-based fair_share (24000 // 10 == 2400) is LESS than the
        default per-message limit (4000) — a "count-blind" implementation
        that shrinks based on message count alone (ignoring the actual
        total body size) would incorrectly clip the big body to 2400
        chars. But the actual combined body size here (4000 + 9*10 == 4090,
        plus wrapper overhead) sits far under the 24000 budget, so a
        correct total-aware implementation must NOT shrink anything.
        """
        n = 10
        bodies = ["x" * DEFAULT_BODY_LIMIT_CHARS] + ["x" * 10] * (n - 1)
        fair_share = max(
            THREAD_MIN_PER_MESSAGE_CHARS, DEFAULT_THREAD_TRANSCRIPT_CHARS // n
        )
        assert fair_share < DEFAULT_BODY_LIMIT_CHARS, (
            "test setup assumption violated: fair_share must be strictly "
            "less than the default limit to distinguish count-blind gating "
            "from total-aware gating"
        )

        gmail, msgs = _thread_messages(bodies)
        result = get_thread_impl(gmail, thread_id="t1")
        expected = [_format_message_for_llm(m) for m in msgs]

        assert [m["id"] for m in result["messages"]] == [m["id"] for m in msgs]
        assert result["messages"] == expected
        # The big body must NOT have been clipped to fair_share (2400).
        big = result["messages"][0]
        assert big["body_truncated"] is False
        assert big["body_chars_dropped"] == 0
        assert big["body"] == wrap_untrusted_body("x" * DEFAULT_BODY_LIMIT_CHARS)


# ---------------------------------------------------------------------------
# W1 — wire-level byte identity through the registered tool (AC3) — GREEN
# ---------------------------------------------------------------------------


class TestWireLevelByteIdentity:
    def test_w1_under_budget_thread_matches_envelope_byte_for_byte(self):
        """For an under-budget thread, the tool's raw JSON string output
        must equal ``_envelope_ok({...})`` byte-for-byte — not just
        structurally-equal-after-parsing. Pins AC3's "byte-identical" claim
        at the actual wire, not just dict equality.
        """
        from gaia_agent_email.tools.envelope import _envelope_ok

        n = 10
        bodies = ["x" * DEFAULT_BODY_LIMIT_CHARS] + ["x" * 10] * (n - 1)
        gmail, msgs = _thread_messages(bodies)
        host = _Host(gmail)
        get_thread = _registered_get_thread(host)

        actual = get_thread(thread_id="t1")
        expected = _envelope_ok(
            {
                "thread_id": "t1",
                "messages": [_format_message_for_llm(m) for m in msgs],
            }
        )
        assert actual == expected


# ---------------------------------------------------------------------------
# DB — exact boundary pair (<= vs >) — first half GREEN, second half RED
# ---------------------------------------------------------------------------


def _build_db_thread(*, bump_last_filler_by: int = 0):
    """n=8 skewed-size thread: one 4000-char body + 7 fillers.

    Filler sizes are computed (never hardcoded) so that the WRAPPED body
    total (as measured through ``_format_message_for_llm`` at the default,
    unshrunk limit) lands EXACTLY on ``DEFAULT_THREAD_TRANSCRIPT_CHARS``
    when ``bump_last_filler_by == 0``, and one char over when it is 1 —
    pinning the ``<=`` boundary from both sides.
    """
    overhead = _measure_wrap_overhead()
    n = 8
    big_raw = DEFAULT_BODY_LIMIT_CHARS
    n_fillers = n - 1

    # sum(wrapped) == sum(raw) + n * overhead == DEFAULT_THREAD_TRANSCRIPT_CHARS
    total_raw_target = DEFAULT_THREAD_TRANSCRIPT_CHARS - n * overhead
    filler_raw_total = total_raw_target - big_raw
    assert filler_raw_total > n_fillers, "computed filler budget is unreasonably small"

    base = filler_raw_total // n_fillers
    remainder = filler_raw_total - base * n_fillers
    filler_sizes = [base] * (n_fillers - 1) + [base + remainder]
    assert sum(filler_sizes) == filler_raw_total
    assert all(size > 0 for size in filler_sizes)

    if bump_last_filler_by:
        filler_sizes[-1] += bump_last_filler_by

    bodies = ["x" * big_raw] + ["x" * size for size in filler_sizes]
    gmail, msgs = _thread_messages(bodies)
    return gmail, msgs


class TestExactBoundaryPair:
    def test_db_exact_boundary_output_unchanged(self):
        """Wrapped total sits EXACTLY at DEFAULT_THREAD_TRANSCRIPT_CHARS.

        The gate must be inclusive (``<=``): output stays byte-identical to
        plain per-message formatting. This is TODAY's behavior (no gate
        exists yet, so it trivially matches) and must remain true once the
        gate is implemented.
        """
        gmail, msgs = _build_db_thread(bump_last_filler_by=0)

        # Sanity-check the arithmetic before trusting the assertions below.
        actual_total = sum(len(_format_message_for_llm(m)["body"]) for m in msgs)
        assert actual_total == DEFAULT_THREAD_TRANSCRIPT_CHARS

        result = get_thread_impl(gmail, thread_id="t1")
        expected = [_format_message_for_llm(m) for m in msgs]
        assert result["messages"] == expected
        assert all(m["body_truncated"] is False for m in result["messages"])

    def test_db_boundary_plus_one_triggers_reformat(self):
        """Wrapped total is budget + 1 char (one filler grown by 1 char).

        The gate must fire: the 4000-char body is re-formatted at
        ``DEFAULT_THREAD_TRANSCRIPT_CHARS // 8`` (the count-based
        fair_share for n=8), producing a VISIBLE behavioral difference
        from the exact-boundary case above (pins ``<=`` vs ``<`` from both
        sides). This is the RED half — no such re-format exists today.
        """
        gmail, msgs = _build_db_thread(bump_last_filler_by=1)

        actual_total = sum(len(_format_message_for_llm(m)["body"]) for m in msgs)
        assert actual_total == DEFAULT_THREAD_TRANSCRIPT_CHARS + 1

        n = len(msgs)
        fair_share = max(
            THREAD_MIN_PER_MESSAGE_CHARS, DEFAULT_THREAD_TRANSCRIPT_CHARS // n
        )
        assert fair_share < DEFAULT_BODY_LIMIT_CHARS

        result = get_thread_impl(gmail, thread_id="t1")
        big = result["messages"][0]
        expected_big = _format_message_for_llm(msgs[0], body_limit=fair_share)

        assert big == expected_big
        assert big["body_truncated"] is True
        assert big["body_chars_dropped"] == DEFAULT_BODY_LIMIT_CHARS - fair_share
        assert "...[truncated]" in big["body"]


# ---------------------------------------------------------------------------
# B1 / B2 / B3 / B5 — over-budget / fair-share branch (AC1, AC2, AC5) — RED
# ---------------------------------------------------------------------------


def _build_b1_thread():
    """50 messages x 4000-char raw bodies — well over the combined budget."""
    n = 50
    bodies = ["x" * DEFAULT_BODY_LIMIT_CHARS] * n
    return _thread_messages(bodies)


class TestOverBudgetFairShare:
    def test_b1_every_message_present_and_shrunk_to_fair_share(self):
        """50 messages x 4000-char bodies. fair_share =
        DEFAULT_THREAD_TRANSCRIPT_CHARS // 50. Every message must still be
        present (never dropped), in insertion order, each individually
        equal to ``_format_message_for_llm(m, body_limit=fair_share)``,
        truncated, with the marker inside the untrusted delimiter pair.
        RED: current ``get_thread_impl`` applies no total budget at all.
        """
        n = 50
        fair_share = max(
            THREAD_MIN_PER_MESSAGE_CHARS, DEFAULT_THREAD_TRANSCRIPT_CHARS // n
        )
        assert fair_share == DEFAULT_THREAD_TRANSCRIPT_CHARS // n
        assert fair_share < DEFAULT_BODY_LIMIT_CHARS

        gmail, msgs = _build_b1_thread()
        result = get_thread_impl(gmail, thread_id="t1")

        assert [m["id"] for m in result["messages"]] == [m["id"] for m in msgs]
        assert len(result["messages"]) == n

        expected_dropped = DEFAULT_BODY_LIMIT_CHARS - fair_share
        for out_msg, src_msg in zip(result["messages"], msgs):
            expected = _format_message_for_llm(src_msg, body_limit=fair_share)
            assert out_msg == expected
            assert out_msg["body_truncated"] is True
            assert out_msg["body_chars_dropped"] == expected_dropped
            assert "...[truncated]" in out_msg["body"]
            # The marker must sit INSIDE the untrusted-body delimiter pair,
            # not appended after it (never silently dropped/relocated).
            open_idx = out_msg["body"].find(UNTRUSTED_BODY_OPEN)
            close_idx = out_msg["body"].find(UNTRUSTED_BODY_CLOSE)
            marker_idx = out_msg["body"].find("...[truncated]")
            assert open_idx != -1 and close_idx != -1 and marker_idx != -1
            assert open_idx < marker_idx < close_idx

    def test_b2_combined_total_stays_within_the_soft_bound(self):
        """The documented soft bound is
        ``DEFAULT_THREAD_TRANSCRIPT_CHARS + n * overhead`` (wrapper overhead
        PLUS the truncation marker's own length) — not a hard 24000, since
        the marker itself adds bytes on top of the per-message limit.
        RED: today's (unshrunk) total vastly exceeds this bound.
        """
        n = 50
        gmail, msgs = _build_b1_thread()
        result = get_thread_impl(gmail, thread_id="t1")

        total = sum(len(m["body"]) for m in result["messages"])
        soft_bound = DEFAULT_THREAD_TRANSCRIPT_CHARS + n * _truncated_marker_overhead()
        assert total <= soft_bound

    def test_b3_floor_kicks_in_when_fair_share_would_undercut_it(self):
        """200 messages x 1000-char bodies: count-based fair_share
        (24000 // 200 == 120) is LESS than THREAD_MIN_PER_MESSAGE_CHARS
        (200), so the floor must win — every message formatted at
        ``body_limit=THREAD_MIN_PER_MESSAGE_CHARS``, all 200 present, in
        insertion order. RED: today's code never truncates a 1000-char
        body (it's under the 4000-char default limit).
        """
        n = 200
        body_size = 1000
        fair_share_raw = DEFAULT_THREAD_TRANSCRIPT_CHARS // n
        assert fair_share_raw < THREAD_MIN_PER_MESSAGE_CHARS, (
            "test setup assumption violated: this case must land below the "
            "per-message floor to exercise the floor branch"
        )

        gmail, msgs = _thread_messages(["x" * body_size] * n)
        result = get_thread_impl(gmail, thread_id="t1")

        assert [m["id"] for m in result["messages"]] == [m["id"] for m in msgs]
        assert len(result["messages"]) == n

        expected_dropped = body_size - THREAD_MIN_PER_MESSAGE_CHARS
        for out_msg, src_msg in zip(result["messages"], msgs):
            expected = _format_message_for_llm(
                src_msg, body_limit=THREAD_MIN_PER_MESSAGE_CHARS
            )
            assert out_msg == expected
            assert out_msg["body_truncated"] is True
            assert out_msg["body_chars_dropped"] == expected_dropped

    def test_b5_empty_thread_returns_empty_messages_no_zero_division(self):
        """An empty thread must never raise ``ZeroDivisionError`` when the
        implementation divides the budget by the message count — the
        empty-thread guard must short-circuit before any division.
        """
        gmail = FakeGmailBackend(user_email="user@example.com")
        result = get_thread_impl(gmail, thread_id="does-not-exist")
        assert result["messages"] == []


# ---------------------------------------------------------------------------
# S1 — registered tool surface: docstring + envelope-level truncation (AC4)
# ---------------------------------------------------------------------------


class TestGetThreadToolSurface:
    def test_s1_docstring_mentions_truncated_and_envelope_marks_truncation(self):
        """The registered ``get_thread`` tool's docstring must tell the
        calling LLM results may be ``[truncated]`` (AC4) — RED, today's
        docstring says nothing about truncation. Then, calling the tool on
        an over-budget thread must return an ``ok`` envelope where every
        message reports ``body_truncated is True`` (AC2 at the envelope
        level) — also RED, since no truncation happens today.
        """
        gmail, msgs = _build_b1_thread()
        host = _Host(gmail)
        get_thread = _registered_get_thread(host)
        description = _TOOL_REGISTRY["get_thread"]["description"] or ""

        assert "[truncated]" in description, (
            "get_thread's tool docstring must tell the calling LLM results "
            "may be '[truncated]' (AC4)"
        )

        payload = json.loads(get_thread(thread_id="t1"))
        assert payload["ok"] is True
        assert len(payload["data"]["messages"]) == len(msgs)
        for msg in payload["data"]["messages"]:
            assert msg["body_truncated"] is True
