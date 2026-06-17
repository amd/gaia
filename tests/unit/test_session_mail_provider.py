# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Session mailbox filter pass-through (#1596 / #1603 Phase 2).

The Agent UI session's ``mail_provider`` is a FILTER: ``None`` (or unset/empty)
means "every connected mailbox". The chat helpers must pass that through to the
email agent untouched — the old ``or "google"`` coercion silently triaged Gmail
for sessions that never picked a provider (or had nothing connected), masking
the fail-loud no-mailbox error and ignoring a connected Outlook.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("gaia.ui._chat_helpers")

from gaia.ui import _chat_helpers
from gaia.ui._chat_helpers import _session_mail_provider


class TestSessionMailProviderPassThrough:
    def test_unset_stays_none(self):
        assert _session_mail_provider({}) is None

    def test_none_stays_none(self):
        assert _session_mail_provider({"mail_provider": None}) is None

    def test_empty_string_normalizes_to_none(self):
        # The frontend may send "" for "no pick" — same semantics as None.
        assert _session_mail_provider({"mail_provider": ""}) is None

    def test_explicit_google_preserved(self):
        assert _session_mail_provider({"mail_provider": "google"}) == "google"

    def test_explicit_microsoft_preserved(self):
        assert _session_mail_provider({"mail_provider": "microsoft"}) == "microsoft"


class TestNoSilentGoogleCoercion:
    def test_chat_helpers_source_has_no_or_google_fallback(self):
        """The 'session.get("mail_provider") or "google"' coercion must be gone.

        Source-level canary: both agent-construction call sites must route
        through _session_mail_provider so None reaches the agent as
        "all connected mailboxes" (fail-loud when none is connected).
        """
        src = inspect.getsource(_chat_helpers)
        assert 'session.get("mail_provider") or "google"' not in src
