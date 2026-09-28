# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The Microsoft connectors must never request ``openid`` (#4079).

AMD's Entra tenant rejects the authorization request with ``AADSTS65002``
when ``openid`` is in the scope set, in both the browser and device-code
flows, so a connect cannot succeed at all. ``openid`` was requested only to
obtain an id_token carrying the account email, which the provider's
``userinfo_url`` (Graph ``/me``) already resolves without it.

This file is the pin: the scope tuples are asserted EXACTLY, not by
membership, so a later change cannot quietly add ``openid`` back by assuming
an id_token is available. It also covers the two things the id_token used to
provide, proving each still resolves without one:

  - the account email, via the Graph ``/me`` fallback;
  - the account kind (#2466), via the connector's own OAuth authority —
    ``consumers`` only ever signs in a personal account, ``organizations``
    only ever a work/school one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gaia.connectors import flow
from gaia.connectors.providers.microsoft import (
    ACCOUNT_TYPE_PERSONAL,
    ACCOUNT_TYPE_WORK,
    MicrosoftOAuthProvider,
)

_GRAPH_USER_READ = "https://graph.microsoft.com/User.Read"
_EXPECTED_IDENTITY_SCOPES = ("offline_access", _GRAPH_USER_READ)


@pytest.fixture
def _ms_env(monkeypatch):
    monkeypatch.setenv(
        "GAIA_MICROSOFT_CLIENT_ID", "11112222-bbbb-3333-cccc-4444dddd5555"
    )
    monkeypatch.setenv(
        "GAIA_MICROSOFT_WORK_CLIENT_ID", "11112222-bbbb-3333-cccc-4444dddd5555"
    )
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GAIA_MICROSOFT_WORK_CLIENT_SECRET", raising=False)


# ----------------------------------------------------------------------
# The pin
# ----------------------------------------------------------------------


def test_provider_default_scopes_are_exactly_the_identity_set(_ms_env):
    from gaia.connectors import providers

    for connector_id in ("microsoft", "microsoft_work"):
        prov = providers.get(connector_id)
        assert tuple(prov.default_scopes) == _EXPECTED_IDENTITY_SCOPES, connector_id


@pytest.mark.parametrize("spec_name", ["MICROSOFT_SPEC", "MICROSOFT_WORK_SPEC"])
def test_catalog_default_scopes_are_exactly_the_identity_set(spec_name):
    import gaia.connectors.catalog.microsoft as catalog

    spec = getattr(catalog, spec_name)
    assert tuple(spec.default_scopes) == _EXPECTED_IDENTITY_SCOPES


def test_openid_is_absent_from_every_microsoft_connect_scope_in_the_fixture():
    """The shared fixture drives the TUI's reconnect scopes (Go) too, so a
    stale ``openid`` there would resurrect the failing request in the TUI even
    with the Python side fixed."""
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "connectors"
        / "email_scopes.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    for connector_id in ("microsoft", "microsoft_work"):
        entry = fixture[connector_id]
        assert "openid" not in entry["connect_union"], connector_id
        assert "openid" not in entry["identity_scopes"]["catalog"], connector_id
        assert "openid" not in entry["identity_scopes"]["provider"], connector_id


def test_offline_access_survives_the_removal(_ms_env):
    """``offline_access`` is the one scope the shared flow genuinely requires —
    it raises without a refresh token. Removing ``openid`` must not take it."""
    from gaia.connectors import providers

    assert "offline_access" in providers.get("microsoft").default_scopes


# ----------------------------------------------------------------------
# Identity without an id_token
# ----------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _fake_async_client(response: _FakeResponse, recorder: dict):
    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, url, headers=None):
            recorder["url"] = url
            recorder["headers"] = headers or {}
            return response

    return _Client


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"mail": "user@contoso.com"}, "user@contoso.com"),
        # Personal accounts null `mail` and carry the address in the UPN.
        (
            {"mail": None, "userPrincipalName": "user@outlook.com"},
            "user@outlook.com",
        ),
    ],
)
def test_account_email_resolves_from_graph_me_without_an_id_token(
    monkeypatch, payload, expected
):
    recorder: dict = {}
    monkeypatch.setattr(
        flow.httpx,
        "AsyncClient",
        _fake_async_client(_FakeResponse(200, payload), recorder),
    )
    provider = MicrosoftOAuthProvider(client_id="test-client")

    resolved = asyncio.run(flow._resolve_account_email(provider, "", "access-token"))

    assert resolved == expected
    assert recorder["url"].startswith("https://graph.microsoft.com/v1.0/me")
    assert recorder["headers"]["Authorization"] == "Bearer access-token"


@pytest.mark.parametrize(
    "connector_id, expected",
    [("microsoft", ACCOUNT_TYPE_PERSONAL), ("microsoft_work", ACCOUNT_TYPE_WORK)],
)
def test_account_type_resolves_from_the_authority_without_an_id_token(
    _ms_env, connector_id, expected
):
    from gaia.connectors import providers

    provider = providers.get(connector_id)
    assert flow._resolve_account_type(provider, "") == expected


def test_a_stored_directory_id_still_classifies_as_work():
    """``microsoft_work`` narrowed to one Entra tenant is still a work sign-in."""
    provider = MicrosoftOAuthProvider(
        client_id="test-client",
        tenant="72f988bf-86f1-41af-91ab-2d7cd011db47",
        provider_id="microsoft_work",
    )
    assert flow._resolve_account_type(provider, "") == ACCOUNT_TYPE_WORK


def test_an_ambiguous_authority_records_the_kind_as_unknown():
    """``common`` accepts both kinds, so it discriminates nothing — unknown is
    a real answer here, never a guess."""
    provider = MicrosoftOAuthProvider(client_id="test-client", tenant="common")
    assert flow._resolve_account_type(provider, "") is None


def test_an_id_token_claim_still_wins_over_the_authority(_ms_env):
    """A tenant-narrowed personal sign-in must not be relabelled by the
    authority when the token actually says what the account is."""
    import base64

    from gaia.connectors import providers
    from gaia.connectors.providers.microsoft import _MSA_TENANT_ID

    def seg(payload: dict) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(payload).encode())
            .decode("ascii")
            .rstrip("=")
        )

    id_token = f"{seg({'alg': 'none'})}.{seg({'tid': _MSA_TENANT_ID})}.sig"
    provider = providers.get("microsoft_work")
    assert flow._resolve_account_type(provider, id_token) == ACCOUNT_TYPE_PERSONAL
