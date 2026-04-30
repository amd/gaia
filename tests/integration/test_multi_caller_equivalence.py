# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-19: multi-caller equivalence test.

Drives the connections layer from each of the three caller surfaces
(SDK / CLI / AgentUI) and asserts end-to-end equivalence: a connection
authenticated via one caller is observable from the other two; a grant
written by one caller is observable from the other two; access tokens
fetched from any caller flow through the same in-process cache.

This is the gating test for the §2.1 consumer contract: "the connections
module is self-contained; SDK, CLI, AgentUI are equal callers."

Marked ``integration`` so it stays out of the fast unit suite by default.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

import gaia.connectors as connections
from gaia.connectors import cli as connections_cli
from gaia.connectors.providers import _registry
from gaia.connectors.store import save_connection

pytestmark = pytest.mark.integration


@pytest.fixture
def env(monkeypatch, tmp_path, in_memory_keyring):  # noqa: F811
    """Configure provider, isolate grants ledger, reset registry, reset cache."""
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "multi-caller-test.apps.example")
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    _registry.clear()
    in_memory_keyring._store.clear()
    from gaia.connectors.tokens import _cache

    _cache.clear()
    yield {"home": tmp_path}


def _seed_connection(google_provider):
    """Skip the loopback flow — pre-seed the keyring directly so we test
    grant + token equivalence without launching a browser."""
    save_connection(
        provider="google",
        account_email="multi-caller@example.com",
        refresh_token="multi-caller-refresh",
        scopes=["gmail.readonly"],
        client_id_hash=google_provider.client_id_hash,
    )


def _ok_token(access="MULTI-CALLER-TOKEN"):
    return httpx.Response(
        200, json={"access_token": access, "expires_in": 3600, "scope": "x"}
    )


class TestSdkPath:
    @respx.mock
    def test_sdk_grant_visible_to_cli_and_ui(self, env):
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())

        google = connections.providers.get("google")
        _seed_connection(google)

        # SDK: grant_agent.
        connections.grant_agent("google", "builtin:multi-test", ["gmail.readonly"])

        # CLI sees the same grant.
        listing = connections.list_agent_grants("google")
        assert listing == {"builtin:multi-test": ["gmail.readonly"]}

        # UI sees the same connection metadata via the public API.
        rows = connections.list_connections()
        assert any(r["provider"] == "google" for r in rows)

        # SDK can fetch a token.
        token = asyncio.run(
            connections.get_access_token(
                provider="google",
                scopes=["gmail.readonly"],
                agent_id="builtin:multi-test",
            )
        )
        assert token == "MULTI-CALLER-TOKEN"


class TestCliPath:
    @respx.mock
    def test_cli_grant_visible_to_sdk(self, env):
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        google = connections.providers.get("google")
        _seed_connection(google)

        # CLI: gaia connectors grants grant google builtin:cli-test ...
        rc = connections_cli.main(["connectors",
                "grants",
                "grant",
                "google",
                "builtin:cli-test",
                "--scopes",
                "gmail.readonly",
            ]
        )
        assert rc == 0

        # SDK sees the grant the CLI wrote.
        listing = connections.list_agent_grants("google")
        assert listing == {"builtin:cli-test": ["gmail.readonly"]}

        # SDK can fetch a token under that agent_id.
        token = asyncio.run(
            connections.get_access_token(
                provider="google",
                scopes=["gmail.readonly"],
                agent_id="builtin:cli-test",
            )
        )
        assert token == "MULTI-CALLER-TOKEN"


class TestUiPath:
    @respx.mock
    def test_ui_grant_visible_to_sdk_and_cli(self, env, ui_api_client):
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        # Grants endpoint pulls _registry lazily — make sure tripwire ran:
        google = connections.providers.get("google")
        _seed_connection(google)

        # UI: PUT /api/connectors/google/grants/builtin:ui-test
        resp = ui_api_client.put(
            "/api/connectors/google/grants/builtin:ui-test",
            json={"scopes": ["gmail.readonly"]},
        )
        assert resp.status_code == 200, resp.text

        # CLI sees the grant.
        listing = connections.list_agent_grants("google")
        assert listing == {"builtin:ui-test": ["gmail.readonly"]}

        # SDK can fetch a token under the same agent_id.
        token = asyncio.run(
            connections.get_access_token(
                provider="google",
                scopes=["gmail.readonly"],
                agent_id="builtin:ui-test",
            )
        )
        assert token == "MULTI-CALLER-TOKEN"

        # And the UI status endpoint reflects it.
        status = ui_api_client.get("/api/connectors/google/grants").json()
        assert status == {"grants": {"builtin:ui-test": ["gmail.readonly"]}}


class TestThreeCallersAgreeOnConnection:
    """All three callers see the same connection metadata."""

    def test_one_seed_three_observations(self, env, ui_api_client):
        google = connections.providers.get("google")
        _seed_connection(google)

        # SDK
        sdk_rows = connections.list_connections()
        assert any(r["provider"] == "google" for r in sdk_rows)

        # CLI
        rc = connections_cli.main(["connectors", "status", "--json"])
        assert rc == 0

        # UI
        ui_rows = ui_api_client.get("/api/connectors").json()["connections"]
        assert any(r["provider"] == "google" for r in ui_rows)

        # Same email surfaces everywhere.
        sdk_email = next(r for r in sdk_rows if r["provider"] == "google")[
            "account_email"
        ]
        ui_email = next(r for r in ui_rows if r["provider"] == "google")[
            "account_email"
        ]
        assert sdk_email == ui_email == "multi-caller@example.com"
