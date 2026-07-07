# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Multi-inbox triage + per-message routing (#1603 Phase 2).

With both mailboxes connected, ``triage_inbox`` / ``pre_scan_inbox`` must scan
BOTH and merge, tagging every item with its source mailbox, while ``max_messages``
stays a TOTAL budget split across mailboxes (never per-mailbox). Downstream
actions (reply / archive / trash) must route to the mailbox the message came
from — no cross-mailbox 404s.

The backends here are lightweight in-test spies that satisfy the ``GmailBackend``
Protocol surface the triage / action paths touch. No live OAuth, no network.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("gaia_agent_email")  # noqa: E402

from unittest.mock import MagicMock, patch

from gaia_agent_email.agent import EmailTriageAgent
from gaia_agent_email.config import EmailAgentConfig

# ---------------------------------------------------------------------------
# In-test spy backend (Gmail-API-shape, records mutations)
# ---------------------------------------------------------------------------


def _msg(message_id: str, *, subject: str = "Hi", sender: str = "a@example.com"):
    """Build a minimal Gmail-API-shape message.

    Tagged ``CATEGORY_PROMOTIONS`` so the triage heuristic commits confidently
    (→ "low priority") WITHOUT an LLM call — these tests exercise multi-inbox
    routing, not classification, so the classifier path is deliberately avoided.
    """
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
            ],
            "body": {"data": ""},
        },
    }


class SpyBackend:
    """Minimal GmailBackend-shaped fake that records mutating calls.

    Holds a fixed list of message ids; ``list_messages`` honors ``max_results``
    so the budget-split assertions can verify the per-backend cap. Every mutate
    method appends ``(method, message_id)`` to ``calls`` so routing tests can
    prove which backend received an action.
    """

    def __init__(self, name: str, message_ids: list[str]):
        self.name = name
        self._messages = {
            mid: _msg(mid, sender=f"{name}@example.com") for mid in message_ids
        }
        self.calls: list[tuple[str, str]] = []

    # -- Read ---------------------------------------------------------------

    def list_messages(
        self, *, query=None, label_ids=None, max_results=25, page_token=None
    ):
        ids = list(self._messages)[:max_results]
        return {"messages": [{"id": m, "threadId": f"t-{m}"} for m in ids]}

    def get_message(self, message_id: str):
        if message_id not in self._messages:
            raise KeyError(f"{self.name}: no message {message_id!r}")
        return self._messages[message_id]

    def get_thread(self, thread_id: str):
        return {"id": thread_id, "messages": list(self._messages.values())}

    # -- Mutate (record routing) -------------------------------------------

    def trash_message(self, message_id: str):
        self.calls.append(("trash_message", message_id))
        return {"id": message_id}

    def untrash_message(self, message_id: str):
        self.calls.append(("untrash_message", message_id))
        return {"id": message_id}

    def archive_message(self, message_id: str):
        self.calls.append(("archive_message", message_id))
        return {"id": message_id}

    def add_label(self, message_id: str, label_id: str):
        self.calls.append(("add_label", message_id))
        return {"id": message_id}

    def create_draft(self, *, to, subject, body, headers=None):
        self.calls.append(("create_draft", to))
        return {"id": f"draft-{self.name}"}

    def send_message(self, *, to, subject, body, headers=None):
        self.calls.append(("send_message", to))
        return {"id": f"sent-{self.name}", "sent": True}


# ---------------------------------------------------------------------------
# Agent builder with two injected spy backends
# ---------------------------------------------------------------------------


def _agent_two_backends(tmp_path, monkeypatch, *, google_ids, microsoft_ids):
    """Construct an agent whose ``_backends`` is {google: spyG, microsoft: spyM}.

    Both mailboxes are reported connected; the heuristic-only path is used
    (chat is a MagicMock, classifier wiring is bypassed because the heuristic is
    confident on these synthetic messages — and even if it weren't, the mocked
    chat never actually classifies).
    """
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
        # is_spam is now content-judged (#1906): a CATEGORY_PROMOTIONS-tagged
        # message is category-confident but not spam-confident, so the
        # classifier IS invoked even though these tests are about routing,
        # not classification -- give it a valid no-op response so it doesn't
        # crash on the mock's unconfigured .text attribute.
        mock_sdk.return_value.send_messages.return_value.text = (
            '{"category": "PROMOTIONAL", "is_spam": false, "confidence": 1.0}'
        )
        agent = EmailTriageAgent(config=cfg)
    return agent, spy_g, spy_m


