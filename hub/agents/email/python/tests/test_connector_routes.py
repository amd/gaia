# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``POST /v1/email/connectors/{provider}/configure`` scope resolution (#2730
site 13).

An explicit empty ``scopes: []`` in the request body must take the same
``default_scopes ∪ ALL_SCOPES`` union path an omitted ``scopes`` field does —
not slip through as a literal ``[]`` that would then hit
``oauth_pkce.configure``'s own D0 empty-scopes guard (or, pre-#2730, silently
narrow the connection to the provider's identity-only defaults).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# parents[0] = tests/, [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")
pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import connector_routes  # noqa: E402


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(connector_routes.router)
    return TestClient(app)


def test_explicit_empty_scopes_takes_the_union_path(client, monkeypatch):
    captured = {}

    async def _fake_configure(provider, config):
        captured["config"] = config
        return {"flow_id": "f1", "authorization_url": "https://auth.example"}

    monkeypatch.setattr("gaia.connectors.handler.configure", _fake_configure)

    resp = client.post(
        "/v1/email/connectors/google/configure",
        json={"client_id": "id", "client_secret": "secret", "scopes": []},
    )
    assert resp.status_code == 200, resp.text
    assert captured["config"]["scopes"] == connector_routes._build_scope_union(
        "google"
    )
    assert captured["config"]["scopes"] != []


def test_omitted_scopes_takes_the_same_union_path(client, monkeypatch):
    captured = {}

    async def _fake_configure(provider, config):
        captured["config"] = config
        return {"flow_id": "f1", "authorization_url": "https://auth.example"}

    monkeypatch.setattr("gaia.connectors.handler.configure", _fake_configure)

    resp = client.post(
        "/v1/email/connectors/google/configure",
        json={"client_id": "id", "client_secret": "secret"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["config"]["scopes"] == connector_routes._build_scope_union(
        "google"
    )


def test_explicit_nonempty_scopes_is_honored_unchanged(client, monkeypatch):
    captured = {}

    async def _fake_configure(provider, config):
        captured["config"] = config
        return {"flow_id": "f1", "authorization_url": "https://auth.example"}

    monkeypatch.setattr("gaia.connectors.handler.configure", _fake_configure)

    resp = client.post(
        "/v1/email/connectors/google/configure",
        json={"client_id": "id", "client_secret": "secret", "scopes": ["openid"]},
    )
    assert resp.status_code == 200, resp.text
    assert captured["config"]["scopes"] == ["openid"]
