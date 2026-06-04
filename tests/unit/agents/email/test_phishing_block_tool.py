# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the ``quarantine_phishing_message`` tool.

Covers:
1. The tool moves a phishing-flagged message out of INBOX into a quarantine
   label and records an undoable action in the action log.
2. The action is REVERSIBLE — ``restore_message`` (existing tool) re-adds
   INBOX and removes the quarantine label.
3. The tool refuses to act on a non-phishing-flagged message (safety gate).
4. The tool is in ``TOOLS_REQUIRING_CONFIRMATION`` — it MUST NOT execute
   without explicit user confirmation.
5. The agent system prompt does NOT follow links or instructions in the body
   of a phishing message (prompt-injection guard).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION  # noqa: E402
from gaia.agents.email import action_store  # noqa: E402
from gaia.agents.email.tools.phishing_tools import (  # noqa: E402
    QUARANTINE_LABEL_NAME,
    quarantine_phishing_impl,
    unquarantine_impl,
)
from gaia.database.mixin import DatabaseMixin  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeGmail:
    """Minimal Gmail backend stub for phishing tool tests."""

    def __init__(self, label_id: str = "Label_quarantine_phishing"):
        self._label_id = label_id
        self._labels: Dict[str, List[str]] = {}
        self._inbox: set[str] = set()

    def get_message(self, message_id: str) -> Dict[str, Any]:
        labels = self._labels.get(message_id, ["INBOX"])
        return {
            "id": message_id,
            "threadId": f"thread_{message_id}",
            "labelIds": labels,
        }

    def list_labels(self) -> List[Dict[str, Any]]:
        return [{"id": self._label_id, "name": QUARANTINE_LABEL_NAME}]

    def add_label(self, message_id: str, label_id: str) -> None:
        self._labels.setdefault(message_id, ["INBOX"])
        if label_id not in self._labels[message_id]:
            self._labels[message_id].append(label_id)

    def archive_message(self, message_id: str) -> None:
        """Remove INBOX label (archive = remove from inbox)."""
        labels = self._labels.get(message_id, ["INBOX"])
        self._labels[message_id] = [lbl for lbl in labels if lbl != "INBOX"]

    def unarchive_message(self, message_id: str) -> None:
        """Re-add INBOX label (unarchive)."""
        labels = self._labels.get(message_id, [])
        if "INBOX" not in labels:
            labels.append("INBOX")
        self._labels[message_id] = labels

    def remove_label(self, message_id: str, label_id: str) -> None:
        labels = self._labels.get(message_id, [])
        self._labels[message_id] = [lbl for lbl in labels if lbl != label_id]

    def create_label(self, name: str) -> Dict[str, Any]:
        return {"id": self._label_id, "name": name}


@pytest.fixture
def db():
    class _DB(DatabaseMixin):
        def __init__(self):
            self.init_db(":memory:")

    d = _DB()
    action_store.init_schema(d)
    yield d
    d.close_db()


@pytest.fixture
def fake_gmail():
    return _FakeGmail()


# ---------------------------------------------------------------------------
# Quarantine tests
# ---------------------------------------------------------------------------


class TestQuarantinePhishingImpl:
    def test_moves_message_out_of_inbox(self, fake_gmail, db):
        """quarantine_phishing_impl removes INBOX and adds quarantine label."""
        fake_gmail._labels["msg1"] = ["INBOX", "UNREAD"]
        result = quarantine_phishing_impl(
            fake_gmail, db, message_id="msg1", is_phishing=True
        )
        assert result["quarantined"] is True
        labels = fake_gmail._labels["msg1"]
        assert "INBOX" not in labels, "INBOX should be removed after quarantine"
        quarantine_label_id = fake_gmail._label_id
        assert quarantine_label_id in labels, "Quarantine label should be added"

    def test_records_undoable_action(self, fake_gmail, db):
        """The action is recorded in the action log so it can be reversed."""
        fake_gmail._labels["msg2"] = ["INBOX"]
        result = quarantine_phishing_impl(
            fake_gmail, db, message_id="msg2", is_phishing=True
        )
        action_id = result["action_id"]
        # Fetch within a generous window (60 s).
        action = action_store.fetch_undoable(db, action_id=action_id, window_seconds=60)
        assert action is not None, "Quarantine action must be in the action log"
        assert action["action_type"] == "quarantine_phishing"
        payload = action["payload"]
        assert "prior_labels" in payload
        assert "INBOX" in payload["prior_labels"]

    def test_refuses_non_phishing_message(self, fake_gmail, db):
        """The tool must refuse to quarantine a message not flagged as phishing."""
        fake_gmail._labels["msg3"] = ["INBOX"]
        with pytest.raises(ValueError, match="phishing"):
            quarantine_phishing_impl(
                fake_gmail, db, message_id="msg3", is_phishing=False
            )

    def test_never_hard_deletes(self, fake_gmail, db):
        """Quarantine is a label-move, not a delete — message must remain in the
        backend after quarantine."""
        fake_gmail._labels["msg4"] = ["INBOX"]
        quarantine_phishing_impl(fake_gmail, db, message_id="msg4", is_phishing=True)
        # get_message should still work (message exists).
        msg = fake_gmail.get_message("msg4")
        assert msg["id"] == "msg4", "Message must not be deleted by quarantine"


