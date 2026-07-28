# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for #2523 + #2533 — both are the same class of defect on
destructive mail operations: the agent told the user it could do something
it could not, on real mail.

#2523 — trashed mail was stranded. The agent's only restore path
(``restore_message``) is gated by a short undo window and a live
``action_id``; once either was gone, three different phrasings all dead-ended
even though the message was still sitting in Trash (Gmail keeps it there for
30 days). ``restore_trashed_message`` + ``search_trash`` fix this by
reconciling with live mailbox state instead of an in-memory undo log.

#2533 — ``permanent_delete`` was advertised as a real capability, but Google
gates real permanent delete behind the ``https://mail.google.com/``
full-mailbox scope, which GAIA deliberately never requests. Every call would
403. The tool is no longer registered at all.

These exercise the REAL ``EmailTriageAgent`` construction + the REAL
``_TOOL_REGISTRY`` — not a hand-maintained list of expected tool names — so a
regression that re-adds ``permanent_delete`` or re-couples restore to the undo
window is caught structurally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# parents[0]=tests/ [1]=python/ [2]=email/ [3]=agents/ [4]=hub/ [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import action_store  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


class _MinimalCalendarBackend:
    pass


def _inbox_message(message_id: str, sender: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1700000000000",
        "snippet": "newsletter",
        "payload": {
            "headers": [
                {"name": "From", "value": f"News <{sender}>"},
                {"name": "Subject", "value": "Weekly digest"},
                {"name": "Message-ID", "value": f"<{message_id}@x.com>"},
            ],
        },
    }


def _build_agent(tmp_path: Path, messages: list[dict]):
    backend = FakeGmailBackend(user_email="me@example.com")
    for msg in messages:
        backend.add_message(msg)

    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
        debug=False,
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent, backend


def _call_tool(name: str, *args, **kwargs) -> dict:
    entry = _TOOL_REGISTRY.get(name)
    assert entry is not None, f"tool {name!r} not registered"
    return json.loads(entry["function"](*args, **kwargs))


# ---------------------------------------------------------------------------
# #2533 — permanent_delete is gone, checked against the real registration
# ---------------------------------------------------------------------------


class TestPermanentDeleteRemoved:
    def test_absent_from_the_real_tool_registry(self, tmp_path):
        """Not a hand-written list -- the actual registry a live agent built."""
        agent, _ = _build_agent(tmp_path, [])
        try:
            assert "permanent_delete" not in _TOOL_REGISTRY
        finally:
            agent.close_db()

    def test_absent_from_confirmation_required_tools(self):
        assert "permanent_delete" not in EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS
        assert "permanent_delete" not in EmailTriageAgent.confirmation_required_tools()

    def test_system_prompt_says_it_cannot_permanently_delete(self, tmp_path):
        """The prompt may still NAME permanent_delete (to pre-empt the LLM
        hallucinating that a tool by that name exists), but must state plainly
        that the capability does not exist, and must not list it alongside
        the other confirmation-gated tools as if it were callable."""
        agent, _ = _build_agent(tmp_path, [])
        try:
            prompt = agent._get_system_prompt()
            assert "CANNOT permanently delete" in prompt
            assert "there is no permanent_delete tool" in prompt
            # The old destructive-tools bullet used to list permanent_delete
            # right alongside accept_invite as if it were a callable tool —
            # that specific grouping must be gone.
            assert "permanent_delete, accept_invite" not in prompt
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# #2523 — restore-from-trash, independent of the undo window / action_id
# ---------------------------------------------------------------------------


class TestRestoreFromTrash:
    def test_search_trash_then_restore_round_trip(self, tmp_path):
        """The exact reported flow: trash a message, lose track of its
        action_id (a fresh triage/scan re-tags message ids and the LLM never
        held onto the id), then find it again via search_trash and restore
        it -- no action_id anywhere in this call sequence."""
        msg = _inbox_message("m1", "news@example.com")
        agent, backend = _build_agent(tmp_path, [msg])
        try:
            trashed = _call_tool("trash_message", "m1")
            assert trashed["ok"] is True
            assert "TRASH" in backend.get_message("m1")["labelIds"]

            found = _call_tool("search_trash", "from:news@example.com")
            assert found["ok"] is True, found
            found_ids = {m["id"] for m in found["data"]["messages"]}
            assert "m1" in found_ids, found

            restored = _call_tool("restore_trashed_message", "m1")
            assert restored["ok"] is True, restored
            assert restored["data"]["restored"] is True

            post = backend.get_message("m1")
            assert "INBOX" in post["labelIds"]
            assert "TRASH" not in post["labelIds"]
        finally:
            agent.close_db()

    def test_restore_trashed_message_ignores_an_expired_undo_window(
        self, tmp_path, monkeypatch
    ):
        """Regression guard for #2523: the observed failure was restore
        dead-ending once the undo window elapsed. Force the window to have
        expired and confirm restore_trashed_message still succeeds while the
        legacy restore_message (same action_id) is confirmed expired."""
        msg = _inbox_message("m1", "news@example.com")
        agent, backend = _build_agent(tmp_path, [msg])
        try:
            trashed = _call_tool("trash_message", "m1")
            action_id = trashed["data"]["action_id"]

            import time

            agent.update(
                "email_actions",
                {"created_at": time.time() - 3600},
                "action_id = :id",
                {"id": action_id},
            )

            expired = _call_tool("restore_message", action_id)
            assert expired["ok"] is False
            assert "restore_trashed_message" in expired["error"], (
                "the expired-window error must point at the working "
                "alternative, not just dead-end"
            )

            restored = _call_tool("restore_trashed_message", "m1")
            assert restored["ok"] is True, restored
            assert "INBOX" in backend.get_message("m1")["labelIds"]
        finally:
            agent.close_db()

    def test_restore_trashed_message_fails_loud_when_not_in_trash(self, tmp_path):
        msg = _inbox_message("m1", "news@example.com")
        agent, _ = _build_agent(tmp_path, [msg])
        try:
            result = _call_tool("restore_trashed_message", "m1")
            assert result["ok"] is False
            assert "not in Trash" in result["error"]
        finally:
            agent.close_db()

    def test_trash_confirmation_wording_says_trash_not_archived(self, tmp_path):
        """Observed verbatim bug: the agent told the user a trashed message
        'has been archived'. The tool's own docstring (what the LLM reads to
        phrase its confirmation) may contrast trash against archive by name,
        but must explicitly instruct: say Trash, never call it archived."""
        agent, _ = _build_agent(tmp_path, [])
        try:
            entry = _TOOL_REGISTRY.get("trash_message")
            assert entry is not None
            doc = entry["description"]
            assert "Trash" in doc
            assert 'never "archived"' in doc or 'never say "archived"' in doc
        finally:
            agent.close_db()

    def test_system_prompt_trash_paragraph_distinguishes_from_archive(self, tmp_path):
        agent, _ = _build_agent(tmp_path, [])
        try:
            prompt = agent._get_system_prompt()
            assert "restore_trashed_message" in prompt
            assert "search_trash" in prompt
            assert 'never "archived"' in prompt or "NOT the same as archive" in prompt
        finally:
            agent.close_db()

    def test_restore_trashed_message_and_search_trash_are_registered(self, tmp_path):
        agent, _ = _build_agent(tmp_path, [])
        try:
            assert "restore_trashed_message" in _TOOL_REGISTRY
            assert "search_trash" in _TOOL_REGISTRY
        finally:
            agent.close_db()
