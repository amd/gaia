# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
#1739 — per-mailbox clean error on revoked grant, without aborting the whole scan.

When one of multiple connected mailboxes has its agent grant revoked,
``_triage_all_backends`` and ``_pre_scan_all_backends`` must:
  - continue scanning the remaining (granted) mailboxes,
  - surface a clean, per-mailbox notice in ``mailbox_errors`` instead of
    propagating the raw ``AuthRequiredError``,
  - never abort the entire multi-mailbox scan.

The connectors layer already raises ``AuthRequiredError(AGENT_NOT_GRANTED)`` eagerly
at ``get_access_token_sync`` time; the agent only needs to catch ``ConnectorsError``
per backend and record the error cleanly.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("gaia_agent_email")  # noqa: E402

from unittest.mock import MagicMock, patch

from gaia_agent_email.agent import EmailTriageAgent
from gaia_agent_email.config import EmailAgentConfig

from gaia.connectors.errors import AuthRequiredError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(message_id: str, *, subject: str = "Hi", sender: str = "a@example.com"):
    """Build a minimal Gmail-API-shape message that heuristic classifies without LLM."""
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


class GrantedBackend:
    """Minimal GmailBackend-shaped spy that always succeeds."""

    def __init__(self, name: str, message_ids: list[str]):
        self.name = name
        self._messages = {
            mid: _msg(mid, sender=f"{name}@example.com") for mid in message_ids
        }
        self.calls: list[tuple[str, str]] = []

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


class UngrantedBackend:
    """Backend whose scan methods raise AuthRequiredError on first call (eager grant gate)."""

    def __init__(self, name: str):
        self.name = name

    def list_messages(
        self, *, query=None, label_ids=None, max_results=25, page_token=None
    ):
        raise AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider=self.name,
            agent_id="installed:email",
            missing_scopes=["gmail.readonly"],
        )

    def get_message(self, message_id: str):
        raise AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider=self.name,
            agent_id="installed:email",
        )

    def get_thread(self, thread_id: str):
        raise AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider=self.name,
            agent_id="installed:email",
        )


