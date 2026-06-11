# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MCP send parity — connector-derived backend (#1603, M6).

``EmailTriageMCPAgent._resolve_send_backend()`` must mirror the REST
connector-derived logic (D2):

  - GAIA_EMAIL_MCP_FAKE_SEND set → _FakeSendBackend (test seam, short-circuits)
  - 0 connected providers  → RuntimeError with actionable message
  - 2+ connected providers → RuntimeError with ambiguous-provider message
  - 1 connected: google    → LiveGmailBackend
  - 1 connected: microsoft → LiveOutlookBackend

The fake-send seam must short-circuit BEFORE the count check (as today),
so that parity tests can run without a live mailbox connection.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.gmail_backend import LiveGmailBackend
from gaia_agent_email.mcp_server import EmailTriageMCPAgent
from gaia_agent_email.outlook_backend import LiveOutlookBackend


def _agent(monkeypatch, providers):
    """Return an EmailTriageMCPAgent with mocked connected_mailbox_providers."""
    monkeypatch.setattr(
        "gaia_agent_email.mcp_server.connected_mailbox_providers",
        lambda: providers,
    )
    return EmailTriageMCPAgent()


class TestMcpSendBackendConnectorDerived:
    """_resolve_send_backend() must be connector-derived (mirrors REST D2)."""

    def test_fake_send_seam_short_circuits_before_count_check(
        self, monkeypatch, tmp_path
    ):
        """GAIA_EMAIL_MCP_FAKE_SEND=1 must bypass provider count, even with 0 connected."""
        monkeypatch.setattr(
            "gaia_agent_email.mcp_server.connected_mailbox_providers",
            lambda: [],  # 0 providers — would error without the seam
        )
        monkeypatch.setenv("GAIA_EMAIL_MCP_FAKE_SEND", "1")
        agent = EmailTriageMCPAgent()
        # Must not raise even with 0 connected providers.
        backend = agent._resolve_send_backend()
        assert backend is not None
        # Must be the fake (has a send_message that returns a non-empty test id).
        result = backend.send_message(to="x@example.com", subject="s", body="b")
        assert result.get("id"), f"fake backend should return a non-empty id: {result}"

    def test_zero_providers_raises_actionable_runtime_error(self, monkeypatch):
        monkeypatch.delenv("GAIA_EMAIL_MCP_FAKE_SEND", raising=False)
        agent = _agent(monkeypatch, [])
        with pytest.raises(RuntimeError) as exc_info:
            agent._resolve_send_backend()
        msg = str(exc_info.value).lower()
        assert "connect" in msg or "mailbox" in msg or "no" in msg

    def test_two_providers_raises_ambiguous_runtime_error(self, monkeypatch):
        monkeypatch.delenv("GAIA_EMAIL_MCP_FAKE_SEND", raising=False)
        agent = _agent(monkeypatch, ["google", "microsoft"])
        with pytest.raises(RuntimeError) as exc_info:
            agent._resolve_send_backend()
        msg = str(exc_info.value)
        # Must name the connected providers so the error is actionable.
        assert "google" in msg or "microsoft" in msg

    def test_google_only_returns_live_gmail_backend(self, monkeypatch):
        monkeypatch.delenv("GAIA_EMAIL_MCP_FAKE_SEND", raising=False)
        agent = _agent(monkeypatch, ["google"])
        backend = agent._resolve_send_backend()
        assert isinstance(backend, LiveGmailBackend)

    def test_microsoft_only_returns_live_outlook_backend(self, monkeypatch):
        monkeypatch.delenv("GAIA_EMAIL_MCP_FAKE_SEND", raising=False)
        agent = _agent(monkeypatch, ["microsoft"])
        backend = agent._resolve_send_backend()
        assert isinstance(backend, LiveOutlookBackend)


class TestMcpSendOutlook502Parity:
    """MCP send mirrors the REST D4 fix: Outlook 202 (no id) is success."""

    def test_outlook_sent_true_returns_success(self, monkeypatch):
        """A backend returning sent=True, empty id → MCP returns sent:True."""
        monkeypatch.delenv("GAIA_EMAIL_MCP_FAKE_SEND", raising=False)

        class _FakeOutlookBackend:
            def send_message(self, *, to, subject, body, **_kw):
                return {"id": "", "sent": True, "to": to, "subject": subject}

        agent = EmailTriageMCPAgent()
        # Seed the confirmation store with a valid token.
        from gaia_agent_email.api_routes import _payload_fingerprint
        from gaia_agent_email.contract import EmailAddress

        to = [EmailAddress(email="bob@example.com")]
        fp = _payload_fingerprint(to, "Hello", "World")
        token = agent._confirmation_store.issue(fp)

        monkeypatch.setattr(agent, "_resolve_send_backend", lambda: _FakeOutlookBackend())

        result = agent._send(
            {
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
            }
        )
        assert result.get("sent") is True

    def test_no_id_no_sent_returns_error(self, monkeypatch):
        """A backend returning {id:'', no sent} → MCP returns sent:False."""
        monkeypatch.delenv("GAIA_EMAIL_MCP_FAKE_SEND", raising=False)

        class _SilentNoOp:
            def send_message(self, *, to, subject, body, **_kw):
                return {"id": ""}

        agent = EmailTriageMCPAgent()
        from gaia_agent_email.api_routes import _payload_fingerprint
        from gaia_agent_email.contract import EmailAddress

        to = [EmailAddress(email="bob@example.com")]
        fp = _payload_fingerprint(to, "Hello", "World")
        token = agent._confirmation_store.issue(fp)

        monkeypatch.setattr(agent, "_resolve_send_backend", lambda: _SilentNoOp())

        result = agent._send(
            {
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
            }
        )
        assert result.get("sent") is not True
        assert result.get("error")
