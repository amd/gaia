# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Multi-mailbox provenance: list/search/summarize tag and route correctly (#1707).

These tests cover the gap left after #1603/#1614/#1696:
- ``list_inbox`` / ``search_messages`` must tag each result with its source
  mailbox and remember provenance for downstream actions.
- ``summarize_message`` must accept an optional ``mailbox`` param and route via
  ``_backend_for_message`` rather than always using the primary (gmail) backend.
- After list/search surfaces a message id, downstream calls
  (summarize_message, summarize_thread, archive_message_batch) succeed without
  the caller passing ``mailbox=``.
- An id never surfaced by any tool + two mailboxes → still fails loud.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent
from gaia_agent_email.config import EmailAgentConfig

# ---------------------------------------------------------------------------
# Minimal spy backend (Gmail-API-shape)
# ---------------------------------------------------------------------------


def _msg(message_id, *, subject="Hi", sender="a@example.com"):
    """Build a minimal Gmail-API-shape message tagged PROMOTIONAL (heuristic confident)."""
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "labelIds": ["INBOX", "CATEGORY_PROMOTIONS"],
        "snippet": subject,
        "internalDate": "1000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": "Thu, 01 Jan 2026 00:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": ""},
        },
    }


class SpyBackend:
    """Minimal GmailBackend-shaped fake that records calls."""

    def __init__(self, name, message_ids):
        self.name = name
        self._messages = {
            mid: _msg(mid, sender=f"{name}-user@example.com", subject=f"msg-{mid}")
            for mid in message_ids
        }
        self.calls = []

    def list_messages(
        self, *, query=None, label_ids=None, max_results=25, page_token=None
    ):
        ids = list(self._messages)[:max_results]
        return {"messages": [{"id": m, "threadId": f"t-{m}"} for m in ids]}

    def get_message(self, message_id):
        if message_id not in self._messages:
            raise KeyError(f"{self.name}: no message {message_id!r}")
        self.calls.append(("get_message", message_id))
        return self._messages[message_id]

    def get_thread(self, thread_id):
        self.calls.append(("get_thread", thread_id))
        return {"id": thread_id, "messages": list(self._messages.values())}

    def archive_message(self, message_id):
        self.calls.append(("archive_message", message_id))
        return {"id": message_id}

    def trash_message(self, message_id):
        self.calls.append(("trash_message", message_id))
        return {"id": message_id}

    def list_labels(self):
        return []


# ---------------------------------------------------------------------------
# Agent builder with two injected spy backends
# ---------------------------------------------------------------------------


def _agent_two_backends(tmp_path, monkeypatch, *, google_ids, microsoft_ids):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    spy_g = SpyBackend("google", google_ids)
    spy_m = SpyBackend("microsoft", microsoft_ids)
    cfg = EmailAgentConfig(
        gmail_backend=spy_g,
        outlook_backend=spy_m,
        calendar_backend=object(),
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
        mail_provider=None,  # scan all connected
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent, spy_g, spy_m


def _tool(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


# ---------------------------------------------------------------------------
# Tests: list_inbox provenance
# ---------------------------------------------------------------------------


class TestListInboxProvenance:
    def test_list_inbox_items_carry_mailbox_field(self, tmp_path, monkeypatch):
        """Each message returned by list_inbox has a mailbox field."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            env = json.loads(_tool("list_inbox")())
            assert env["ok"] is True, env
            messages = env["data"]["messages"]
            assert messages, "list_inbox returned no messages"
            for msg in messages:
                assert msg.get("mailbox") in {
                    "google",
                    "microsoft",
                }, f"message {msg.get('id')!r} missing mailbox field: {msg}"
        finally:
            agent.close_db()

    def test_list_inbox_remembers_provenance_in_agent(self, tmp_path, monkeypatch):
        """After list_inbox, agent._message_mailbox has entries for each id."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            json.loads(_tool("list_inbox")())
            assert agent._message_mailbox.get("g1") == "google"
            assert agent._message_mailbox.get("m1") == "microsoft"
        finally:
            agent.close_db()

    def test_list_inbox_thread_ids_remembered(self, tmp_path, monkeypatch):
        """list_inbox also remembers thread_id provenance."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            json.loads(_tool("list_inbox")())
            assert agent._message_mailbox.get("t-g1") == "google"
            assert agent._message_mailbox.get("t-m1") == "microsoft"
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Tests: search_messages provenance
# ---------------------------------------------------------------------------


class TestSearchMessagesProvenance:
    def test_search_items_carry_mailbox_field(self, tmp_path, monkeypatch):
        """Each message returned by search_messages has a mailbox field."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1", "g2"], microsoft_ids=["m1", "m2"]
        )
        try:
            env = json.loads(_tool("search_messages")("subject:msg"))
            assert env["ok"] is True, env
            messages = env["data"]["messages"]
            assert messages, "search_messages returned no messages"
            for msg in messages:
                assert msg.get("mailbox") in {
                    "google",
                    "microsoft",
                }, f"message {msg.get('id')!r} missing mailbox field"
        finally:
            agent.close_db()

    def test_search_remembers_provenance_in_agent(self, tmp_path, monkeypatch):
        """After search_messages, agent._message_mailbox is populated."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            json.loads(_tool("search_messages")("anything"))
            assert agent._message_mailbox.get("g1") == "google"
            assert agent._message_mailbox.get("m1") == "microsoft"
        finally:
            agent.close_db()

    def test_search_tags_per_source_mailbox(self, tmp_path, monkeypatch):
        """google ids tagged google, microsoft ids tagged microsoft."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1", "g2"], microsoft_ids=["m1", "m2"]
        )
        try:
            env = json.loads(_tool("search_messages")("anything"))
            msgs = env["data"]["messages"]
            by_id = {m["id"]: m["mailbox"] for m in msgs if "mailbox" in m}
            if "g1" in by_id:
                assert by_id["g1"] == "google"
            if "m1" in by_id:
                assert by_id["m1"] == "microsoft"
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Tests: summarize_message routes via provenance
# ---------------------------------------------------------------------------


