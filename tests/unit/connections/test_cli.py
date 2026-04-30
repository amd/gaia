# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-CLI: ``gaia connections`` subcommand tests.

Covers the thin wrappers in ``src/gaia/connections/cli.py`` that delegate
to ``gaia.connections.api``. The actual flow / token / grant logic is
tested elsewhere; these tests verify wiring + output shape + exit codes.
"""

from __future__ import annotations

import json

import pytest

from gaia.connections import cli as connections_cli
from gaia.connections.providers import _registry
from gaia.connections.store import save_connection


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Isolated grants ledger per test."""
    monkeypatch.setattr("gaia.connections.grants.Path.home", lambda: tmp_path)
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    yield


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
        rc, out, _err = _run("connections", "status")
        assert rc == 0
        assert "No connections" in out

    def test_status_seeded(self):
        from gaia.connections.providers import get as get_provider

        provider = get_provider("google")
        save_connection(
            provider="google",
            account_email="alice@example.com",
            refresh_token="x",
            scopes=["s"],
            client_id_hash=provider.client_id_hash,
        )
        rc, out, _err = _run("connections", "status")
        assert rc == 0
        assert "alice@example.com" in out
        assert "google" in out

    def test_status_json(self):
        from gaia.connections.providers import get as get_provider

        sentinel_token = "TOKEN-MUST-NOT-LEAK-12345"
        provider = get_provider("google")
        save_connection(
            provider="google",
            account_email="alice@example.com",
            refresh_token=sentinel_token,
            scopes=["s"],
            client_id_hash=provider.client_id_hash,
        )
        rc, out, _err = _run("connections", "status", "--json")
        assert rc == 0
        rows = json.loads(out)
        assert any(row["provider"] == "google" for row in rows)
        # Refresh token MUST NOT leak into the CLI output.
        assert sentinel_token not in out
        assert "refresh_token" not in out


class TestGrants:
    def test_grants_grant_then_list(self):
        rc, _out, _err = _run(
            "connections",
            "grants",
            "grant",
            "google",
            "builtin:chat",
            "--scopes",
            "gmail.readonly",
        )
        assert rc == 0

        rc2, out2, _err2 = _run("connections", "grants", "list", "google")
        assert rc2 == 0
        assert "builtin:chat" in out2
        assert "gmail.readonly" in out2

    def test_grants_revoke(self):
        _run(
            "connections",
            "grants",
            "grant",
            "google",
            "builtin:chat",
            "--scopes",
            "gmail.readonly",
        )
        rc, _out, _err = _run(
            "connections", "grants", "revoke", "google", "builtin:chat"
        )
        assert rc == 0
        rc2, out2, _err2 = _run("connections", "grants", "list", "google")
        assert "No grants" in out2 or "builtin:chat" not in out2

    def test_grants_list_empty_default_provider(self):
        rc, out, _err = _run("connections", "grants", "list")
        assert rc == 0
        assert "No grants" in out


class TestDisconnect:
    def test_disconnect_idempotent(self):
        rc, _out, _err = _run("connections", "disconnect", "google")
        # Idempotent — works even when nothing to disconnect.
        assert rc == 0


class TestMissingSubcommand:
    def test_no_subcommand_returns_exit_2(self):
        rc, _out, _err = _run("connections")
        assert rc == 2