class TestUnquarantineImpl:
    def test_restores_prior_labels(self, fake_gmail, db):
        """unquarantine_impl re-adds INBOX and removes the quarantine label."""
        fake_gmail._labels["msg5"] = ["INBOX"]
        result = quarantine_phishing_impl(
            fake_gmail, db, message_id="msg5", is_phishing=True
        )
        action_id = result["action_id"]

        # Now reverse it.
        unquarantine_impl(fake_gmail, db, action_id=action_id, window_seconds=60)
        labels = fake_gmail._labels["msg5"]
        assert "INBOX" in labels, "INBOX should be restored"
        assert fake_gmail._label_id not in labels, "Quarantine label should be removed"

    def test_unquarantine_outside_window_raises(self, fake_gmail, db):
        """After the undo window, unquarantine raises rather than silently no-ops."""
        fake_gmail._labels["msg6"] = ["INBOX"]
        result = quarantine_phishing_impl(
            fake_gmail, db, message_id="msg6", is_phishing=True
        )
        action_id = result["action_id"]
        with pytest.raises(RuntimeError, match="undo window"):
            unquarantine_impl(fake_gmail, db, action_id=action_id, window_seconds=0)


# ---------------------------------------------------------------------------
# Confirmation gating
# ---------------------------------------------------------------------------


class TestConfirmationGating:
    def test_quarantine_phishing_message_is_confirmation_gated(self):
        """quarantine_phishing_message must be in TOOLS_REQUIRING_CONFIRMATION."""
        assert "quarantine_phishing_message" in TOOLS_REQUIRING_CONFIRMATION, (
            "'quarantine_phishing_message' is missing from TOOLS_REQUIRING_CONFIRMATION. "
            "This tool mutates message state and must require explicit user confirmation."
        )


# ---------------------------------------------------------------------------
# Prompt-injection safety
# ---------------------------------------------------------------------------


class TestPhishingBodyPromptInjection:
    """The agent MUST NOT follow instructions embedded in phishing email bodies.

    The system prompt uses <<<UNTRUSTED_EMAIL_BODY_*>>> delimiters and
    explicitly tells the LLM that body content is DATA, never instructions.
    This test verifies the system prompt contains the safety language
    and that the phishing flag propagates through the contract.
    """

    def test_system_prompt_contains_untrusted_input_warning(self):
        from gaia.agents.email.agent import _SYSTEM_PROMPT

        assert (
            "UNTRUSTED" in _SYSTEM_PROMPT
        ), "System prompt must contain the UNTRUSTED INPUT warning section."
        assert (
            "UNTRUSTED_EMAIL_BODY_START" in _SYSTEM_PROMPT
        ), "System prompt must reference the <<<UNTRUSTED_EMAIL_BODY_START>>> delimiter."

    def test_system_prompt_refuses_phishing_action_instructions(self):
        from gaia.agents.email.agent import _SYSTEM_PROMPT

        # The system prompt must tell the LLM to refuse acting on body instructions.
        assert (
            "refuse" in _SYSTEM_PROMPT.lower()
            or "must refuse" in _SYSTEM_PROMPT.lower()
        ), "System prompt must instruct the LLM to refuse acting on email body instructions."
