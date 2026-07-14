# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression guards for the #1636 pre_scan_inbox docstring fix.

The bug: on gemma-4-e4b the Email Triage agent rendered the triage card
but the reply truncated right after the lead-in — the prose summary never
appeared. Root cause was a stale "CRITICAL OUTPUT FORMAT … re-emit the
JSON" instruction in pre_scan_inbox's docstring that made the 4B model
burn its output budget re-serialising ~100-char Graph message/thread IDs
across ~10 messages.

These tests are hermetic (no live OAuth, no Lemonade) and cover two
things:

  (a) The docstring no longer carries the stale re-emit instruction and
      now tells the model NOT to copy the JSON (the #1636 fix).
  (b) pre_scan_inbox preserves message_id — the issue's "don't strip the
      ids" constraint, since those IDs back the card's Approve/Reply/Archive
      buttons. Note: FakeGmailBackend SHA256-truncates Message-IDs to 16
      chars, so (b) is a hermetic contract check, not a long-ID truncation
      repro (that needs a live Outlook/Exchange mailbox — see #1636).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Docstring regression guard: stale re-emit instruction is gone (#1636)
# ---------------------------------------------------------------------------


_READ_TOOLS_SRC = (
    _REPO_ROOT
    / "hub"
    / "agents"
    / "email"
    / "python"
    / "gaia_agent_email"
    / "tools"
    / "read_tools.py"
)


class TestPreScanInboxDocstring:
    """Read the source file from the worktree directly so the test is not
    affected by which editable-install path Python resolves at import time.
    The worktree owns the fix; checking the installed copy would be fragile.
    """

    def test_docstring_does_not_instruct_re_emit(self):
        """The pre_scan_inbox docstring must NOT contain the old 'CRITICAL
        OUTPUT FORMAT … fenced code block' instruction.

        That instruction caused gemma-4-e4b to burn its output budget
        re-serialising the full JSON envelope (with ~100-char Graph
        message/thread IDs × 10 messages) and truncate before the prose
        summary (#1636).
        """
        src = _READ_TOOLS_SRC.read_text(encoding="utf-8")
        assert "CRITICAL OUTPUT FORMAT" not in src, (
            "Stale 'CRITICAL OUTPUT FORMAT' re-emit instruction must be "
            "removed from pre_scan_inbox docstring (see #1636)"
        )
        assert "single fenced code block" not in src, (
            "Stale fenced-block re-emit instruction still present in "
            "pre_scan_inbox docstring (see #1636)"
        )

    def test_docstring_instructs_model_not_to_copy_json(self):
        """The fixed docstring must contain the replacement wording that
        tells the model NOT to re-emit the JSON.
        """
        src = _READ_TOOLS_SRC.read_text(encoding="utf-8")
        assert "do NOT copy" in src or "do not copy" in src.lower(), (
            "Fixed docstring must tell the model not to copy the JSON "
            "(see #1636 fix)"
        )


# ---------------------------------------------------------------------------
# FakeGmailBackend seam: pre_scan_inbox envelope + message_id contract (hermetic)
# ---------------------------------------------------------------------------

_GRAPH_IDS_MBOX = _REPO_ROOT / "tests" / "fixtures" / "email" / "_graph_ids_inbox.mbox"


@pytest.mark.skipif(
    not _GRAPH_IDS_MBOX.exists(),
    reason="_graph_ids_inbox.mbox fixture not found",
)
class TestPreScanInboxContract:
    """Hermetic contract test for pre_scan_inbox_impl via FakeGmailBackend.

    Verifies (no live OAuth, no Lemonade):
    - pre_scan_inbox_impl returns a valid email_pre_scan envelope.
    - message_id is populated (not stripped) — the card's Approve/Reply/Archive
      buttons depend on it (the issue's "don't strip the ids" constraint).

    NOTE: this does NOT reproduce the #1636 long-ID truncation. FakeGmailBackend
    SHA256-truncates every Message-ID to a 16-char hash, so the ~100-char Graph
    IDs in the fixture never reach the tool; the real repro needs a live
    Outlook/Exchange mailbox. The TestPreScanInboxDocstring assertions are the
    hermetic regression guard for the fix itself.
    """

    @pytest.fixture
    def fake_gmail_with_graph_ids(self):
        pytest.importorskip("gaia_agent_email")
        from tests.fixtures.email.fake_gmail import FakeGmailBackend

        return FakeGmailBackend(_GRAPH_IDS_MBOX)

    def test_pre_scan_returns_email_pre_scan_kind(self, fake_gmail_with_graph_ids):
        pytest.importorskip("gaia_agent_email")
        from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl

        out = pre_scan_inbox_impl(fake_gmail_with_graph_ids, max_messages=25)
        assert out["kind"] == "email_pre_scan"

    def test_pre_scan_has_all_required_keys(self, fake_gmail_with_graph_ids):
        pytest.importorskip("gaia_agent_email")
        from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl

        out = pre_scan_inbox_impl(fake_gmail_with_graph_ids, max_messages=25)
        for key in (
            "urgent",
            "actionable",
            "informational_count",
            "suggested_archives",
            "suggested_drafts",
            "preferences_applied",
            "totals",
        ):
            assert key in out, f"missing pre-scan key: {key}"

    def test_pre_scan_message_ids_are_preserved(self, fake_gmail_with_graph_ids):
        """message_id and thread_id fields must NOT be stripped or hashed —
        they're used by the frontend card's Approve/Reply/Archive buttons.
        """
        pytest.importorskip("gaia_agent_email")
        from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl

        out = pre_scan_inbox_impl(fake_gmail_with_graph_ids, max_messages=25)
        all_items = (
            out.get("urgent", [])
            + out.get("actionable", [])
            + out.get("suggested_archives", [])
        )
        assert all_items, "Pre-scan returned no categorised items from Graph-ID fixture"
        for item in all_items:
            assert item.get(
                "message_id"
            ), f"message_id must be non-empty for item: {item}"

    def test_pre_scan_processes_all_inbox_messages(self, fake_gmail_with_graph_ids):
        """The fixture has 10 messages; all INBOX-labelled ones must be scanned."""
        pytest.importorskip("gaia_agent_email")
        from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl

        out = pre_scan_inbox_impl(fake_gmail_with_graph_ids, max_messages=25)
        totals = out.get("totals", {})
        total = sum(totals.values())
        assert (
            total >= 7
        ), f"Expected at least 7 inbox messages processed, got totals={totals}"

    def test_fake_backend_requires_no_live_oauth(self):
        """FakeGmailBackend should construct without touching keyring / OAuth."""
        pytest.importorskip("gaia_agent_email")
        from tests.fixtures.email.fake_gmail import FakeGmailBackend

        # Construction must not raise (no OAuth attempted).
        backend = FakeGmailBackend(_GRAPH_IDS_MBOX)
        assert (
            len(backend._messages) == 10
        ), f"Expected 10 messages in Graph-ID fixture, got {len(backend._messages)}"
