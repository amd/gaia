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


class TestConnectSelfDocuments:
    """`gaia connectors connect google` with no client credentials must be
    self-documenting for a headless user (#2347) — the console setup steps and
    the exact commands, not a UI-only dead end."""

    def test_connect_without_client_creds_prints_setup_guide(self, monkeypatch):
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)
        # Ensure no keyring-stored client creds resolve either.
        monkeypatch.setattr(
            "gaia.connectors.store.peek_provider_credentials", lambda pid: None
        )
        _registry.clear()

        rc, _out, err = _run("connectors", "connect", "google")

        assert rc == 3  # ConfigurationError exit code
        assert "not configured" in err
        assert "console.cloud.google.com" in err  # console steps
        assert "gaia connectors configure google --client-id" in err  # exact command
        assert "gaia connectors grants grant google" in err
        assert "amd-gaia.ai/docs/connectors/google" in err


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


class TestConfigure:
    """``gaia connectors configure google --client-id … --client-secret …`` (#1084).

    The flags persist the OAuth *client* credentials to the same keyring slot the
    Google provider resolves from (``store.peek_provider_credentials("google")``),
    completing OAuth config WITHOUT the Agent UI and WITHOUT any live OAuth/network
    step. The actual browser login stays a separate ``gaia connectors connect``.
    """

    def test_client_id_secret_persist_to_provider_store(self, monkeypatch):
        # No env creds — the persisted keyring blob must be the sole source the
        # provider reads from afterward.
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)

        rc, out, _err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "cli-id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-cli",
        )
        assert rc == 0
        assert "Configured google" in out

        # Landed in the exact store the provider resolves from.
        from gaia.connectors.store import peek_provider_credentials

        creds = peek_provider_credentials("google")
        assert creds == {
            "client_id": "cli-id.apps.googleusercontent.com",
            "client_secret": "GOCSPX-cli",
        }

        # And the provider actually picks them up on next construction.
        from gaia.connectors.providers import get as get_provider

        prov = get_provider("google")
        assert prov.client_id == "cli-id.apps.googleusercontent.com"
        assert prov.client_secret == "GOCSPX-cli"

    def test_client_id_secret_does_not_start_oauth_flow(self, monkeypatch):
        # AC: no live OAuth/network. The credential-persist path must NOT invoke
        # the PKCE flow starter (which opens a browser + loopback server).
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        called = {"start": False}

        def _boom(*_a, **_k):
            called["start"] = True
            raise AssertionError("start_authorization must not run on configure")

        monkeypatch.setattr("gaia.connectors.flow.start_authorization", _boom)

        rc, _out, _err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-x",
        )
        assert rc == 0
        assert called["start"] is False

    def test_secret_not_echoed_to_stdout(self, monkeypatch):
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        rc, out, _err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-super-secret",
        )
        assert rc == 0
        assert "GOCSPX-super-secret" not in out

    def test_client_id_without_secret_is_usage_error(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
        )
        assert rc == 2
        assert "client-secret" in err

    def test_client_secret_without_id_is_usage_error(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-secret",
            "GOCSPX-x",
        )
        assert rc == 2
        assert "client-id" in err

    def test_keyring_failure_surfaces_as_connectors_error(self, monkeypatch):
        # Fail-loudly: a keyring write failure must propagate as a
        # ConnectorsError (exit 5), never a silent success.
        from gaia.connectors.errors import ConnectorsError

        def _boom(*_a, **_k):
            raise ConnectorsError("Keyring set_password failed: backend locked")

        monkeypatch.setattr("gaia.connectors.store.save_provider_credentials", _boom)
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-x",
        )
        assert rc == 5
        assert "Connectors error" in err

    def test_unknown_connector_returns_exit_1(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "does-not-exist",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-x",
        )
        assert rc == 1
        assert "unknown connector" in err

    def test_client_id_with_set_is_usage_error(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-x",
            "--set",
            "FOO=bar",
        )
        assert rc == 2
        assert "--set" in err or "--json" in err


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
