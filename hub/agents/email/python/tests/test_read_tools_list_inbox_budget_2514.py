# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``list_inbox`` / ``search_messages`` combined-envelope budget tests (#2514).

Issue #2514 (P0): ``list_inbox_impl`` loops ``gmail.get_message()`` ->
``_format_message_for_llm(full)`` with NO combined cap -- only a PER-message
``DEFAULT_BODY_LIMIT_CHARS`` truncation. 25 messages of realistic body size
builds a >100KB tool result that overflows the NPU profile's 32768-token
context window on the first tool call of a fresh conversation. The fix must
never silently drop messages (the current N=10-truncated-to-8 bug) -- it
must shrink bodies TOGETHER, or fail loudly if even a minimal shrink can't
fit.

This file pins the target contract for ``list_inbox_impl`` and
``search_messages_impl``:

- Both gain a ``budget_tokens: Optional[int] = None`` kwarg.
- Under budget: output stays byte-identical to today's unbudgeted
  per-message formatting (small inboxes N=3, N=5 unchanged).
- Over budget: every message stays present (never dropped), reformatted at
  one SHARED reduced per-message body limit (never an independent
  per-message choice), floored at ``THREAD_MIN_PER_MESSAGE_CHARS`` (200,
  reused from the ``get_thread`` path -- not a new constant).
- A larger device-profile budget (GPU/CPU, 65536 ctx) must permit less (or
  no) shrinkage than the smaller NPU budget (32768 ctx) for the identical
  input.
- When even the floor can't fit ``max_results`` messages in ``budget_tokens``,
  raise a new ``EnvelopeBudgetExceeded`` (never silently drop messages to
  make the count fit).

TDD split (red/green): none of ``budget_tokens``, the combined-shrink
behavior, or ``EnvelopeBudgetExceeded`` exist on current ``main`` -- every
test in this file is RED against today's ``read_tools.py`` (either a
``TypeError`` from the unrecognized ``budget_tokens`` kwarg, or an
``ImportError`` for ``EnvelopeBudgetExceeded``), except the nice-to-have
wire-level test at the bottom, which is deliberately a non-regression pin
(GREEN today, must stay green).

Fixture numbers were empirically sanity-checked against the REAL
``_format_message_for_llm`` / ``estimate_tokens_json`` before being chosen
(not guessed): 3/5 messages at ``DEFAULT_BODY_LIMIT_CHARS`` fit comfortably
under both device-profile budgets unshrunk; 25 messages overflow both; a
10-message floor-shrunk envelope still needs ~5.8K tokens, far above the
tiny explicit budget the fail-loud tests use. All EXPECTED values in the
assertions below are still derived from imported constants /
runtime-computed budgets, never a hardcoded literal duplicating a
constant's value.

Hermetic: ``FakeGmailBackend`` only, no Lemonade, no network.
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
# Path / import bootstrap (mirrors test_read_tools_thread_budget.py)
# ---------------------------------------------------------------------------

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

