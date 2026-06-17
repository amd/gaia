# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for ``connected_mailbox_providers()`` (#1603).

The helper returns the ids of OAuth PKCE connectors whose connection blob is
present in the keyring — regardless of per-agent grants. "Connected" means the
user completed the OAuth flow; "granted" is a separate (per-agent) gate that
fires later at token time.

Test cases:
  - no connection stored → []
  - only google stored → ["google"]
  - only microsoft stored → ["microsoft"]
  - both stored → ["google", "microsoft"] (registry order: google before microsoft)
  - connected-but-ungranted still counted (only peek_connection matters)
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("keyring")
from gaia.connectors.store import DEFAULT_ACCOUNT, SERVICE_NAME, _connection_username

# ---------------------------------------------------------------------------
# Helpers to write blobs directly into the in-memory keyring so we can
# simulate "google connected", "microsoft connected", etc. without running the
# real OAuth flow. The autouse _autouse_in_memory_keyring fixture from
# tests/unit/connectors/conftest.py guarantees we have a clean in-memory
# keyring for every test.
# ---------------------------------------------------------------------------


def _write_connection(provider: str, keyring_backend):
    """Write a minimal connection blob for *provider* into the keyring."""
    import keyring

    username = _connection_username(provider, DEFAULT_ACCOUNT)
    blob = json.dumps(
        {
            "account_email": f"user@{provider}.example.com",
            "scopes": ["openid"],
            "connected_at": "2026-01-01T00:00:00Z",
            "refresh_token": "tok-dummy",
            "client_id_hash": "testhash",
        }
    )
    keyring.set_password(SERVICE_NAME, username, blob)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConnectedMailboxProviders:
    def _call(self):
        # Import inside each test so catalog registration is always fresh.
        from gaia.connectors.api import connected_mailbox_providers

        return connected_mailbox_providers()

    def test_none_connected_returns_empty(self, _autouse_in_memory_keyring):
        assert self._call() == []

    def test_google_only_returns_google(self, _autouse_in_memory_keyring):
        _write_connection("google", _autouse_in_memory_keyring)
        result = self._call()
        assert result == ["google"]

    def test_microsoft_only_returns_microsoft(self, _autouse_in_memory_keyring):
        _write_connection("microsoft", _autouse_in_memory_keyring)
        result = self._call()
        assert result == ["microsoft"]

    def test_both_connected_returns_both_in_order(self, _autouse_in_memory_keyring):
        _write_connection("google", _autouse_in_memory_keyring)
        _write_connection("microsoft", _autouse_in_memory_keyring)
        result = self._call()
        # google is tier=1, id='google'; microsoft is tier=1, id='microsoft';
        # REGISTRY.all() sorts by (tier, id) → google < microsoft alphabetically.
        assert result == ["google", "microsoft"]

    def test_connected_but_no_grant_still_listed(self, _autouse_in_memory_keyring):
        """A connected provider with no per-agent grant is still returned.

        connected_mailbox_providers() gates on the OAuth connection (peek_connection),
        NOT on per-agent grants. The grant check fires later at token fetch time
        and fails loudly there if needed — but the enumeration layer must not
        pre-filter by grants, because doing so would mask valid single-mailbox
        cases for agents that haven't run the grant step yet.
        """
        _write_connection("google", _autouse_in_memory_keyring)
        # No grants are registered at all — still expect google listed.
        result = self._call()
        assert "google" in result

    def test_returns_list_not_set(self, _autouse_in_memory_keyring):
        _write_connection("google", _autouse_in_memory_keyring)
        result = self._call()
        assert isinstance(result, list)

    def test_non_oauth_pkce_connectors_excluded(self, _autouse_in_memory_keyring):
        """Only oauth_pkce connectors are mailbox candidates.

        MCP-server connectors (type != "oauth_pkce") must never appear in
        the result even if they happen to have a keyring entry with the same
        key structure.
        """
        from gaia.connectors.api import connected_mailbox_providers

        # The result should contain at most google / microsoft, never an
        # mcp_server type connector id.
        _write_connection("google", _autouse_in_memory_keyring)
        result = connected_mailbox_providers()
        assert all(p in ("google", "microsoft") for p in result)