def _registered_tool(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


# ---------------------------------------------------------------------------
# D3 — merge + tag + budget
# ---------------------------------------------------------------------------


class TestMultiInboxTriageMergeAndTag:
    def test_agent_binds_both_backends(self, tmp_path, monkeypatch):
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            assert set(agent._backends) == {"google", "microsoft"}
            assert agent._backends["google"] is spy_g
            assert agent._backends["microsoft"] is spy_m
        finally:
            agent.close_db()

    def test_triage_merges_both_and_tags_mailbox(self, tmp_path, monkeypatch):
        agent, _, _ = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1", "g2"], microsoft_ids=["m1", "m2"]
        )
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            assert envelope["ok"] is True, envelope
            results = envelope["data"]["results"]
            ids = {r["id"] for r in results}
            # Both mailboxes contributed.
            assert {"g1", "g2"} & ids
            assert {"m1", "m2"} & ids
            # Every item carries a source mailbox tag.
            assert all(r.get("mailbox") in {"google", "microsoft"} for r in results)
            by_id = {r["id"]: r["mailbox"] for r in results}
            assert by_id["g1"] == "google"
            assert by_id["m1"] == "microsoft"
        finally:
            agent.close_db()

    def test_triage_total_budget_split_across_mailboxes(self, tmp_path, monkeypatch):
        # 10 messages in each mailbox; ask for 20 total → ~10 per mailbox, but
        # the MERGED total must not exceed the requested budget.
        agent, _, _ = _agent_two_backends(
            tmp_path,
            monkeypatch,
            google_ids=[f"g{i}" for i in range(10)],
            microsoft_ids=[f"m{i}" for i in range(10)],
        )
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            results = envelope["data"]["results"]
            # TOTAL budget — never per-mailbox-doubled.
            assert len(results) <= 20
            # Per-backend cap is max_messages // n_backends == 10.
            n_google = sum(1 for r in results if r["mailbox"] == "google")
            n_microsoft = sum(1 for r in results if r["mailbox"] == "microsoft")
            assert n_google <= 10
            assert n_microsoft <= 10
        finally:
            agent.close_db()

    def test_triage_budget_is_total_not_per_mailbox(self, tmp_path, monkeypatch):
        # 8 in each; ask for 8 total → 4 per mailbox, 8 merged (NOT 16).
        agent, _, _ = _agent_two_backends(
            tmp_path,
            monkeypatch,
            google_ids=[f"g{i}" for i in range(8)],
            microsoft_ids=[f"m{i}" for i in range(8)],
        )
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(8))
            results = envelope["data"]["results"]
            assert len(results) <= 8
            assert sum(1 for r in results if r["mailbox"] == "google") <= 4
            assert sum(1 for r in results if r["mailbox"] == "microsoft") <= 4
        finally:
            agent.close_db()

    def test_pre_scan_tags_every_section_item(self, tmp_path, monkeypatch):
        agent, _, _ = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1", "g2"], microsoft_ids=["m1", "m2"]
        )
        try:
            envelope = json.loads(_registered_tool("pre_scan_inbox")(20))
            assert envelope["ok"] is True, envelope
            data = envelope["data"]
            assert data["kind"] == "email_pre_scan"
            items = data["urgent"] + data["actionable"] + data["suggested_archives"]
            # Some item was produced and every one carries a mailbox tag.
            assert items, "pre-scan produced no section items"
            assert all(it.get("mailbox") in {"google", "microsoft"} for it in items)
        finally:
            agent.close_db()

    def test_pre_scan_refreshes_backends_connected_after_agent_construction(
        self, tmp_path, monkeypatch
    ):
        """A cached Agent UI email agent must see mailboxes connected later."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        spy_g = SpyBackend("google", ["g1"])
        spy_m = SpyBackend("microsoft", ["m1"])
        cfg = EmailAgentConfig(
            outlook_backend=spy_m,
            calendar_backend=object(),
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
            mail_provider=None,
        )
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            # is_spam is content-judged (#1906): give the mocked chat a valid
            # no-op response so a spam-only classifier escalation (see the
            # other mock_sdk setups in this file) doesn't crash.
            mock_sdk.return_value.send_messages.return_value.text = (
                '{"category": "PROMOTIONAL", "is_spam": false, "confidence": 1.0}'
            )
            agent = EmailTriageAgent(config=cfg)

        try:
            assert set(agent._backends) == {"microsoft"}

            # Simulate connecting Gmail after the Agent UI cached this agent.
            agent.config.gmail_backend = spy_g

            envelope = json.loads(_registered_tool("pre_scan_inbox")(20))
            assert envelope["ok"] is True, envelope
            data = envelope["data"]
            items = data["urgent"] + data["actionable"] + data["suggested_archives"]
            mailboxes = {it["mailbox"] for it in items}

            assert mailboxes == {"google", "microsoft"}
            assert agent._message_mailbox.get("g1") == "google"
            assert agent._message_mailbox.get("m1") == "microsoft"
        finally:
            agent.close_db()

    def test_pre_scan_under_budget_backend_not_skipped_when_earlier_backend_underfills(
        self, tmp_path, monkeypatch
    ):
        # The budget guard must count messages ACTUALLY returned, not the
        # per-backend cap. Fails-first scenario (max_messages < n_backends):
        #   - google inbox EMPTY, microsoft has 1 message
        #   - pre_scan(max_messages=1) → per_backend = max(1, 1//2) = 1
        #   - OLD `scanned += per_backend`: empty google bumps scanned to 1 →
        #     guard `scanned >= 1` trips → microsoft is SKIPPED (the bug)
        #   - NEW `scanned += actual`: empty google returns 0 → scanned stays 0
        #     → microsoft is scanned and contributes.
        # google is first in registry order, so it is the under-filling backend.
        agent, _, _ = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=[], microsoft_ids=["m1"]
        )
        try:
            envelope = json.loads(_registered_tool("pre_scan_inbox")(1))
            data = envelope["data"]
            items = data["urgent"] + data["actionable"] + data["suggested_archives"]
            mailboxes = {it["mailbox"] for it in items}
            # microsoft was reached despite the empty google scanned first.
            assert "microsoft" in mailboxes, mailboxes
            # And its message was tagged for downstream routing.
            assert agent._message_mailbox.get("m1") == "microsoft"
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# D4 — per-message backend routing
# ---------------------------------------------------------------------------


class TestPerMessageRouting:
    def test_trash_routes_to_tagged_mailbox(self, tmp_path, monkeypatch):
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            # Triage tags g1→google, m1→microsoft in _message_mailbox.
            _registered_tool("triage_inbox")(20)
            # Trash the Outlook-tagged message — must hit the Outlook spy only.
            envelope = json.loads(_registered_tool("trash_message")("m1"))
            assert envelope["ok"] is True, envelope
            assert ("trash_message", "m1") in spy_m.calls
            assert all(c[0] != "trash_message" for c in spy_g.calls)
            # And a Gmail-tagged message routes to the Gmail spy.
            json.loads(_registered_tool("trash_message")("g1"))
            assert ("trash_message", "g1") in spy_g.calls
        finally:
            agent.close_db()

    def test_archive_routes_to_tagged_mailbox(self, tmp_path, monkeypatch):
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            _registered_tool("triage_inbox")(20)
            json.loads(_registered_tool("archive_message")("m1"))
            assert ("archive_message", "m1") in spy_m.calls
            assert all(c[0] != "archive_message" for c in spy_g.calls)
        finally:
            agent.close_db()

    def test_unknown_message_with_two_backends_fails_loud(self, tmp_path, monkeypatch):
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            # Never triaged → no provenance → ambiguous with two backends.
            envelope = json.loads(_registered_tool("trash_message")("unknown-id"))
            assert envelope["ok"] is False
            # No mutation reached either backend.
            assert spy_g.calls == []
            assert spy_m.calls == []
        finally:
            agent.close_db()

    def test_explicit_mailbox_arg_overrides_provenance(self, tmp_path, monkeypatch):
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            # No triage; pass mailbox explicitly so routing is unambiguous.
            envelope = json.loads(_registered_tool("trash_message")("m1", "microsoft"))
            assert envelope["ok"] is True, envelope
            assert ("trash_message", "m1") in spy_m.calls
        finally:
            agent.close_db()

    def test_get_message_routes_to_tagged_mailbox(self, tmp_path, monkeypatch):
        # The standard flow is triage → get_message → act; a body read on an
        # Outlook-tagged id must hit Outlook, not 404 against the primary Gmail.
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            _registered_tool("triage_inbox")(20)
            envelope = json.loads(_registered_tool("get_message")("m1"))
            assert envelope["ok"] is True, envelope
            # The Outlook spy stamps its sender — proves which backend answered.
            assert "microsoft@example.com" in envelope["data"]["from"]
        finally:
            agent.close_db()

    def test_get_thread_routes_to_tagged_mailbox(self, tmp_path, monkeypatch):
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            _registered_tool("triage_inbox")(20)
            # Thread ids are remembered alongside message ids at triage time.
            envelope = json.loads(_registered_tool("get_thread")("t-m1"))
            assert envelope["ok"] is True, envelope
            senders = [m["from"] for m in envelope["data"]["messages"]]
            assert any("microsoft@example.com" in s for s in senders)
        finally:
            agent.close_db()

    def test_undo_routes_to_the_mailbox_the_action_hit(self, tmp_path, monkeypatch):
        # D5: trash an Outlook-tagged message, then restore — the untrash must
        # dispatch to the Outlook backend (read from the action row's mailbox),
        # never to Gmail.
        agent, spy_g, spy_m = _agent_two_backends(
            tmp_path, monkeypatch, google_ids=["g1"], microsoft_ids=["m1"]
        )
        try:
            _registered_tool("triage_inbox")(20)
            trash_env = json.loads(_registered_tool("trash_message")("m1"))
            assert trash_env["ok"] is True, trash_env
            action_id = trash_env["data"]["action_id"]
            restore_env = json.loads(_registered_tool("restore_message")(action_id))
            assert restore_env["ok"] is True, restore_env
            assert ("untrash_message", "m1") in spy_m.calls
            assert all(c[0] != "untrash_message" for c in spy_g.calls)
        finally:
            agent.close_db()


class TestSingleBackendRoutingUnchanged:
    def test_single_backend_routes_without_provenance(self, tmp_path, monkeypatch):
        # One mailbox connected → no tagging needed; an untriaged id still
        # routes to the sole backend (preserves the shipped single-mailbox UX).
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        spy_g = SpyBackend("google", ["g1"])
        cfg = EmailAgentConfig(
            gmail_backend=spy_g,
            calendar_backend=object(),
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
        )
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            # is_spam is content-judged (#1906): give the mocked chat a valid
            # no-op response so a spam-only classifier escalation (see the
            # other mock_sdk setups in this file) doesn't crash.
            mock_sdk.return_value.send_messages.return_value.text = (
                '{"category": "PROMOTIONAL", "is_spam": false, "confidence": 1.0}'
            )
            agent = EmailTriageAgent(config=cfg)
        try:
            assert set(agent._backends) == {"google"}
            envelope = json.loads(_registered_tool("trash_message")("g1"))
            assert envelope["ok"] is True, envelope
            assert ("trash_message", "g1") in spy_g.calls
        finally:
            agent.close_db()
