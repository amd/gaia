# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-9 E2E smoke tests — connectors framework end-to-end.

These tests exercise the full vertical slice: CLI → handler → state store
→ grants ledger → router, using only in-memory / tmp-path fakes for the
keyring and filesystem. They verify that the three caller surfaces
(CLI, SDK, HTTP router) are consistent after each operation.
"""

from __future__ import annotations

import json

import pytest

from gaia.connectors import cli as connectors_cli
from gaia.connectors.providers import _registry as _oauth_provider_registry

# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────


def _run(*argv) -> tuple[int, str, str]:
    import sys
    from io import StringIO

    out, err = StringIO(), StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = connectors_cli.main(list(argv))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out.getvalue(), err.getvalue()


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Isolate filesystem and env for every smoke test."""
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    # Clear the OAuth provider cache (not the catalog registry).
    _oauth_provider_registry.clear()
    yield


def _seed_google_connection(account_email: str, scopes=("openid",)) -> None:
    """Helper: write a Google keyring blob the same way the OAuth flow
    would, so live readers (CLI status, router catalog) see the
    connector as configured. Replaces the old ``set_connector_state``
    seeding pattern now that the keyring blob is the source of truth.
    """
    from gaia.connectors.providers import get as get_provider
    from gaia.connectors.store import save_connection

    provider = get_provider("google")
    save_connection(
        provider="google",
        account_email=account_email,
        refresh_token="seed-refresh",
        scopes=list(scopes),
        client_id_hash=provider.client_id_hash,
    )


# ─────────────────────────────────────────────────────────────────
# Smoke: catalog is populated and CLI reflects it
# ─────────────────────────────────────────────────────────────────


class TestCatalogSmoke:
    def test_status_lists_google(self):
        """CLI status lists google connector from catalog."""
        rc, out, _ = _run("connectors", "status")
        assert rc == 0
        assert "google" in out

    def test_status_json_has_connectors(self):
        """JSON mode returns a non-empty list."""
        rc, out, _ = _run("connectors", "status", "--json")
        assert rc == 0
        rows = json.loads(out)
        assert isinstance(rows, list)
        assert len(rows) > 0
        ids = {r["id"] for r in rows}
        assert "google" in ids

    def test_status_json_no_secrets(self):
        """Connector status JSON must not contain any token/secret fields."""
        rc, out, _ = _run("connectors", "status", "--json")
        assert rc == 0
        assert "refresh_token" not in out
        assert "access_token" not in out


# ─────────────────────────────────────────────────────────────────
# Smoke: grants ledger round-trip via CLI
# ─────────────────────────────────────────────────────────────────


class TestGrantsSmoke:
    def test_grant_and_list(self):
        """Grant a scope then verify it appears in the list."""
        rc, _, _ = _run(
            "connectors",
            "grants",
            "grant",
            "google",
            "builtin:chat",
            "--scopes",
            "https://www.googleapis.com/auth/gmail.readonly",
        )
        assert rc == 0

        rc2, out2, _ = _run("connectors", "grants", "list", "google")
        assert rc2 == 0
        assert "builtin:chat" in out2
        assert "gmail.readonly" in out2

    def test_revoke_clears_grant(self):
        """Revoke removes the grant from the ledger."""
        _run(
            "connectors",
            "grants",
            "grant",
            "google",
            "builtin:chat",
            "--scopes",
            "gmail.readonly",
        )
        rc, _, _ = _run("connectors", "grants", "revoke", "google", "builtin:chat")
        assert rc == 0

        rc2, out2, _ = _run("connectors", "grants", "list", "google")
        assert rc2 == 0
        assert "builtin:chat" not in out2

    def test_grants_empty_by_default(self):
        """Fresh install has no grants."""
        rc, out, _ = _run("connectors", "grants", "list")
        assert rc == 0
        assert "No grants" in out


# ─────────────────────────────────────────────────────────────────
# Smoke: state store + CLI consistency
# ─────────────────────────────────────────────────────────────────


class TestStateSyncSmoke:
    def test_seeded_state_appears_in_cli_status(self):
        """A keyring-saved connection is reflected in CLI status."""
        _seed_google_connection("smoke@example.com")
        rc, out, _ = _run("connectors", "status")
        assert rc == 0
        assert "smoke@example.com" in out

    def test_seeded_state_appears_in_json(self):
        """JSON status output reflects keyring-saved connection."""
        _seed_google_connection("json@example.com")
        rc, out, _ = _run("connectors", "status", "--json")
        assert rc == 0
        rows = json.loads(out)
        google = next((r for r in rows if r["id"] == "google"), None)
        assert google is not None
        assert google["configured"] is True
        assert google["account_id"] == "json@example.com"


# ─────────────────────────────────────────────────────────────────
# Smoke: disconnect is idempotent
# ─────────────────────────────────────────────────────────────────


class TestDisconnectSmoke:
    def test_disconnect_unknown_does_not_crash(self):
        """Disconnect on an unconfigured connector exits 0 (idempotent)."""
        rc, _, _ = _run("connectors", "disconnect", "google")
        assert rc == 0

    def test_disconnect_clears_state(self):
        """Disconnect removes a previously seeded keyring entry."""
        from gaia.connectors.store import peek_connection

        _seed_google_connection("bye@example.com")
        assert peek_connection("google") is not None

        rc, _, _ = _run("connectors", "disconnect", "google")
        assert rc == 0

        blob = peek_connection("google")
        assert blob is None, f"Expected entry cleared after disconnect, got: {blob}"


# ─────────────────────────────────────────────────────────────────
# Smoke: router reflects CLI operations
# ─────────────────────────────────────────────────────────────────


class TestRouterSyncSmoke:
    def test_router_lists_catalog_after_cli_configure(self, ui_api_client):
        """A keyring-saved connection is visible through the HTTP router."""
        _seed_google_connection("router@example.com")
        r = ui_api_client.get("/api/connectors")
        assert r.status_code == 200
        data = r.json()
        assert "connectors" in data
        google = next((c for c in data["connectors"] if c["id"] == "google"), None)
        assert google is not None
        assert google["configured"] is True
        assert google["account_id"] == "router@example.com"

    def test_router_grants_match_cli_grants(self, ui_api_client):
        """Grants written by CLI are visible through the router grants endpoint."""
        from gaia.connectors.grants import grant_agent

        grant_agent(
            "google",
            "builtin:chat",
            ["https://www.googleapis.com/auth/gmail.readonly"],
        )
        r = ui_api_client.get("/api/connectors/google/grants")
        assert r.status_code == 200
        grants = r.json()["grants"]
        assert "builtin:chat" in grants
        assert (
            "https://www.googleapis.com/auth/gmail.readonly" in grants["builtin:chat"]
        )
