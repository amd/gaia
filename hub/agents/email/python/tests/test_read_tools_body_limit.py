# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Configurable email body-truncation-limit tests for EmailTriageAgent (#1318).

Acceptance criteria covered (from the accepted plan — the target interface
does not exist in the current code yet; these tests are the red half of
red-green TDD for the follow-up implementation step):

- AC1: an explicit ``body_limit`` override (or the tool-level ``full_body``
  flag) returns the FULL, untruncated body — no "...[truncated]" marker,
  ``body_truncated is False``, ``body_chars_dropped == 0``.
- AC2: truncation metadata is accurate — ``body_truncated`` and the new
  ``body_chars_dropped`` count reflect exactly how many characters were
  dropped (or 0 when nothing was dropped).
- AC3: the default behavior (no explicit limit) is unchanged — a body is
  still truncated at ``DEFAULT_BODY_LIMIT_CHARS`` (4000) chars, including
  the exact-boundary case (a body of exactly 4000 chars is NOT truncated).
- AC4: edge guards — an empty body never crashes and reports zero chars
  dropped; ``full_body=True`` is bounded by a finite ceiling
  (``MAX_FULL_BODY_CHARS`` = 50000), not unbounded; and the module-private
  ``_truncate`` helper fails loudly (``ValueError``) on a non-positive
  limit instead of silently no-op'ing.

All tests are hermetic: FakeGmailBackend only, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

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

# NOTE: ``MAX_FULL_BODY_CHARS`` and the new ``body_limit``/``full_body``
# parameters do not exist on current ``main`` — this import is EXPECTED to
# raise ImportError until the implementation step lands (see module
# docstring). That collection-time failure is the intended "red".
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    DEFAULT_BODY_LIMIT_CHARS,
    MAX_FULL_BODY_CHARS,
    UNTRUSTED_BODY_CLOSE,
    UNTRUSTED_BODY_OPEN,
    ReadToolsMixin,
    _format_message_for_llm,
    _truncate,
    get_message_impl,
    wrap_untrusted_body,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg_with_body(msg_id: str, body_text: str, **overrides: Any) -> Dict[str, Any]:
    """Minimal Gmail API v1 message dict with a single-part text/plain body.

    ``body_text`` should use a whitespace-free filler character (e.g.
    ``"x" * n``) so the production decoder's ``.strip()`` on the decoded
    body is a no-op and the intended length survives round-tripping
    through the fake backend.
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


def _backend_with_body(msg_id: str, body_text: str) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email="user@example.com")
    gmail.add_message(_msg_with_body(msg_id, body_text))
    return gmail


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


def _registered_get_message(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    assert "get_message" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["get_message"]["function"]


# ---------------------------------------------------------------------------
# ``_truncate`` — the arithmetic engine (AC2 / AC3 / AC4)
# ---------------------------------------------------------------------------


class TestTruncateHelper:
    """Unit tests for the module-private ``_truncate(text, limit)`` helper.

    Its return type is changing from ``tuple[str, bool]`` to
    ``tuple[str, int]`` — the actual count of characters dropped, where 0
    means untouched/no truncation. These tests pin the new contract
    directly, independent of the higher-level formatting/tool layers.
    """

    def test_ac3_under_limit_returns_zero_dropped(self):
        text, dropped = _truncate("x" * 100, 4000)
        assert dropped == 0
        assert text == "x" * 100

    def test_ac3_exact_boundary_returns_zero_dropped(self):
        body = "x" * 4000
        text, dropped = _truncate(body, 4000)
        assert dropped == 0
        assert text == body

    def test_ac2_over_limit_reports_exact_dropped_count(self):
        body = "x" * 5000
        text, dropped = _truncate(body, 4000)
        assert dropped == 1000
        assert text == "x" * 4000 + "\n...[truncated]"

    def test_ac4_empty_text_returns_zero_dropped(self):
        text, dropped = _truncate("", 4000)
        assert dropped == 0
        assert text == ""

    def test_ac4_zero_limit_raises_value_error(self):
        with pytest.raises(ValueError):
            _truncate("hello", 0)

    def test_ac4_negative_limit_raises_value_error(self):
        with pytest.raises(ValueError):
            _truncate("hello", -5)


# ---------------------------------------------------------------------------
# ``_format_message_for_llm`` — the new ``body_chars_dropped`` field (AC1-4)
# ---------------------------------------------------------------------------


class TestFormatMessageForLLM:
    """Tests for ``_format_message_for_llm``'s new ``body_chars_dropped`` key."""

    def test_ac1_body_limit_override_returns_full_untruncated(self):
        body = "x" * 5000
        msg = _msg_with_body("m1", body)
        out = _format_message_for_llm(msg, body_limit=MAX_FULL_BODY_CHARS)
        assert out["body"] == wrap_untrusted_body(body)
        assert out["body_truncated"] is False
        assert out["body_chars_dropped"] == 0
        assert "...[truncated]" not in out["body"]

    def test_ac2_default_limit_truncates_and_reports_dropped_count(self):
        body = "x" * 5000
        msg = _msg_with_body("m1", body)
        out = _format_message_for_llm(msg)
        assert out["body_truncated"] is True
        assert out["body_chars_dropped"] == 1000

    def test_ac2_body_under_default_limit_not_truncated(self):
        body = "x" * 100
        msg = _msg_with_body("m1", body)
        out = _format_message_for_llm(msg)
        assert out["body_truncated"] is False
        assert out["body_chars_dropped"] == 0

    def test_ac3_exact_default_boundary_not_truncated(self):
        body = "x" * DEFAULT_BODY_LIMIT_CHARS
        msg = _msg_with_body("m1", body)
        out = _format_message_for_llm(msg)
        assert out["body_truncated"] is False
        assert out["body_chars_dropped"] == 0
        assert out["body"] == wrap_untrusted_body(body)

    def test_ac4_empty_body_no_crash_zero_dropped(self):
        msg = _msg_with_body("m1", "")
        out = _format_message_for_llm(msg)
        assert out["body_chars_dropped"] == 0
        assert out["body_truncated"] is False


