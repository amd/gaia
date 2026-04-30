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
    """Isolated grants/state dirs per test."""
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.state.Path.home", lambda: tmp_path)
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
        # list/status shows catalog entries; google is always in the catalog
        rc, out, _err = _run("connectors", "status")
        assert rc == 0
        assert "google" in out
        assert "not configured" in out

    def test_status_seeded(self):
        from gaia.connectors.state import set_connector_state

        set_connector_state(
            "google",
            configured=True,
            account_id="alice@example.com",
            scopes=["s"],
        )
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
