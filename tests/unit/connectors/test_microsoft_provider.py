# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the Microsoft OAuth PKCE provider (#1105).

Covers the three patterns the #1105 reviewer flagged as "the three that
matter":
  1. OAuth URL construction (PKCE challenge + S256 + state + offline_access).
  2. Token-exchange body shape (code_verifier present, NO client_secret —
     Microsoft public clients use PKCE without a secret).
  3. Scope-description coverage (every available_scope has a plain-language
     label in SCOPE_DESCRIPTIONS).

Plus: consumers-tenant endpoints, CRC32 client_id_hash, lazy registration,
ConfigurationError when unconfigured, keyring fallback, no import side
effects, registry-driven catalog registration on import.
"""

from __future__ import annotations

import importlib
import zlib

import pytest

from gaia.connectors import providers
from gaia.connectors.errors import ConfigurationError
from gaia.connectors.providers.base import OAuthProvider


@pytest.fixture(autouse=True)
def _reset_registry():
    """Clear the providers registry between tests so lazy registration is observable."""
    saved = dict(providers._registry)  # type: ignore[attr-defined]
    providers._registry.clear()  # type: ignore[attr-defined]
    yield
    providers._registry.clear()  # type: ignore[attr-defined]
    providers._registry.update(saved)  # type: ignore[attr-defined]


class TestRegistry:
    def test_lazy_microsoft_registration(self, monkeypatch):
        # When the registry is empty for "microsoft", get() instantiates and
        # registers MicrosoftOAuthProvider on demand.
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-client-id")
        prov = providers.get("microsoft")
        assert prov.provider_id == "microsoft"
        # Second call returns the SAME instance (cached in registry).
        assert providers.get("microsoft") is prov

    def test_lazy_microsoft_missing_creds_raises_configuration_error(self, monkeypatch):
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        with pytest.raises(ConfigurationError) as exc:
            providers.get("microsoft")
        msg = str(exc.value)
        assert "Settings" in msg
        assert "Connections" in msg
        assert "docs/runbooks/microsoft-oauth-client.md" in msg

    def test_microsoft_loads_from_keyring_without_env(self, monkeypatch):
        # AgentUI path: user pasted the client_id into the setup form; the
        # next get() should pick it up without env vars.
        from gaia.connectors.store import save_provider_credentials

        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        save_provider_credentials("microsoft", client_id="from-keyring-id")
        prov = providers.get("microsoft")
        assert prov.client_id == "from-keyring-id"


class TestOAuthProviderProtocol:
    def test_microsoft_satisfies_protocol(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-client-id")
        prov = providers.get("microsoft")
        assert isinstance(prov, OAuthProvider)


class TestMicrosoftProvider:
    def test_endpoints_use_consumers_tenant(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "id")
        prov = providers.get("microsoft")
        assert (
            prov.auth_url
            == "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
        )
        assert (
            prov.token_url
            == "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
        )

    def test_client_id_hash_is_stable_crc32(self, monkeypatch):
        client_id = "00000000-1111-2222-3333-444444444444"
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", client_id)
        prov = providers.get("microsoft")
        expected = format(zlib.crc32(client_id.encode()), "08x")
        assert prov.client_id_hash == expected

    def test_authorization_params_is_empty(self, monkeypatch):
        # Refresh-token issuance is driven by the offline_access scope, not
        # by an authorization-URL param (unlike Google's access_type=offline).
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "id")
        prov = providers.get("microsoft")
        assert prov.authorization_params() == {}

    def test_default_scopes_include_offline_access(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "id")
        prov = providers.get("microsoft")
        assert "offline_access" in prov.default_scopes

    # ── Pattern 1: OAuth URL construction ────────────────────────────────
    def test_authorization_url_includes_pkce_and_state(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "client-abc")
        prov = providers.get("microsoft")
        url = prov.authorization_url(
            redirect_uri="http://127.0.0.1:54321/callback",
            challenge="abcCHAL",
            state="state-nonce",
            scopes=[
                "offline_access",
                "https://graph.microsoft.com/Mail.Read",
            ],
        )
        assert url.startswith(prov.auth_url)
        assert "code_challenge=abcCHAL" in url
        assert "code_challenge_method=S256" in url
        assert "state=state-nonce" in url
        assert "response_type=code" in url
        assert "client_id=client-abc" in url
        assert "offline_access" in url

    # ── Pattern 2: token-exchange body shape ─────────────────────────────
    def test_token_request_body_includes_verifier_and_omits_secret(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "client-abc")
        prov = providers.get("microsoft")
        body = prov.token_request_body(
            code="auth-code-x",
            verifier="VERIFIER-VAL",
            redirect_uri="http://127.0.0.1:54321/callback",
        )
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "auth-code-x"
        assert body["code_verifier"] == "VERIFIER-VAL"
        assert body["redirect_uri"] == "http://127.0.0.1:54321/callback"
        assert body["client_id"] == "client-abc"
        # Public client — PKCE replaces the secret.
        assert "client_secret" not in body

    def test_refresh_request_body_omits_client_secret(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "client-abc")
        prov = providers.get("microsoft")
        body = prov.refresh_request_body("refresh-tok")
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "refresh-tok"
        assert body["client_id"] == "client-abc"
        assert "client_secret" not in body


# ── Pattern 3: scope-description coverage (AC23) ─────────────────────────
class TestMicrosoftScopeDescriptions:
    def test_every_available_scope_has_a_plain_language_label(self):
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC
        from gaia.connectors.providers.microsoft import SCOPE_DESCRIPTIONS

        missing = [
            scope
            for scope in MICROSOFT_SPEC.available_scopes
            if not SCOPE_DESCRIPTIONS.get(scope, "").strip()
        ]
        assert not missing, (
            "Every scope in MICROSOFT_SPEC.available_scopes must have a "
            "non-empty SCOPE_DESCRIPTIONS entry (AC23 consent-dialog "
            f"coverage). Missing: {missing}"
        )


class TestMicrosoftCatalogScopes:
    """Named-scope guard (#1105): the issue's scopes must stay in
    available_scopes so the grant ledger accepts token requests for them."""

    def test_catalog_declares_issue_named_scopes(self):
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        for scope in (
            "offline_access",
            "https://graph.microsoft.com/Mail.Read",
            "https://graph.microsoft.com/Mail.Send",
            "https://graph.microsoft.com/Calendars.ReadWrite",
        ):
            assert scope in MICROSOFT_SPEC.available_scopes

    def test_offline_access_in_default_scopes(self):
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        assert "offline_access" in MICROSOFT_SPEC.default_scopes

    def test_setup_form_has_no_client_secret_field(self):
        # Public client: only the client_id is collected, never a secret.
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        keys = {f.key for f in MICROSOFT_SPEC.oauth_setup_fields}
        assert keys == {"client_id"}


class TestCatalogRegistration:
    def test_microsoft_spec_registered_on_catalog_import(self):
        # The catalog/__init__ import wires the tile at app boot — importing
        # the package must register the spec into the global REGISTRY.
        import gaia.connectors.catalog  # noqa: F401
        from gaia.connectors.registry import REGISTRY

        spec = REGISTRY.get("microsoft")
        assert spec.id == "microsoft"
        assert spec.type == "oauth_pkce"
        assert spec.oauth_provider_ref == "microsoft"


class TestNoImportSideEffects:
    def test_importing_microsoft_module_does_not_register(self, monkeypatch):
        # providers/microsoft.py must have NO side effects on import.
        from gaia.connectors.providers import microsoft as microsoft_mod

        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        importlib.reload(microsoft_mod)
        assert "microsoft" not in providers._registry  # type: ignore[attr-defined]