# These all exist today -- module-level import is safe. ``budget_tokens`` is
# exercised as a CALL-time kwarg per test below (never imported as a name),
# and ``EnvelopeBudgetExceeded`` (which does NOT exist yet) is imported
# lazily inside only the tests that need it, so a missing symbol there
# doesn't turn every other test in this file into a collection error.
from gaia_agent_email.context_budget import (  # noqa: E402
    envelope_budget_tokens,
    estimate_tokens_json,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    DEFAULT_BODY_LIMIT_CHARS,
    THREAD_MIN_PER_MESSAGE_CHARS,
    ReadToolsMixin,
    _format_message_for_llm,
    list_inbox_impl,
    search_messages_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.llm.lemonade_client import GPU_CTX_SIZE, NPU_CTX_SIZE  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers (adapted from test_read_tools_thread_budget.py)
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


def _inbox_messages(
    bodies: List[str], *, id_prefix: str = "m"
) -> Tuple[FakeGmailBackend, List[Dict[str, Any]]]:
    """Build a ``FakeGmailBackend`` seeded with ``len(bodies)`` distinct
    top-level INBOX messages (one per body) -- unlike ``get_thread``'s
    single-thread grouping, ``list_inbox``/``search_messages`` list across
    the whole INBOX, so each message gets its own id/threadId.

    ``internalDate`` is assigned in DESCENDING order as insertion proceeds
    (m0 newest, m_last oldest), matching ``FakeGmailBackend.list_messages``'s
    newest-first sort -- so the returned ``msgs`` list is already in the same
    order ``list_inbox_impl``/``search_messages_impl`` will return.
    """
    gmail = FakeGmailBackend(user_email="user@example.com")
    msgs: List[Dict[str, Any]] = []
    base_date = 1_800_000_000_000
    for i, body in enumerate(bodies):
        msg_id = f"{id_prefix}{i}"
        msg = _msg_with_body(
            msg_id, body, threadId=msg_id, internalDate=str(base_date - i)
        )
        gmail.add_message(msg)
        msgs.append(msg)
    return gmail, msgs


def _build_over_budget_inbox(n: int) -> Tuple[FakeGmailBackend, List[Dict[str, Any]]]:
    """N messages x exactly ``DEFAULT_BODY_LIMIT_CHARS`` bodies -- the plan's
    canonical over-budget ``list_inbox`` repro shape (issue #2514)."""
    bodies = ["x" * DEFAULT_BODY_LIMIT_CHARS] * n
    return _inbox_messages(bodies)


# ---------------------------------------------------------------------------
# Minimal tool-hosting stand-in (mirrors EmailTriageAgent's tool surface;
# copied from test_read_tools_thread_budget.py's ``_Host``)
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


def _registered_list_inbox(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    assert "list_inbox" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["list_inbox"]["function"]


# ---------------------------------------------------------------------------
# Fits-under-budget / no-shrink-when-headroom-exists (AC: "small inboxes
# N=3, N=5 unchanged") -- RED (budget_tokens not yet an accepted kwarg)
# ---------------------------------------------------------------------------


class TestFitsUnderBudgetAndUntruncated:
    """Small inboxes must format identically to plain per-message formatting
    (byte-for-byte) AND report ``body_truncated=False`` for every message --
    both are the same underlying "no shrink happened" fact, so one test
    class covers both acceptance-criteria framings from the plan."""

    @pytest.mark.parametrize("n", [3, 5])
    def test_small_inbox_is_byte_identical_and_untruncated(self, n):
        bodies = ["x" * DEFAULT_BODY_LIMIT_CHARS for _ in range(n)]
        gmail, msgs = _inbox_messages(bodies)
        npu_budget = envelope_budget_tokens(ctx_size=NPU_CTX_SIZE)

        result = list_inbox_impl(gmail, max_results=n, budget_tokens=npu_budget)

        expected_messages = [_format_message_for_llm(m) for m in msgs]
        assert len(result["messages"]) == n
        assert result["messages"] == expected_messages
        assert all(m["body_truncated"] is False for m in result["messages"])


# ---------------------------------------------------------------------------
# Over-budget / shared-shrink -- the canonical #2514 repro shape -- RED
# ---------------------------------------------------------------------------


class TestOverBudgetSharedShrink:
    """25 messages x exactly ``DEFAULT_BODY_LIMIT_CHARS`` bodies against the
    NPU-profile envelope budget -- the shape of the issue's real-world
    repro: a fresh conversation's first ``list_inbox`` call on a realistic
    mailbox overflowing the NPU's 32768-token context window."""

    def test_all_messages_present_shrunk_to_one_shared_limit_and_fits(self):
        n = 25
        gmail, msgs = _build_over_budget_inbox(n)
        npu_budget = envelope_budget_tokens(ctx_size=NPU_CTX_SIZE)

        result = list_inbox_impl(gmail, max_results=n, budget_tokens=npu_budget)

        # Regression guard -- the issue's reported bug is messages silently
        # vanishing (N=10 truncated to 8). Every seeded message must still
        # be present.
        assert len(result["messages"]) == n

        # Shared shrink: every message truncated to the IDENTICAL limit --
        # never an independent per-message choice.
        dropped_values = {m["body_chars_dropped"] for m in result["messages"]}
        assert all(m["body_truncated"] is True for m in result["messages"])
        assert len(dropped_values) == 1, (
            "every message must shrink to the SAME shared per-message body "
            "limit -- a differing body_chars_dropped means messages were "
            "truncated independently rather than together"
        )
        shared_dropped = dropped_values.pop()
        assert shared_dropped > 0
        shared_limit = DEFAULT_BODY_LIMIT_CHARS - shared_dropped
        assert shared_limit >= THREAD_MIN_PER_MESSAGE_CHARS, (
            "the shared shrunk limit must never go below the reused thread "
            "floor -- THREAD_MIN_PER_MESSAGE_CHARS, not a new constant"
        )

        # Every message must equal formatting the SAME source message at the
        # shared limit -- not just an aggregate count/size check.
        by_id = {m["id"]: m for m in msgs}
        for out_msg in result["messages"]:
            src = by_id[out_msg["id"]]
            expected = _format_message_for_llm(src, body_limit=shared_limit)
            assert out_msg == expected

        # The shrunk envelope must actually fit the budget it was shrunk for.
        serialized = json.dumps({"messages": result["messages"]}, default=str)
        assert estimate_tokens_json(serialized) <= npu_budget


class TestGpuBudgetPermitsLessShrinkageThanNpu:
    def test_gpu_profile_drops_strictly_fewer_chars_than_npu_for_the_same_inbox(self):
        n = 25
        gmail, msgs = _build_over_budget_inbox(n)
        npu_budget = envelope_budget_tokens(ctx_size=NPU_CTX_SIZE)
        gpu_budget = envelope_budget_tokens(ctx_size=GPU_CTX_SIZE)
        assert npu_budget < gpu_budget  # precondition for a meaningful comparison

        npu_result = list_inbox_impl(gmail, max_results=n, budget_tokens=npu_budget)
        gpu_result = list_inbox_impl(gmail, max_results=n, budget_tokens=gpu_budget)

        npu_dropped = npu_result["messages"][0]["body_chars_dropped"]
        gpu_dropped = gpu_result["messages"][0]["body_chars_dropped"]
        assert (
            npu_dropped > 0
        ), "NPU's smaller budget must force shrinkage for this fixture"
        assert gpu_dropped < npu_dropped, (
            "a larger context-profile budget must permit at least as much "
            "per-message body content -- either less shrinkage or none at all"
        )


# ---------------------------------------------------------------------------
# Fail-loud: even the floor can't fit -- RED (EnvelopeBudgetExceeded is new)
# ---------------------------------------------------------------------------


class TestEnvelopeBudgetExceededIsImportable:
    def test_is_a_public_exception_subclass(self):
        from gaia_agent_email.tools.read_tools import EnvelopeBudgetExceeded

        assert issubclass(EnvelopeBudgetExceeded, Exception)


class TestFailLoudWhenFloorStillOverflows:
    def test_list_inbox_raises_when_even_the_floor_cannot_fit(self):
        from gaia_agent_email.tools.read_tools import EnvelopeBudgetExceeded

        n = 10
        gmail, msgs = _build_over_budget_inbox(n)
        # Empirically: 10 messages reformatted at THE FLOOR alone already
        # serialize to ~5.8K estimated tokens -- a budget of 10 is nowhere
        # close, regardless of the implementation's exact accounting.
        tiny_budget = 10

        with pytest.raises(EnvelopeBudgetExceeded) as exc_info:
            list_inbox_impl(gmail, max_results=n, budget_tokens=tiny_budget)

        assert isinstance(exc_info.value, Exception)
        message = str(exc_info.value)
        assert str(tiny_budget) in message
        assert "max_results" in message.lower()


# ---------------------------------------------------------------------------
# search_messages_impl shares the same contract (issue #2514 work item 3:
# check bulk-read siblings for the same unbounded shape) -- RED
# ---------------------------------------------------------------------------


class TestSearchMessagesSharesTheEnvelopeBudgetContract:
    """One over-budget case mirroring ``list_inbox``'s canonical 25-message
    scenario, plus the fail-loud case, is sufficient here -- the full case
    matrix is already covered for ``list_inbox_impl`` above."""

    def test_all_messages_present_and_shrunk_to_a_shared_limit(self):
        n = 25
        gmail, msgs = _build_over_budget_inbox(n)
        npu_budget = envelope_budget_tokens(ctx_size=NPU_CTX_SIZE)

        result = search_messages_impl(
            gmail,
            query="",
            max_results=n,
            budget_tokens=npu_budget,
            operator_retry=False,
        )

        assert len(result["messages"]) == n

        dropped_values = {m["body_chars_dropped"] for m in result["messages"]}
        assert all(m["body_truncated"] is True for m in result["messages"])
        assert len(dropped_values) == 1
        shared_dropped = dropped_values.pop()
        assert shared_dropped > 0
        assert DEFAULT_BODY_LIMIT_CHARS - shared_dropped >= THREAD_MIN_PER_MESSAGE_CHARS

        serialized = json.dumps({"messages": result["messages"]}, default=str)
        assert estimate_tokens_json(serialized) <= npu_budget

    def test_raises_when_even_the_floor_cannot_fit(self):
        from gaia_agent_email.tools.read_tools import EnvelopeBudgetExceeded

        n = 10
        gmail, msgs = _build_over_budget_inbox(n)
        tiny_budget = 10

        with pytest.raises(EnvelopeBudgetExceeded) as exc_info:
            search_messages_impl(
                gmail,
                query="",
                max_results=n,
                budget_tokens=tiny_budget,
                operator_retry=False,
            )
        message = str(exc_info.value)
        assert str(tiny_budget) in message
        assert "max_results" in message.lower()


# ---------------------------------------------------------------------------
# Nice-to-have: registered tool wire-level byte identity for a small inbox.
# Deliberately touches NO budget_tokens wiring (out of scope per the plan --
# the registered @tool wrapper dividing budget across mailboxes is an
# implementation-level defensive addition, not part of this contract), so
# this is expected GREEN both today and after the fix: a safety net proving
# the fix doesn't change small-mailbox output once wired through the full
# tool wrapper, not a new-behavior pin.
# ---------------------------------------------------------------------------


class TestRegisteredListInboxWireLevelByteIdentity:
    def test_small_inbox_wire_output_matches_todays_envelope(self):
        from gaia_agent_email.tools.envelope import _envelope_ok

        n = 3
        bodies = ["x" * DEFAULT_BODY_LIMIT_CHARS for _ in range(n)]
        gmail, msgs = _inbox_messages(bodies)
        host = _Host(gmail)
        list_inbox = _registered_list_inbox(host)

        actual = list_inbox(max_results=n)
        expected = _envelope_ok(
            {
                # The registered wrapper (unlike list_inbox_impl in isolation)
                # tags every message with its source mailbox -- "google", the
                # single key _Host registers its backend under.
                "messages": [
                    {**_format_message_for_llm(m), "mailbox": "google"} for m in msgs
                ],
                "next_page_token": None,
            }
        )
        assert actual == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
