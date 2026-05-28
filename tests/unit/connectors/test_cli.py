# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-CLI: ``gaia connectors`` subcommand tests.

Covers the thin wrappers in ``src/gaia/connectors/cli.py`` that delegate
to ``gaia.connectors.api``. The actual flow / token / grant logic is
tested elsewhere; these tests verify wiring + output shape + exit codes.
"""

from __future__ import annotations

import json

import pytest

from gaia.connectors import cli as connections_cli
from gaia.connectors.providers import _registry


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Isolated grants/mcp_servers dirs per test."""
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.activations.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    yield


def _seed_google(account_email: str) -> None:
    """Helper: write a Google keyring blob (the source of truth for
    ``configured`` after the state.json removal)."""
    from gaia.connectors.providers import get as get_provider
    from gaia.connectors.store import save_connection

    save_connection(
        provider="google",
        account_email=account_email,
        refresh_token="seed",
        scopes=["s"],
        client_id_hash=get_provider("google").client_id_hash,
    )


def _run(*argv) -> tuple[int, str, str]:
    import sys
    from io import StringIO

    out = StringIO()
    err = StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = connections_cli.main(list(argv))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out.getvalue(), err.getvalue()


class TestStatus:
    def test_status_empty(self):
        # list/status shows catalog entries; google is always in the catalog
        rc, out, _err = _run("connectors", "status")
        assert rc == 0
        assert "google" in out
        assert "not configured" in out

    def test_status_seeded(self):
        _seed_google("alice@example.com")
        rc, out, _err = _run("connectors", "status")
        assert rc == 0
        assert "alice@example.com" in out
        assert "google" in out

    def test_status_json(self):
        sentinel_token = "TOKEN-MUST-NOT-LEAK-12345"
        rc, out, _err = _run("connectors", "status", "--json")
        assert rc == 0
        rows = json.loads(out)
        assert any(row["id"] == "google" for row in rows)
        # Credentials must not appear in the output.
        assert sentinel_token not in out
        assert "refresh_token" not in out


class TestGrants:
    def test_grants_grant_then_list(self):
        rc, _out, _err = _run(
            "connectors",
            "grants",
            "grant",
            "google",
            "builtin:chat",
            "--scopes",
            "gmail.readonly",
        )
        assert rc == 0

        rc2, out2, _err2 = _run("connectors", "grants", "list", "google")
        assert rc2 == 0
        assert "builtin:chat" in out2
        assert "gmail.readonly" in out2

    def test_grants_revoke(self):
        _run(
            "connectors",
            "grants",
            "grant",
            "google",
            "builtin:chat",
            "--scopes",
            "gmail.readonly",
        )
        rc, _out, _err = _run(
            "connectors", "grants", "revoke", "google", "builtin:chat"
        )
        assert rc == 0
        rc2, out2, _err2 = _run("connectors", "grants", "list", "google")
        assert "No grants" in out2 or "builtin:chat" not in out2

    def test_grants_list_empty_default_provider(self):
        rc, out, _err = _run("connectors", "grants", "list")
        assert rc == 0
        assert "No grants" in out


class TestDisconnect:
    def test_disconnect_idempotent(self):
        rc, _out, _err = _run("connectors", "disconnect", "google")
        # Idempotent — works even when nothing to disconnect.
        assert rc == 0


class TestMissingSubcommand:
    def test_no_subcommand_returns_exit_2(self):
        rc, _out, _err = _run("connectors")
        assert rc == 2


class TestActivations:
    """Activations apply to MCP-server connectors only (#1005). All tests
    here use ``mcp-github`` (a real MCP catalog entry); OAuth rejection
    is covered by :class:`TestActivationsRejectOauth` below.
    """

    def test_activations_list_empty(self):
        rc, out, _err = _run("connectors", "activations", "list", "mcp-github")
        assert rc == 0
        assert "No activations" in out

    def test_activate_with_explicit_scopes_auto_grants(self):
        rc, out, _err = _run(
            "connectors",
            "activations",
            "activate",
            "mcp-github",
            "builtin:chat",
            "--scopes",
            "use",
        )
        assert rc == 0
        assert "Auto-granted" in out
        assert "Activated mcp-github for builtin:chat" in out

        # The grant landed too — visible via the grants subcommand.
        rc2, out2, _err2 = _run("connectors", "grants", "list", "mcp-github")
        assert rc2 == 0
        assert "builtin:chat" in out2
        assert "use" in out2

        # List shows the activation.
        rc3, out3, _err3 = _run("connectors", "activations", "list", "mcp-github")
        assert rc3 == 0
        assert "builtin:chat: active" in out3

    def test_activate_without_grant_or_scopes_returns_exit_3(self):
        # ConfigurationError → exit code 3 per the shared error-class table.
        rc, _out, err = _run(
            "connectors", "activations", "activate", "mcp-github", "builtin:chat"
        )
        assert rc == 3
        assert "Configuration error" in err

    def test_activate_existing_grant_no_auto_grant_message(self):
        _run(
            "connectors",
            "grants",
            "grant",
            "mcp-github",
            "builtin:chat",
            "--scopes",
            "use",
        )
        rc, out, _err = _run(
            "connectors", "activations", "activate", "mcp-github", "builtin:chat"
        )
        assert rc == 0
        assert "Auto-granted" not in out
        assert "Activated mcp-github for builtin:chat" in out

    def test_deactivate_preserves_grant(self):
        _run(
            "connectors",
            "activations",
            "activate",
            "mcp-github",
            "builtin:chat",
            "--scopes",
            "use",
        )
        rc, out, _err = _run(
            "connectors", "activations", "deactivate", "mcp-github", "builtin:chat"
        )
        assert rc == 0
        assert "Deactivated" in out

        # Grant survives.
        rc2, out2, _err2 = _run("connectors", "grants", "list", "mcp-github")
        assert "builtin:chat" in out2
        assert "use" in out2

        # No active rows.
        rc3, out3, _err3 = _run("connectors", "activations", "list", "mcp-github")
        assert "No activations" in out3 or "builtin:chat: active" not in out3

    def test_deactivate_idempotent(self):
        rc, _out, _err = _run(
            "connectors", "activations", "deactivate", "mcp-github", "builtin:chat"
        )
        assert rc == 0

    def test_activations_list_json(self):
        _run(
            "connectors",
            "activations",
            "activate",
            "mcp-github",
            "builtin:chat",
            "--scopes",
            "use",
        )
        rc, out, _err = _run(
            "connectors", "activations", "list", "mcp-github", "--json"
        )
        assert rc == 0
        listing = json.loads(out)
        assert listing == {"mcp-github": {"builtin:chat": True}}

    def test_activations_no_subcommand_returns_exit_2(self):
        rc, _out, _err = _run("connectors", "activations")
        assert rc == 2


class TestActivationsRejectOauth:
    """#1005 follow-up — activations gate MCP tool visibility only.

    OAuth connectors like ``google`` have no MCP tool surface — their
    per-agent access is controlled by grants. The CLI must reject the
    write with the standard ConfigurationError exit code (3) so users
    don't end up with a ledger entry nothing reads.
    """

    def test_activate_on_oauth_connector_returns_exit_3(self):
        rc, _out, err = _run(
            "connectors",
            "activations",
            "activate",
            "google",
            "builtin:chat",
            "--scopes",
            "openid",
        )
        assert rc == 3
        assert "MCP-server" in err

    def test_deactivate_on_oauth_connector_returns_exit_3(self):
        rc, _out, err = _run(
            "connectors", "activations", "deactivate", "google", "builtin:chat"
        )
        assert rc == 3
        assert "MCP-server" in err
