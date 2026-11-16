# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every surface spells a connector's identity scopes the same way (#2738).

The catalog spec's ``default_scopes`` feeds the Agent UI and bare ``connect``
union; the OAuthProvider's feeds ``--grant-agent`` and a first-time connect.
Two literal spellings of one concept is how the mail/calendar drift in #2730
started, so both must equal the shared fixture's ``identity_scopes`` exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors import providers
from gaia.connectors.registry import REGISTRY

_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "connectors"
        / "email_scopes.json"
    ).read_text(encoding="utf-8")
)
_CONNECTORS = ("google", "microsoft", "microsoft_work")


@pytest.fixture(autouse=True)
def _client_ids(monkeypatch):
    for var in (
        "GAIA_GOOGLE_CLIENT_ID",
        "GAIA_MICROSOFT_CLIENT_ID",
        "GAIA_MICROSOFT_WORK_CLIENT_ID",
    ):
        monkeypatch.setenv(var, "11112222-bbbb-3333-cccc-4444dddd5555")


@pytest.mark.parametrize("connector_id", _CONNECTORS)
def test_catalog_and_provider_identity_scopes_are_one_spelling(connector_id):
    catalog = tuple(REGISTRY.get(connector_id).default_scopes)
    provider = tuple(providers.get(connector_id).default_scopes)
    assert catalog == provider
    assert list(catalog) == _FIXTURE[connector_id]["identity_scopes"]


@pytest.mark.parametrize("connector_id", _CONNECTORS)
def test_fixture_identity_scopes_are_a_single_list(connector_id):
    identity = _FIXTURE[connector_id]["identity_scopes"]
    assert isinstance(identity, list) and identity
    assert all(isinstance(s, str) for s in identity)


@pytest.mark.parametrize("connector_id", _CONNECTORS)
def test_connect_union_carries_the_identity_scopes_verbatim(connector_id):
    entry = _FIXTURE[connector_id]
    union = entry["connect_union"]
    assert union[: len(entry["identity_scopes"])] == entry["identity_scopes"]
    assert len(union) == len(set(union)), "the union must not repeat a scope"


def test_google_requests_no_short_alias_for_a_url_it_already_asks_for():
    # "email" and userinfo.email are the same scope; requesting both spellings
    # is the drift this guards against.
    defaults = set(REGISTRY.get("google").default_scopes)
    assert "email" not in defaults and "profile" not in defaults
    assert "https://www.googleapis.com/auth/userinfo.email" in defaults
    union = set(_FIXTURE["google"]["connect_union"])
    assert not {"email", "profile"} & union


def test_google_identity_scopes_match_what_the_token_response_echoes():
    # Google echoes the URL spelling in the token response's `scope`, which is
    # what a stored connection records. Requesting that spelling keeps the
    # stored set equal to the request.
    from gaia.connectors.flow import _resolve_granted_scopes

    requested = list(REGISTRY.get("google").default_scopes)
    echoed = {"scope": " ".join(requested)}
    assert _resolve_granted_scopes(echoed, requested) == requested


@pytest.mark.parametrize("connector_id", _CONNECTORS)
def test_every_default_scope_is_grantable(connector_id):
    spec = REGISTRY.get(connector_id)
    assert set(spec.default_scopes) <= set(spec.available_scopes)


def test_legacy_google_aliases_stay_grantable():
    # Connections and grants made before the spellings were unified may carry
    # the short aliases; the catalog must still accept them.
    available = set(REGISTRY.get("google").available_scopes)
    assert {"email", "profile"} <= available