def _build_agent(tmp_path, monkeypatch, *, granted_backend, ungranted_backend):
    """Construct an agent with one granted and one ungranted backend.

    Ungranted is microsoft, granted is google (or vice-versa depending on
    which SpyBackend instance is passed).
    """
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    cfg = EmailAgentConfig(
        gmail_backend=granted_backend,
        outlook_backend=ungranted_backend,
        calendar_backend=object(),
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
        mail_provider=None,  # scan all connected
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        # is_spam is now content-judged (#1906): a CATEGORY_PROMOTIONS-tagged
        # message is category-confident but not spam-confident, so the
        # classifier IS invoked -- give it a valid no-op response so it
        # doesn't crash on the mock's unconfigured .text attribute.
        mock_sdk.return_value.send_messages.return_value.text = (
            '{"category": "PROMOTIONAL", "is_spam": false, "confidence": 1.0}'
        )
        agent = EmailTriageAgent(config=cfg)
    return agent


def _registered_tool(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


# ---------------------------------------------------------------------------
# AC — triage loop continues when one mailbox is ungranted
# ---------------------------------------------------------------------------


class TestTriageContinuesOnUngrantedMailbox:
    def test_triage_continues_when_one_mailbox_ungranted(self, tmp_path, monkeypatch):
        """The granted mailbox results ARE returned; the scan does NOT raise; the
        ungranted provider appears in ``mailbox_errors`` with an actionable message.
        (Today ``_triage_all_backends`` propagates the AuthRequiredError out of the
        loop and the whole scan fails — this test MUST fail against current main.)
        """
        granted = GrantedBackend("google", ["g1", "g2"])
        ungranted = UngrantedBackend("microsoft")
        agent = _build_agent(
            tmp_path, monkeypatch, granted_backend=granted, ungranted_backend=ungranted
        )
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            assert (
                envelope["ok"] is True
            ), f"scan raised instead of continuing: {envelope}"

            data = envelope["data"]
            results = data["results"]
            # Granted mailbox contributed its messages.
            ids = {r["id"] for r in results}
            assert (
                "g1" in ids or "g2" in ids
            ), f"no google messages in results: {results}"
            # Ungranted provider reported as a per-mailbox error, not an unhandled crash.
            mailbox_errors = data.get("mailbox_errors", [])
            assert mailbox_errors, "expected mailbox_errors but got none"
            providers_in_errors = {e["mailbox"] for e in mailbox_errors}
            assert (
                "microsoft" in providers_in_errors
            ), f"microsoft not in mailbox_errors: {mailbox_errors}"
            # Error message is actionable.
            ms_error = next(e for e in mailbox_errors if e["mailbox"] == "microsoft")
            error_msg = ms_error["error"]
            assert error_msg, "mailbox_errors entry has empty error message"
            # The message should be actionable: mentions grant or Settings.
            assert any(
                kw in error_msg
                for kw in ("grant", "Grant", "Settings", "AGENT_NOT_GRANTED")
            ), f"error message not actionable: {error_msg!r}"
        finally:
            agent.close_db()

    def test_pre_scan_continues_when_one_mailbox_ungranted(self, tmp_path, monkeypatch):
        """Same for ``_pre_scan_all_backends``: granted mailbox results come back,
        ungranted appears in ``mailbox_errors``, scan does NOT raise.
        (Today this also propagates the exception — this test MUST fail against current main.)
        """
        granted = GrantedBackend("google", ["g1", "g2"])
        ungranted = UngrantedBackend("microsoft")
        agent = _build_agent(
            tmp_path, monkeypatch, granted_backend=granted, ungranted_backend=ungranted
        )
        try:
            envelope = json.loads(_registered_tool("pre_scan_inbox")(20))
            assert (
                envelope["ok"] is True
            ), f"scan raised instead of continuing: {envelope}"

            data = envelope["data"]
            assert data.get("kind") == "email_pre_scan"

            # At least one google message appeared in some section.
            all_items = (
                data.get("urgent", [])
                + data.get("actionable", [])
                + data.get("suggested_archives", [])
            )
            google_items = [it for it in all_items if it.get("mailbox") == "google"]
            assert google_items, f"no google items in pre-scan: {all_items}"

            # Ungranted provider reported as a per-mailbox error.
            mailbox_errors = data.get("mailbox_errors", [])
            assert mailbox_errors, "expected mailbox_errors but got none"
            providers_in_errors = {e["mailbox"] for e in mailbox_errors}
            assert (
                "microsoft" in providers_in_errors
            ), f"microsoft not in mailbox_errors: {mailbox_errors}"
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# AC — available set stays connection-derived (ungranted provider still listed)
# ---------------------------------------------------------------------------


class TestAvailableSetConnectionDerived:
    def test_available_set_still_lists_connected_ungranted_provider(
        self, tmp_path, monkeypatch
    ):
        """``resolve_mail_backends()`` returns BOTH providers even when microsoft
        has no grant — connection-derived is intentional; grant enforcement is the
        connectors layer's job.

        The injected eval seam (UngrantedBackend passed as outlook_backend) means
        the config resolves both providers at construction time — the UNGRANTED
        check is deferred until the backend's list_messages is called.
        """
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        granted = GrantedBackend("google", ["g1"])
        ungranted = UngrantedBackend("microsoft")
        cfg = EmailAgentConfig(
            gmail_backend=granted,
            outlook_backend=ungranted,
            calendar_backend=object(),
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
            mail_provider=None,
        )
        backends = cfg.resolve_mail_backends()
        providers = [p for p, _ in backends]
        # Both providers appear — connection-derived, grant is deferred to token time.
        assert "google" in providers, providers
        assert "microsoft" in providers, providers


# ---------------------------------------------------------------------------
# AC — injected fakes with no grants file → scan works unchanged (regression)
# ---------------------------------------------------------------------------


class TestInjectedFakeScanUnaffected:
    def test_injected_fake_scan_unaffected(self, tmp_path, monkeypatch):
        """Both backends granted → scan works as before (hermetic regression guard)."""
        google = GrantedBackend("google", ["g1"])
        microsoft = GrantedBackend("microsoft", ["m1"])
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cfg = EmailAgentConfig(
            gmail_backend=google,
            outlook_backend=microsoft,
            calendar_backend=object(),
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
            mail_provider=None,
        )
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            # is_spam is content-judged (#1906): give the mocked chat a valid
            # no-op response so a spam-only classifier escalation doesn't crash.
            mock_sdk.return_value.send_messages.return_value.text = (
                '{"category": "PROMOTIONAL", "is_spam": false, "confidence": 1.0}'
            )
            agent = EmailTriageAgent(config=cfg)
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            assert envelope["ok"] is True, envelope
            data = envelope["data"]
            results = data["results"]
            ids = {r["id"] for r in results}
            assert "g1" in ids and "m1" in ids, f"expected both mailboxes: {ids}"
            # No errors when all are granted.
            assert not data.get("mailbox_errors"), data.get("mailbox_errors")
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# AC — calendar ungranted returns clean actionable error (not an unhandled crash)
# ---------------------------------------------------------------------------


class TestCalendarUngrantedCleanError:
    def test_calendar_ungranted_returns_clean_actionable_error(
        self, tmp_path, monkeypatch
    ):
        """A connected-but-ungranted calendar raises AuthRequiredError at call time;
        the calendar tool closure must return a clean error envelope, not crash.

        This verifies the EXISTING per-tool ConnectorsError catch already covers
        the calendar single-backend case — no multi-loop abort problem here.
        """
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        granted = GrantedBackend("google", ["g1"])

        class UngrantedCalendarBackend:
            """Simulates a connected calendar whose agent grant has been revoked."""

            def list_events(self, *, time_min=None, time_max=None):
                raise AuthRequiredError(
                    AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                    provider="google",
                    agent_id="installed:email",
                    missing_scopes=["calendar.events"],
                )

            def __getattr__(self, name):
                raise AuthRequiredError(
                    AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                    provider="google",
                    agent_id="installed:email",
                )

        cfg = EmailAgentConfig(
            gmail_backend=granted,
            calendar_backend=UngrantedCalendarBackend(),
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
            mail_provider=None,
        )
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            # is_spam is content-judged (#1906): give the mocked chat a valid
            # no-op response so a spam-only classifier escalation doesn't crash.
            mock_sdk.return_value.send_messages.return_value.text = (
                '{"category": "PROMOTIONAL", "is_spam": false, "confidence": 1.0}'
            )
            agent = EmailTriageAgent(config=cfg)
        try:
            # The list_calendar_events tool should return a clean error envelope,
            # not propagate the raw exception.
            envelope = json.loads(_registered_tool("list_calendar_events")())
            assert (
                envelope["ok"] is False
            ), f"expected error envelope but got ok=True: {envelope}"
            error_msg = envelope.get("error", "")
            assert error_msg, "error envelope has empty error message"
            # The message should be actionable.
            assert any(
                kw in error_msg
                for kw in ("grant", "Grant", "Settings", "AGENT_NOT_GRANTED")
            ), f"calendar error not actionable: {error_msg!r}"
        finally:
            agent.close_db()


class TestAllMailboxesUngrantedFailsLoudly:
    """When EVERY connected mailbox errors, the scan must fail loudly (ok:False)
    rather than return ok:True with zero results — which would read to the user
    as "your inbox is empty" instead of "every mailbox needs a grant".
    """

    def test_triage_all_ungranted_returns_error_envelope(self, tmp_path, monkeypatch):
        agent = _build_agent(
            tmp_path,
            monkeypatch,
            granted_backend=UngrantedBackend("google"),
            ungranted_backend=UngrantedBackend("microsoft"),
        )
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            assert (
                envelope["ok"] is False
            ), f"all-failed triage must fail loudly, got: {envelope}"
            err = envelope.get("error", "")
            assert "google" in err and "microsoft" in err, err
        finally:
            agent.close_db()

    def test_pre_scan_all_ungranted_returns_error_envelope(self, tmp_path, monkeypatch):
        agent = _build_agent(
            tmp_path,
            monkeypatch,
            granted_backend=UngrantedBackend("google"),
            ungranted_backend=UngrantedBackend("microsoft"),
        )
        try:
            envelope = json.loads(_registered_tool("pre_scan_inbox")(20))
            assert (
                envelope["ok"] is False
            ), f"all-failed pre-scan must fail loudly, got: {envelope}"
            err = envelope.get("error", "")
            assert "google" in err and "microsoft" in err, err
        finally:
            agent.close_db()