class _FakeChat:
    def send_messages(self, messages, system_prompt=None, **kwargs):
        class _R:
            text = "Short summary of the email."

        return _R()


class TestSummarizeMessageRouting:
    def test_summarize_message_routes_to_microsoft_after_list(
        self, tmp_path, monkeypatch
    ):
        """After list_inbox surfaces an Outlook id, summarize_message routes there."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        agent.chat = _FakeChat()
        try:
            json.loads(_tool("list_inbox")())
            assert agent._message_mailbox.get("m1") == "microsoft"

            spy_m.calls.clear()
            spy_g.calls.clear()
            env = json.loads(_tool("summarize_message")("m1"))
            assert env["ok"] is True, env

            assert any(c == ("get_message", "m1") for c in spy_m.calls), (
                f"summarize_message did not route to microsoft backend. "
                f"spy_m.calls={spy_m.calls}, spy_g.calls={spy_g.calls}"
            )
            assert not any(c == ("get_message", "m1") for c in spy_g.calls)
        finally:
            agent.close_db()

    def test_summarize_message_routes_to_google_after_list(self, tmp_path, monkeypatch):
        """After list_inbox surfaces a Gmail id, summarize_message routes there."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        agent.chat = _FakeChat()
        try:
            json.loads(_tool("list_inbox")())
            assert agent._message_mailbox.get("g1") == "google"

            spy_g.calls.clear()
            spy_m.calls.clear()
            env = json.loads(_tool("summarize_message")("g1"))
            assert env["ok"] is True, env

            assert any(c == ("get_message", "g1") for c in spy_g.calls)
            assert not any(c == ("get_message", "g1") for c in spy_m.calls)
        finally:
            agent.close_db()

    def test_summarize_message_explicit_mailbox_routes_correctly(
        self, tmp_path, monkeypatch
    ):
        """summarize_message(id, mailbox='microsoft') routes to microsoft."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        agent.chat = _FakeChat()
        try:
            spy_m.calls.clear()
            spy_g.calls.clear()
            env = json.loads(_tool("summarize_message")("m1", "microsoft"))
            assert env["ok"] is True, env
            assert any(c == ("get_message", "m1") for c in spy_m.calls)
            assert not any(c == ("get_message", "m1") for c in spy_g.calls)
        finally:
            agent.close_db()

    def test_summarize_message_unknown_id_two_backends_fails_loud(
        self, tmp_path, monkeypatch
    ):
        """An id never surfaced + two backends -> error envelope, not a guess."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        agent.chat = _FakeChat()
        try:
            env = json.loads(_tool("summarize_message")("never-seen-id"))
            assert env["ok"] is False, "expected error for unknown id with two backends"
            assert env["error"]
            assert not any(c[1] == "never-seen-id" for c in spy_g.calls)
            assert not any(c[1] == "never-seen-id" for c in spy_m.calls)
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Tests: summarize_thread routes via provenance (regression guard #1268)
# ---------------------------------------------------------------------------


class TestSummarizeThreadRouting:
    def test_summarize_thread_routes_after_search(self, tmp_path, monkeypatch):
        """After search surfaces a thread id, summarize_thread routes correctly."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        agent.chat = _FakeChat()
        try:
            json.loads(_tool("search_messages")("anything"))

            spy_m.calls.clear()
            spy_g.calls.clear()
            env = json.loads(_tool("summarize_thread")("t-m1"))
            assert env["ok"] is True, env

            assert any(c[0] == "get_thread" for c in spy_m.calls), (
                f"summarize_thread did not route to microsoft. "
                f"spy_m.calls={spy_m.calls}, spy_g.calls={spy_g.calls}"
            )
            assert not any(c[0] == "get_thread" for c in spy_g.calls)
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Tests: archive_message_batch routes via provenance (#1270)
# ---------------------------------------------------------------------------


class TestBatchArchiveRouting:
    def test_batch_archive_routes_each_id_to_its_mailbox(self, tmp_path, monkeypatch):
        """archive_message_batch routes each id to the correct backend."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            json.loads(_tool("list_inbox")())

            spy_g.calls.clear()
            spy_m.calls.clear()

            env = json.loads(_tool("archive_message_batch")(["g1", "m1"]))
            assert env["ok"] is True, env
            data = env["data"]
            assert data["total"] == 2
            assert data["failed"] == []

            assert any(c == ("archive_message", "g1") for c in spy_g.calls), spy_g.calls
            assert any(c == ("archive_message", "m1") for c in spy_m.calls), spy_m.calls
            assert not any(c == ("archive_message", "g1") for c in spy_m.calls)
            assert not any(c == ("archive_message", "m1") for c in spy_g.calls)
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Negative: unknown id + two backends = loud error (no silent guess)
# ---------------------------------------------------------------------------


class TestNoSilentFallback:
    def test_unknown_id_two_backends_raises_via_list_inbox_tool(
        self, tmp_path, monkeypatch
    ):
        """An id not in any backend's results does not get routing."""
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            json.loads(_tool("list_inbox")())
            env = json.loads(_tool("trash_message")("completely-unknown"))
            assert env["ok"] is False
            assert not any(c[1] == "completely-unknown" for c in spy_g.calls)
            assert not any(c[1] == "completely-unknown" for c in spy_m.calls)
        finally:
            agent.close_db()
