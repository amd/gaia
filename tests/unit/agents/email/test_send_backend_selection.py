# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for connector-derived ``get_send_backend()`` (#1603, M2).

``get_send_backend()`` must be derived from the connected OAuth mailbox, not
hardcoded to Gmail:

  - 0 providers → HTTP 503 (no mailbox connected)
  - 2+ providers → HTTP 400 (ambiguous; can't choose)
  - 1 provider "google"  → LiveGmailBackend
  - 1 provider "microsoft" → LiveOutlookBackend

The ``resolve_send_backend`` module-level alias is KEPT as the injectable
test seam (existing tests monkeypatch it).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("gaia_agent_email")

from fastapi import HTTPException

import gaia_agent_email.api_routes as email_routes
from gaia_agent_email.gmail_backend import LiveGmailBackend
from gaia_agent_email.outlook_backend import LiveOutlookBackend


class TestGetSendBackendConnectorDerived:
    """get_send_backend() must be connector-derived, fail-loud."""

    def test_no_providers_raises_503(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.api_routes.connected_mailbox_providers",
            lambda: [],
        )
        with pytest.raises(HTTPException) as exc_info:
            email_routes.get_send_backend()
        assert exc_info.value.status_code == 503
        detail = exc_info.value.detail
        assert "connect" in detail.lower() or "mailbox" in detail.lower()

    def test_two_providers_raises_400(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.api_routes.connected_mailbox_providers",
            lambda: ["google", "microsoft"],
        )
        with pytest.raises(HTTPException) as exc_info:
            email_routes.get_send_backend()
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        # Must name the providers so the error is actionable.
        assert "google" in detail.lower() or "microsoft" in detail.lower()

    def test_google_only_returns_live_gmail_backend(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.api_routes.connected_mailbox_providers",
            lambda: ["google"],
        )
        backend = email_routes.get_send_backend()
        assert isinstance(backend, LiveGmailBackend)

    def test_microsoft_only_returns_live_outlook_backend(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.api_routes.connected_mailbox_providers",
            lambda: ["microsoft"],
        )
        backend = email_routes.get_send_backend()
        assert isinstance(backend, LiveOutlookBackend)

    def test_resolve_send_backend_alias_preserved(self):
        """The module-level alias must remain for existing test monkeypatching."""
        # resolve_send_backend is the injectable seam; it must stay as a module
        # attribute pointing to the same default callable.
        assert hasattr(email_routes, "resolve_send_backend")
        assert callable(email_routes.resolve_send_backend)

    def test_503_detail_is_actionable(self, monkeypatch):
        """503 must tell the user what to do."""
        monkeypatch.setattr(
            "gaia_agent_email.api_routes.connected_mailbox_providers",
            lambda: [],
        )
        with pytest.raises(HTTPException) as exc_info:
            email_routes.get_send_backend()
        detail = exc_info.value.detail
        # Must name the settings path (Settings → Connectors or similar) so
        # the user knows what to do.
        assert "connect" in detail.lower()

    def test_400_lists_connected_providers(self, monkeypatch):
        """400 must list which providers are connected so the error is actionable."""
        monkeypatch.setattr(
            "gaia_agent_email.api_routes.connected_mailbox_providers",
            lambda: ["google", "microsoft"],
        )
        with pytest.raises(HTTPException) as exc_info:
            email_routes.get_send_backend()
        detail = exc_info.value.detail
        assert "google" in detail and "microsoft" in detail
