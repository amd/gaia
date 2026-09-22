# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Drift guard for the #2736 scope ceiling: every scope a connector can request
WITHOUT the user naming it must already be inside its ``available_scopes``.

``flow.start_authorization`` / ``start_device_flow`` / ``poll_device_flow``
reject any scope outside the catalog entry. That guard is only safe if GAIA's
own automatic scope sources stay under the ceiling — otherwise the first
connect a new user runs is rejected by GAIA itself, with an error telling them
to file a bug.

Two automatic sources exist and they are NOT the same list:
  * ``ConnectorSpec.default_scopes`` — used by ``oauth_pkce.configure``, the
    bare-``connect`` union, and the TUI/Agent UI connect unions.
  * ``OAuthProvider.default_scopes`` — the first-time-connect fallback in
    ``prior_state.resolve_or_reject_empty_scopes``, and unioned into every
    ``connect --grant-agent`` request (``cli.py``).

They legitimately differ in literal form (``email`` vs
``.../auth/userinfo.email``); the ceiling does not care which is canonical, it
cares that BOTH are covered. Google shipped with only the short spelling
listed, so ``connect google --grant-agent installed:email`` and every
first-time connect raised ``ScopeNotAllowedError``.
"""

from __future__ import annotations

import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.registry import REGISTRY

_OAUTH_SPEC_IDS = sorted(s.id for s in REGISTRY.all() if s.type == "oauth_pkce")


@pytest.fixture(autouse=True)
def _configured_providers(monkeypatch):
    """Give every OAuth connector a client id so ``providers.get`` constructs.
    Home and keyring are already isolated by the autouse fixtures in
    ``tests/unit/connectors/conftest.py``."""
    from gaia.connectors.providers import _registry as provider_registry

    for spec_id in _OAUTH_SPEC_IDS:
        monkeypatch.setenv(f"GAIA_{spec_id.upper()}_CLIENT_ID", f"test-{spec_id}")
    provider_registry.clear()
    yield
    provider_registry.clear()


def test_every_shipped_oauth_connector_is_covered():
    """Guard the guard: a shrunken parametrize list would silently stop
    checking a connector instead of failing."""
    assert _OAUTH_SPEC_IDS == ["google", "microsoft", "microsoft_work"]


@pytest.mark.parametrize("spec_id", _OAUTH_SPEC_IDS)
def test_catalog_default_scopes_are_inside_available_scopes(spec_id):
    spec = REGISTRY.get(spec_id)
    outside = sorted(set(spec.default_scopes) - set(spec.available_scopes))
    assert not outside, (
        f"{spec_id}: ConnectorSpec.default_scopes {outside} are outside "
        "available_scopes — a first connect would be rejected by the #2736 "
        "ceiling. Widen available_scopes; do not loosen the guard."
    )


@pytest.mark.parametrize("spec_id", _OAUTH_SPEC_IDS)
def test_provider_default_scopes_are_inside_available_scopes(spec_id):
    """The list ``resolve_or_reject_empty_scopes`` falls back to, and the one
    ``cli.py`` unions into every ``--grant-agent`` connect."""
    from gaia.connectors.providers import get as get_provider

    spec = REGISTRY.get(spec_id)
    provider = get_provider(spec_id)
    outside = sorted(set(provider.default_scopes) - set(spec.available_scopes))
    assert not outside, (
        f"{spec_id}: OAuthProvider.default_scopes {outside} are outside the "
        "catalog's available_scopes — GAIA's own first-connect fallback would "
        "be rejected by the #2736 ceiling. Widen available_scopes; do not "
        "loosen the guard."
    )


@pytest.mark.parametrize("spec_id", _OAUTH_SPEC_IDS)
def test_automatic_scope_union_survives_the_real_guard(spec_id):
    """Run the union both automatic sources produce through the actual
    rejection function, not a reimplementation of it."""
    from gaia.connectors.flow import _reject_scopes_outside_catalog
    from gaia.connectors.providers import get as get_provider

    spec = REGISTRY.get(spec_id)
    union = sorted(set(spec.default_scopes) | set(get_provider(spec_id).default_scopes))
    _reject_scopes_outside_catalog(spec_id, union)


def test_the_guard_still_rejects_a_scope_outside_the_catalog():
    """The tests above would also pass if the guard had been neutered."""
    from gaia.connectors.errors import ScopeNotAllowedError
    from gaia.connectors.flow import _reject_scopes_outside_catalog

    with pytest.raises(ScopeNotAllowedError):
        _reject_scopes_outside_catalog(
            "google", ["https://www.googleapis.com/auth/drive"]
        )
