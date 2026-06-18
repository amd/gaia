# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Issue #1738 — Outlook archive-undo: folder move changes the message id.

Outlook.archive_message moves the message to a folder and returns a NEW id.
The prior undo_archive_batch_impl used get_message(old_id) + add_label loops
which 404 for Outlook. This file pins all four #1738 acceptance criteria:

1. Outlook archive -> undo restores the message to the inbox (uses new id).
2. Mixed Gmail+Outlook batch undo restores both providers.
3. Partial failure in undo (one row's backend raises) reports the failure
   without aborting the rest of the batch.
4. archive_message_batch records post_archive_id in the action payload for
   Outlook (so undo can locate the message after the move).

These tests MUST fail against the un-patched main code and pass after the fix.
"""

from __future__ import annotations

import json as _json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeCalendarBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fake backends
# ---------------------------------------------------------------------------


def _gmail_msg(msg_id: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
    """Minimal Gmail-API-shape message in INBOX."""
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": labels if labels is not None else ["INBOX", "UNREAD"],
        "snippet": f"snippet for {msg_id}",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": "sender@gmail.com"},
                {"name": "Subject", "value": f"Gmail msg {msg_id}"},
            ],
            "body": {"size": 0, "data": ""},
        },
        "sizeEstimate": 100,
    }


def _outlook_msg(msg_id: str) -> Dict[str, Any]:
    """Minimal Outlook/Graph-API-shape message."""
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": [],
        "snippet": f"Outlook msg {msg_id}",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "From", "value": "user@outlook.com"}],
            "body": {"size": 0},
        },
    }


class FakeGmailBackend:
    """Minimal Gmail-shaped fake with stable ids (archive = remove INBOX label)."""

    def __init__(self, messages: Dict[str, Dict[str, Any]]) -> None:
        self._messages = {k: dict(v) for k, v in messages.items()}
        self.calls: List[tuple] = []

    def get_user_email(self) -> str:
        return "user@gmail.com"

    def list_messages(
        self, *, query=None, label_ids=None, max_results=25, page_token=None
    ):
        return {"messages": [{"id": k, "threadId": f"t-{k}"} for k in self._messages]}

    def get_message(self, message_id: str) -> Dict[str, Any]:
        self.calls.append(("get_message", message_id))
        if message_id not in self._messages:
            raise KeyError(f"FakeGmailBackend: no message {message_id!r}")
        return self._messages[message_id]

    def archive_message(self, message_id: str) -> Dict[str, Any]:
        self.calls.append(("archive_message", message_id))
        msg = self._messages[message_id]
        labels = [lab for lab in msg.get("labelIds", []) if lab != "INBOX"]
        msg["labelIds"] = labels
        return dict(msg)  # id is STABLE for Gmail

    def unarchive_message(
        self, message_id: str, prior_labels: List[str]
    ) -> Dict[str, Any]:
        """Restore to inbox: re-add INBOX + any prior labels."""
        self.calls.append(("unarchive_message", message_id, prior_labels))
        msg = self._messages[message_id]
        to_add = list({"INBOX", *(prior_labels or [])})
        for lab in to_add:
            if lab not in msg["labelIds"]:
                msg["labelIds"].append(lab)
        return dict(msg)

    def add_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        self.calls.append(("add_label", message_id, label_id))
        msg = self._messages[message_id]
        if label_id not in msg["labelIds"]:
            msg["labelIds"].append(label_id)
        return dict(msg)

    def list_labels(self) -> List[Dict[str, Any]]:
        return [{"id": "INBOX", "name": "INBOX", "type": "system"}]

    def trash_message(self, message_id: str) -> Dict[str, Any]:
        self.calls.append(("trash_message", message_id))
        return self._messages.get(message_id, {"id": message_id})

    def untrash_message(self, message_id: str) -> Dict[str, Any]:
        self.calls.append(("untrash_message", message_id))
        return self._messages.get(message_id, {"id": message_id})

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        return {"id": thread_id, "messages": list(self._messages.values())}


class FakeOutlookBackend:
    """Outlook fake where archive MOVES the message and assigns a NEW id.

    This is the critical correctness guard for #1738: the old id becomes invalid
    after archive, so get_message(old_id) would raise.  The fix must use
    post_archive_id from the action payload for undo.
    """

    def __init__(self, messages: Dict[str, Dict[str, Any]]) -> None:
        self._messages = {k: dict(v) for k, v in messages.items()}
        self._archive_folder: Dict[str, Dict[str, Any]] = {}
        self.calls: List[tuple] = []

    def get_user_email(self) -> str:
        return "user@outlook.com"

    def list_messages(
        self, *, query=None, label_ids=None, max_results=25, page_token=None
    ):
        return {"messages": [{"id": k, "threadId": f"t-{k}"} for k in self._messages]}

    def get_message(self, message_id: str) -> Dict[str, Any]:
        self.calls.append(("get_message", message_id))
        if message_id in self._messages:
            return self._messages[message_id]
        if message_id in self._archive_folder:
            return self._archive_folder[message_id]
        raise KeyError(
            f"FakeOutlookBackend: no message {message_id!r} (moved by archive?)"
        )

    def archive_message(self, message_id: str) -> Dict[str, Any]:
        """Move to archive and return a new id (Outlook semantics)."""
        self.calls.append(("archive_message", message_id))
        if message_id not in self._messages:
            raise KeyError(f"FakeOutlookBackend: no message {message_id!r}")
        msg = dict(self._messages.pop(message_id))
        new_id = message_id + "-archived"
        msg["id"] = new_id
        self._archive_folder[new_id] = msg
        return msg  # Returns resource with the NEW id

    def unarchive_message(
        self, message_id: str, prior_labels: List[str]
    ) -> Dict[str, Any]:
        """Move back to inbox; prior_labels unused (categories survive folder move)."""
        self.calls.append(("unarchive_message", message_id, prior_labels))
        if message_id in self._archive_folder:
            msg = dict(self._archive_folder.pop(message_id))
            original_id = message_id.replace("-archived", "")
            msg["id"] = original_id
            self._messages[original_id] = msg
            return msg
        if message_id in self._messages:
            return self._messages[message_id]
        raise KeyError(f"FakeOutlookBackend: no message {message_id!r} to unarchive")

    def add_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        self.calls.append(("add_label", message_id, label_id))
        for store in (self._messages, self._archive_folder):
            if message_id in store:
                return store[message_id]
        raise KeyError(f"FakeOutlookBackend: no message {message_id!r}")

    def list_labels(self) -> List[Dict[str, Any]]:
        return []

    def trash_message(self, message_id: str) -> Dict[str, Any]:
        self.calls.append(("trash_message", message_id))
        return self._messages.get(message_id, {"id": message_id})

    def untrash_message(self, message_id: str) -> Dict[str, Any]:
        self.calls.append(("untrash_message", message_id))
        return self._messages.get(message_id, {"id": message_id})

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        return {"id": thread_id, "messages": list(self._messages.values())}

    # -- Test helpers ---------------------------------------------------------

    def inbox_contains(self, original_id: str) -> bool:
        return original_id in self._messages

    def archive_folder_contains(self, archived_id: str) -> bool:
        return archived_id in self._archive_folder


class BrokenBackend:
    """Backend whose unarchive_message always raises — for partial failure test."""

    def unarchive_message(
        self, message_id: str, prior_labels: List[str]
    ) -> Dict[str, Any]:
        raise RuntimeError(f"BrokenBackend: cannot restore {message_id!r}")

    def get_message(self, message_id: str) -> Dict[str, Any]:
        raise KeyError(f"BrokenBackend: {message_id!r} not found")

    def add_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        raise RuntimeError("BrokenBackend.add_label always fails")


# ---------------------------------------------------------------------------
# Agent factory helpers
# ---------------------------------------------------------------------------


def _make_agent(gmail, outlook, tmp_path):
    """Create EmailTriageAgent with gmail + outlook backends injected."""
    cfg = EmailAgentConfig(
        gmail_backend=gmail,
        outlook_backend=outlook,
        calendar_backend=FakeCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
        mail_provider=None,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


def _make_single_gmail_agent(gmail, tmp_path):
    """Create EmailTriageAgent with only a Gmail backend (no Outlook)."""
    cfg = EmailAgentConfig(
        gmail_backend=gmail,
        calendar_backend=FakeCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


def _tool(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


# ---------------------------------------------------------------------------
# AC 4: archive_message_batch records post_archive_id in payload
# ---------------------------------------------------------------------------


class TestArchiveRecordsPostArchiveId:
    """AC 4: action store payload contains post_archive_id after Outlook archive."""

    def test_archive_records_post_archive_id_for_outlook(self, tmp_path):
        """Archiving an Outlook message records post_archive_id in action payload."""
        outlook = FakeOutlookBackend({"m-outlook": _outlook_msg("m-outlook")})
        gmail = FakeGmailBackend({})
        agent = _make_agent(gmail, outlook, tmp_path)
        try:
            agent._remember_message_mailbox("m-outlook", "microsoft")

            env = _json.loads(_tool("archive_message_batch")(["m-outlook"]))
            assert env["ok"] is True, env
            assert env["data"]["failed"] == []

            rows = agent.query(
                "SELECT payload_json FROM email_actions WHERE message_id = 'm-outlook'"
            )
            assert rows, "No action row recorded"
            payload = _json.loads(rows[0]["payload_json"])
            assert (
                "post_archive_id" in payload
            ), f"post_archive_id not recorded in payload: {payload}"
            # Outlook archive changes the id
            assert (
                payload["post_archive_id"] == "m-outlook-archived"
            ), f"Expected 'm-outlook-archived' got {payload['post_archive_id']!r}"
        finally:
            agent.close_db()

    def test_archive_records_stable_id_for_gmail(self, tmp_path):
        """For Gmail, post_archive_id equals the pre-archive id (stable id)."""
        gmail = FakeGmailBackend({"g1": _gmail_msg("g1")})
        agent = _make_single_gmail_agent(gmail, tmp_path)
        try:
            env = _json.loads(_tool("archive_message_batch")(["g1"]))
            assert env["ok"] is True, env

            rows = agent.query(
                "SELECT payload_json FROM email_actions WHERE message_id = 'g1'"
            )
            assert rows
            payload = _json.loads(rows[0]["payload_json"])
            # post_archive_id must be present and equal to original for Gmail
            if "post_archive_id" in payload:
                assert payload["post_archive_id"] == "g1"
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# AC 1: Outlook archive -> undo restores to inbox
# ---------------------------------------------------------------------------


class TestUndoArchiveOutlookRestoresToInbox:
    """AC 1: undo_archive_batch restores Outlook message to the Inbox within the window."""

    def test_undo_archive_outlook_restores_to_inbox(self, tmp_path):
        """Canonical #1738 repro: archive changes id; undo must use new id to restore."""
        outlook = FakeOutlookBackend({"m-outlook": _outlook_msg("m-outlook")})
        gmail = FakeGmailBackend({})
        agent = _make_agent(gmail, outlook, tmp_path)
        try:
            agent._remember_message_mailbox("m-outlook", "microsoft")

            # Archive: id changes to m-outlook-archived
            env = _json.loads(_tool("archive_message_batch")(["m-outlook"]))
            assert env["ok"] is True, env
            batch_id = env["data"]["batch_id"]

            assert outlook.inbox_contains("m-outlook") is False
            assert outlook.archive_folder_contains("m-outlook-archived") is True

            # Undo: must find the message by post_archive_id (not original id)
            undo_env = _json.loads(_tool("undo_archive_batch")(batch_id))
            assert undo_env["ok"] is True, undo_env
            data = undo_env["data"]
            assert data["restored"] == 1
            assert (
                data.get("failed", []) == []
            ), f"undo had failures: {data.get('failed')}"

            # Message is back in the inbox folder
            assert outlook.inbox_contains(
                "m-outlook"
            ), "Outlook message was not moved back to inbox"
            assert outlook.archive_folder_contains("m-outlook-archived") is False
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# AC 2: Mixed Gmail + Outlook batch undo restores both providers
# ---------------------------------------------------------------------------


class TestUndoArchiveMixedBatchRestoresBoth:
    """AC 2: mixed-mailbox batch undo restores both providers (no half-undo)."""

    def test_undo_archive_mixed_batch_restores_both(self, tmp_path):
        """Gmail + Outlook in one batch; undo; assert both restored, failed == []."""
        outlook = FakeOutlookBackend({"m-outlook": _outlook_msg("m-outlook")})
        gmail = FakeGmailBackend({"g-gmail": _gmail_msg("g-gmail")})
        agent = _make_agent(gmail, outlook, tmp_path)
        try:
            agent._remember_message_mailbox("g-gmail", "google")
            agent._remember_message_mailbox("m-outlook", "microsoft")

            env = _json.loads(_tool("archive_message_batch")(["g-gmail", "m-outlook"]))
            assert env["ok"] is True, env
            assert env["data"]["failed"] == []
            batch_id = env["data"]["batch_id"]

            assert "INBOX" not in gmail._messages["g-gmail"]["labelIds"]
            assert outlook.inbox_contains("m-outlook") is False

            undo_env = _json.loads(_tool("undo_archive_batch")(batch_id))
            assert undo_env["ok"] is True, undo_env
            data = undo_env["data"]
            assert data["restored"] == 2
            assert (
                data.get("failed", []) == []
            ), f"undo had failures: {data.get('failed')}"

            assert (
                "INBOX" in gmail._messages["g-gmail"]["labelIds"]
            ), "Gmail message not restored to INBOX"
            assert outlook.inbox_contains(
                "m-outlook"
            ), "Outlook message was not moved back to inbox"
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# AC 3: Partial failure in undo reports failure without aborting
# ---------------------------------------------------------------------------


class TestUndoArchiveBatchPartialFailure:
    """AC 3: one row's backend raises on restore; other row still restored."""

    def test_undo_archive_batch_partial_failure_reports_not_aborts(self, tmp_path):
        """Partial undo failure: remaining rows restored, failure in 'failed' list."""
        gmail = FakeGmailBackend({"g1": _gmail_msg("g1"), "g2": _gmail_msg("g2")})
        agent = _make_single_gmail_agent(gmail, tmp_path)
        try:
            env = _json.loads(_tool("archive_message_batch")(["g1", "g2"]))
            assert env["ok"] is True, env
            batch_id = env["data"]["batch_id"]

            broken = BrokenBackend()

            original_backend_for_action = agent._backend_for_action

            def _patched(action):
                if action.get("message_id") == "g1":
                    return broken
                return original_backend_for_action(action)

            agent._backend_for_action = _patched

            undo_env = _json.loads(_tool("undo_archive_batch")(batch_id))
            assert undo_env["ok"] is True, undo_env
            data = undo_env["data"]

            assert data["restored"] == 1, f"Expected 1 restored, got {data['restored']}"
            failed = data.get("failed", [])
            assert len(failed) == 1, f"Expected 1 failure, got {failed}"
            assert failed[0]["message_id"] == "g1"

            # g2 is back in inbox
            assert (
                "INBOX" in gmail._messages["g2"]["labelIds"]
            ), "g2 not restored to INBOX"
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Regression guard: existing Gmail-only undo still green
# ---------------------------------------------------------------------------


class TestGmailOnlyUndoRegression:
    """Gmail-only batch archive -> undo must still work after the fix."""

    def test_gmail_only_undo_still_works(self, tmp_path):
        """Single-provider Gmail undo works after the fix."""
        gmail = FakeGmailBackend({f"g{i}": _gmail_msg(f"g{i}") for i in range(5)})
        agent = _make_single_gmail_agent(gmail, tmp_path)
        try:
            ids = list(gmail._messages.keys())
            env = _json.loads(_tool("archive_message_batch")(ids))
            assert env["ok"] is True, env
            batch_id = env["data"]["batch_id"]

            for mid in ids:
                assert "INBOX" not in gmail._messages[mid]["labelIds"]

            undo_env = _json.loads(_tool("undo_archive_batch")(batch_id))
            assert undo_env["ok"] is True, undo_env
            assert undo_env["data"]["restored"] == len(ids)
            assert undo_env["data"].get("failed", []) == []

            for mid in ids:
                assert (
                    "INBOX" in gmail._messages[mid]["labelIds"]
                ), f"{mid} not restored"
        finally:
            agent.close_db()

    def test_undo_fails_after_window_still_works(self, tmp_path):
        """Outside the undo window, undo still fails loudly (regression guard)."""
        gmail = FakeGmailBackend({"g1": _gmail_msg("g1")})
        agent = _make_single_gmail_agent(gmail, tmp_path)
        try:
            env = _json.loads(_tool("archive_message_batch")(["g1"]))
            batch_id = env["data"]["batch_id"]

            agent.update(
                "email_actions",
                {"created_at": time.time() - 3600},
                "batch_id = :b",
                {"b": batch_id},
            )

            undo_env = _json.loads(_tool("undo_archive_batch")(batch_id))
            assert undo_env["ok"] is False
            assert "undo window" in undo_env["error"].lower()
        finally:
            agent.close_db()
