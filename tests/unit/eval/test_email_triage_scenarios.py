# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the email_triage eval scenario category.

Covers:
  (a) The email_pre_scan_summary.yaml scenario passes validate_scenario().
  (b) FakeGmailBackend seeded with long Graph-style message IDs returns a
      well-formed pre_scan_inbox envelope — confirming the eval seam works
      without live OAuth.
  (c) The pre_scan_inbox docstring no longer contains the stale "CRITICAL
      OUTPUT FORMAT" re-emit instruction that caused gemma-4-e4b truncation
      (#1636).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.eval.runner import validate_scenario  # noqa: E402

# Resolve from REPO_ROOT so tests work regardless of which editable install
# is active — the installed package's SCENARIOS_DIR may point to main repo.
_SCENARIOS_DIR = _REPO_ROOT / "eval" / "scenarios"
EMAIL_TRIAGE_DIR = _SCENARIOS_DIR / "email_triage"
_PRE_SCAN_SCENARIO = EMAIL_TRIAGE_DIR / "email_pre_scan_summary.yaml"

# ---------------------------------------------------------------------------
# Scenario YAML validation (no LLM needed)
# ---------------------------------------------------------------------------


class TestEmailTriageScenarioYAML:
    def test_scenario_directory_exists(self):
        assert EMAIL_TRIAGE_DIR.exists(), (
            f"email_triage scenario directory not found: {EMAIL_TRIAGE_DIR}"
        )

    def test_pre_scan_summary_scenario_exists(self):
        assert _PRE_SCAN_SCENARIO.exists(), (
            f"email_pre_scan_summary.yaml not found: {_PRE_SCAN_SCENARIO}"
        )

    def test_pre_scan_summary_passes_validate_scenario(self):
        data = yaml.safe_load(_PRE_SCAN_SCENARIO.read_text(encoding="utf-8"))
        # validate_scenario raises ValueError on schema violations.
        validate_scenario(_PRE_SCAN_SCENARIO, data)

    def test_scenario_category_is_email_triage(self):
        data = yaml.safe_load(_PRE_SCAN_SCENARIO.read_text(encoding="utf-8"))
        assert data.get("category") == "email_triage"

    def test_scenario_agent_type_is_email(self):
        data = yaml.safe_load(_PRE_SCAN_SCENARIO.read_text(encoding="utf-8"))
        assert data.get("agent_type") == "email"

    def test_scenario_has_success_criteria_covering_truncation_regression(self):
        data = yaml.safe_load(_PRE_SCAN_SCENARIO.read_text(encoding="utf-8"))
        criteria = data["turns"][0].get("success_criteria", "")
        # Must call out the empty-summary regression explicitly.
        assert "truncat" in criteria.lower() or "dangling" in criteria.lower(), (
            "success_criteria must explicitly cover the truncation regression"
        )
        # Must require a natural-language prose summary.
        assert (
            "natural-language" in criteria.lower()
            or "prose" in criteria.lower()
            or "sentence" in criteria.lower()
        ), "success_criteria must require a prose/sentence summary"

    def test_scenario_id_matches_filename(self):
        data = yaml.safe_load(_PRE_SCAN_SCENARIO.read_text(encoding="utf-8"))
        assert data.get("id") == _PRE_SCAN_SCENARIO.stem


# ---------------------------------------------------------------------------
# Docstring regression guard: stale re-emit instruction is gone (#1636)
# ---------------------------------------------------------------------------


_READ_TOOLS_SRC = (
    _REPO_ROOT
    / "hub"
    / "agents"
    / "python"
    / "email"
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
        assert (
            "your response MUST be a\n            single fenced code block" not in src
        ), (
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
# FakeGmailBackend seam: pre_scan_inbox works with long Graph-style IDs
# ---------------------------------------------------------------------------

_GRAPH_IDS_MBOX = (
    _REPO_ROOT / "tests" / "fixtures" / "email" / "_graph_ids_inbox.mbox"
)


@pytest.mark.skipif(
    not _GRAPH_IDS_MBOX.exists(),
    reason="_graph_ids_inbox.mbox fixture not found",
)
class TestPreScanInboxWithGraphStyleIds:
    """Confirm that pre_scan_inbox_impl works correctly when the mailbox
    contains long Graph-style Message-ID headers (as Outlook/Exchange uses).

    This verifies:
    - The FakeGmailBackend seam works hermetically (no live OAuth).
    - pre_scan_inbox_impl returns a valid email_pre_scan envelope.
    - The message_id / thread_id fields are populated (not stripped).
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
            assert item.get("message_id"), (
                f"message_id must be non-empty for item: {item}"
            )

    def test_pre_scan_processes_all_inbox_messages(self, fake_gmail_with_graph_ids):
        """The fixture has 10 messages; all INBOX-labelled ones must be scanned."""
        pytest.importorskip("gaia_agent_email")
        from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl

        out = pre_scan_inbox_impl(fake_gmail_with_graph_ids, max_messages=25)
        totals = out.get("totals", {})
        total = sum(totals.values())
        assert total >= 7, (
            f"Expected at least 7 inbox messages processed, got totals={totals}"
        )

    def test_fake_backend_requires_no_live_oauth(self):
        """FakeGmailBackend should construct without touching keyring / OAuth."""
        pytest.importorskip("gaia_agent_email")
        from tests.fixtures.email.fake_gmail import FakeGmailBackend

        # Construction must not raise (no OAuth attempted).
        backend = FakeGmailBackend(_GRAPH_IDS_MBOX)
        assert len(backend._messages) == 10, (
            f"Expected 10 messages in Graph-ID fixture, got {len(backend._messages)}"
        )