# ---------------------------------------------------------------------------
# ``get_message_impl`` — the new ``body_limit`` keyword param (AC1-4)
# ---------------------------------------------------------------------------


class TestGetMessageImpl:
    """Tests for ``get_message_impl``'s new ``body_limit`` keyword param."""

    def test_ac1_body_limit_override_returns_full_untruncated(self):
        body = "x" * 5000
        gmail = _backend_with_body("m1", body)
        out = get_message_impl(gmail, message_id="m1", body_limit=MAX_FULL_BODY_CHARS)
        assert out["body"] == wrap_untrusted_body(body)
        assert out["body_truncated"] is False
        assert out["body_chars_dropped"] == 0
        assert "...[truncated]" not in out["body"]

    def test_ac2_default_reports_accurate_dropped_count(self):
        body = "x" * 5000
        gmail = _backend_with_body("m1", body)
        out = get_message_impl(gmail, message_id="m1")
        assert out["body_truncated"] is True
        assert out["body_chars_dropped"] == 1000

    def test_ac2_body_under_default_limit_not_truncated(self):
        body = "x" * 3000
        gmail = _backend_with_body("m1", body)
        out = get_message_impl(gmail, message_id="m1")
        assert out["body_truncated"] is False
        assert out["body_chars_dropped"] == 0

    def test_ac3_default_truncates_content_at_exactly_4000_chars(self):
        body = "x" * 5000
        gmail = _backend_with_body("m1", body)
        out = get_message_impl(gmail, message_id="m1")
        expected = body[:DEFAULT_BODY_LIMIT_CHARS] + "\n...[truncated]"
        assert out["body"] == wrap_untrusted_body(expected)

    def test_ac3_exact_4000_boundary_not_truncated(self):
        body = "x" * DEFAULT_BODY_LIMIT_CHARS
        gmail = _backend_with_body("m1", body)
        out = get_message_impl(gmail, message_id="m1")
        assert out["body_truncated"] is False
        assert out["body_chars_dropped"] == 0
        assert out["body"] == wrap_untrusted_body(body)

    def test_ac4_empty_body_no_crash(self):
        gmail = _backend_with_body("m1", "")
        out = get_message_impl(gmail, message_id="m1")
        assert out["body_chars_dropped"] == 0
        assert out["body_truncated"] is False


# ---------------------------------------------------------------------------
# Registered ``@tool get_message`` — the new ``full_body`` param (AC1 / AC4)
# ---------------------------------------------------------------------------


class TestGetMessageTool:
    """Tests for the registered ``@tool get_message``'s new ``full_body`` flag."""

    def test_ac1_full_body_true_envelope_returns_untruncated(self):
        body = "x" * 5000
        gmail = _backend_with_body("m1", body)
        host = _Host(gmail)
        get_message = _registered_get_message(host)

        payload = json.loads(get_message(message_id="m1", full_body=True))
        assert payload["ok"] is True
        assert payload["data"]["body_truncated"] is False
        assert payload["data"]["body_chars_dropped"] == 0

    def test_ac3_full_body_omitted_still_uses_default_body_limit(self):
        # full_body defaults to False -> the tool must still map to
        # DEFAULT_BODY_LIMIT_CHARS, i.e. pre-#1318 default behavior is
        # unchanged at the tool surface.
        body = "x" * 5000
        gmail = _backend_with_body("m1", body)
        host = _Host(gmail)
        get_message = _registered_get_message(host)

        payload = json.loads(get_message(message_id="m1"))
        assert payload["ok"] is True
        assert payload["data"]["body_truncated"] is True
        assert payload["data"]["body_chars_dropped"] == 1000

    def test_ac4_full_body_true_still_capped_at_max_full_body_chars(self):
        # Proves full_body=True is bounded by MAX_FULL_BODY_CHARS (50000),
        # not unbounded: a 60000-char body is still truncated, losing
        # exactly 60000 - 50000 = 10000 chars.
        body = "x" * 60_000
        gmail = _backend_with_body("m1", body)
        host = _Host(gmail)
        get_message = _registered_get_message(host)

        payload = json.loads(get_message(message_id="m1", full_body=True))
        assert payload["ok"] is True
        assert payload["data"]["body_truncated"] is True
        assert payload["data"]["body_chars_dropped"] == 10_000


# ---------------------------------------------------------------------------
# Sanity: the untrusted-body delimiters are unchanged by this feature
# ---------------------------------------------------------------------------


class TestUntrustedBodyWrappingUnchanged:
    """#1318 must not alter the untrusted-body wrapper contract itself."""

    def test_wrap_untrusted_body_delimiters_unchanged(self):
        assert UNTRUSTED_BODY_OPEN == "<<<UNTRUSTED_EMAIL_BODY_START>>>"
        assert UNTRUSTED_BODY_CLOSE == "<<<UNTRUSTED_EMAIL_BODY_END>>>"
        assert wrap_untrusted_body("hi") == (
            f"{UNTRUSTED_BODY_OPEN}\nhi\n{UNTRUSTED_BODY_CLOSE}"
        )
