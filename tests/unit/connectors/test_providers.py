# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia.connectors.providers``.

Coverage:
- ``OAuthProvider`` Protocol structural compatibility — any class implementing
  the documented attribute/method set is accepted.
- ``ConnectorRequirement`` frozen dataclass equality and immutability.
- Registry: ``register/get`` round-trip, unknown provider raises ``KeyError``.
- Lazy registration: ``get("google")`` instantiates ``GoogleOAuthProvider`` on
  first call when the registry is empty for that id.
- ``GoogleOAuthProvider`` reads ``GAIA_GOOGLE_CLIENT_ID`` at instantiation
  (NOT at module import) and surfaces a ``ConfigurationError`` when missing.
- ``authorization_params()`` returns Google-specific extras (``access_type``,
  ``prompt``).
- ``client_id_hash`` is a stable CRC32 fingerprint of the client id.
"""

from __future__ import annotations

import zlib

import pytest

from gaia.connectors import providers
from gaia.connectors.errors import ConfigurationError
from gaia.connectors.providers.base import ConnectorRequirement, OAuthProvider


@pytest.fixture(autouse=True)
def _reset_registry():
    """Clear the providers registry between tests so lazy registration is observable."""
    saved = dict(providers._registry)  # type: ignore[attr-defined]
    providers._registry.clear()  # type: ignore[attr-defined]
    yield
    providers._registry.clear()  # type: ignore[attr-defined]
    providers._registry.update(saved)  # type: ignore[attr-defined]


class TestConnectorRequirement:
    def test_basic_construction(self):
        req = ConnectorRequirement(
            connector_id="google",
            scopes=["gmail.readonly"],
            reason="Needed to read your inbox",
        )
        assert req.connector_id == "google"
        assert req.scopes == ("gmail.readonly",)
        assert req.reason == "Needed to read your inbox"

    def test_is_frozen(self):
        # Frozen dataclasses raise FrozenInstanceError on attribute assignment.
        req = ConnectorRequirement(
            connector_id="google", scopes=["gmail.readonly"], reason="x"
        )
        with pytest.raises(Exception):
            req.connector_id = "microsoft"  # type: ignore[misc]

    def test_equality_and_hashable(self):
        a = ConnectorRequirement(connector_id="google", scopes=["a"], reason="r")
        b = ConnectorRequirement(connector_id="google", scopes=["a"], reason="r")
        assert a == b
        # Hashable so it can live in sets/dict keys.
        assert {a, b} == {a}

    def test_scopes_normalized_to_tuple(self):
        # Lists are mutable; storing as tuple preserves equality across copies.
        req = ConnectorRequirement(connector_id="google", scopes=["a", "b"], reason="r")
        assert isinstance(req.scopes, tuple)


class TestRegistry:
    def test_get_unknown_provider_raises_keyerror(self):
        # "microsoft" is a known lazy-registered provider since #1105; use an
        # id that is genuinely absent from the registry's lazy-init branches.
        with pytest.raises(KeyError):
            providers.get("definitely-not-a-provider")

    def test_register_then_get_round_trip(self):
        class FakeProvider:
            provider_id = "fake"
            auth_url = "https://example/auth"
            token_url = "https://example/token"
            client_id = "fake-id"
            client_id_hash = "abc123"
            default_scopes = ()

            def authorization_url(self, redirect_uri, challenge, state, scopes):
                return "https://example/auth?..."

            def token_request_body(self, code, verifier, redirect_uri):
                return {}

            def refresh_request_body(self, refresh_token):
                return {}

            def authorization_params(self):
                return {}

        prov = FakeProvider()
        providers.register(prov)
        assert providers.get("fake") is prov

    def test_lazy_google_registration(self, monkeypatch):
        # When the registry is empty for "google", get() instantiates and
        # registers GoogleOAuthProvider on demand. This means SDK/CLI/UI
        # consumers do not need explicit setup.
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test-client.apps.example")
        prov = providers.get("google")
        assert prov.provider_id == "google"
        # Second call returns the SAME instance (cached in registry).
        assert providers.get("google") is prov

    def test_lazy_google_missing_creds_raises_configuration_error(self, monkeypatch):
        # No env vars and no keyring entry → a self-documenting error that
        # unblocks a headless user (#2347): the Google Cloud Console steps, the
        # exact `gaia connectors ...` commands, an example grant, AND the UI path.
        from gaia.connectors.errors import OAuthClientNotConfiguredError

        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)
        with pytest.raises(OAuthClientNotConfiguredError) as exc:
            providers.get("google")
        msg = str(exc.value)
        # Subclass of ConfigurationError so CLI (exit 3) / router (503) unchanged.
        assert isinstance(exc.value, ConfigurationError)
        assert "not configured" in msg
        # Console setup steps a headless user must do by hand.
        assert "console.cloud.google.com" in msg
        assert "Desktop app" in msg
        # The exact CLI commands (self-documenting, no UI needed).
        assert "gaia connectors configure google --client-id" in msg
        assert "gaia connectors connect google" in msg
        assert "gaia connectors grants grant google" in msg
        # Concrete example grant for the email agent.
        assert "installed:email" in msg
        assert "gmail.modify" in msg
        # UI path still named for UI users.
        assert "Settings -> Connections -> Google" in msg
        assert "amd-gaia.ai/docs/connectors/google" in msg

    def test_google_loads_from_keyring_without_env(self, monkeypatch):
        # New AgentUI path: user pasted client_id/client_secret into the
        # setup form; the next get_provider() call should pick them up
        # without needing env vars.
        from gaia.connectors.store import save_provider_credentials

        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)
        save_provider_credentials(
            "google",
            client_id="from-keyring.apps.googleusercontent.com",
            client_secret="GOCSPX-from-keyring",
        )
        prov = providers.get("google")
        assert prov.client_id == "from-keyring.apps.googleusercontent.com"
        assert prov.client_secret == "GOCSPX-from-keyring"


class TestOAuthProviderProtocol:
    def test_google_satisfies_protocol(self, monkeypatch):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test-client.apps.example")
        prov = providers.get("google")
        # Structural Protocol — runtime_checkable means isinstance works.
        assert isinstance(prov, OAuthProvider)


class TestGoogleProvider:
    def test_endpoints(self, monkeypatch):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "id.apps.example")
        prov = providers.get("google")
        assert prov.auth_url == "https://accounts.google.com/o/oauth2/v2/auth"
        assert prov.token_url == "https://oauth2.googleapis.com/token"

    def test_client_id_hash_is_stable_crc32(self, monkeypatch):
        client_id = "test.apps.googleusercontent.com"
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", client_id)
        prov = providers.get("google")
        expected = format(zlib.crc32(client_id.encode()), "08x")
        assert prov.client_id_hash == expected

    def test_authorization_params_includes_offline_and_consent(self, monkeypatch):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "id.apps.example")
        prov = providers.get("google")
        params = prov.authorization_params()
        # Per Google docs, refresh-token issuance requires:
        # - access_type=offline (issue refresh token)
        # - prompt=consent     (force re-prompt so refresh token is reissued
        #                       on every authorization)
        assert params.get("access_type") == "offline"
        assert params.get("prompt") == "consent"

    def test_authorization_url_includes_pkce_and_state(self, monkeypatch):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "id.apps.example")
        prov = providers.get("google")
        url = prov.authorization_url(
            redirect_uri="http://127.0.0.1:54321/callback",
            challenge="abcCHAL",
            state="state-nonce",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        assert url.startswith(prov.auth_url)
        assert "code_challenge=abcCHAL" in url
        assert "code_challenge_method=S256" in url
        assert "state=state-nonce" in url
        assert "response_type=code" in url
        assert "client_id=id.apps.example" in url
        # Provider-specific extras come along.
        assert "access_type=offline" in url
        assert "prompt=consent" in url

    def test_token_request_body_includes_pkce_verifier(self, monkeypatch):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "id.apps.example")
        prov = providers.get("google")
        body = prov.token_request_body(
            code="auth-code-x",
            verifier="VERIFIER-VAL",
            redirect_uri="http://127.0.0.1:54321/callback",
        )
        assert body["code"] == "auth-code-x"
        assert body["code_verifier"] == "VERIFIER-VAL"
        assert body["redirect_uri"] == "http://127.0.0.1:54321/callback"
        assert body["grant_type"] == "authorization_code"
        assert body["client_id"] == "id.apps.example"
        # PKCE flow has NO client secret.
        assert "client_secret" not in body

    def test_refresh_request_body_omits_client_secret(self, monkeypatch):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "id.apps.example")
        prov = providers.get("google")
        body = prov.refresh_request_body("refresh-tok")
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "refresh-tok"
        assert body["client_id"] == "id.apps.example"
        assert "client_secret" not in body


class TestNoImportSideEffects:
    def test_importing_google_module_does_not_register(self, monkeypatch):
        # Per A-Crit-3 in Iteration 1: providers/google.py must have NO
        # side effects on import. Reimport the module with the env unset and
        # ensure the registry stays empty.
        import importlib

        from gaia.connectors.providers import google as google_mod

        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        importlib.reload(google_mod)
        assert "google" not in providers._registry  # type: ignore[attr-defined]


class TestGoogleCatalogScopes:
    """
    Per #962: gmail.modify must be in available_scopes so the email triage
    agent's organize/trash/mark-read tools can request it without the
    grant ledger refusing the token (``handler.get_credential`` rejects any
    token request for a scope absent from ``ConnectorSpec.available_scopes``).

    Named explicitly — easy to grep, hard to silently drop in a merge.
    """

    def test_google_catalog_declares_gmail_modify_scope(self):
        from gaia.connectors.catalog.google import GOOGLE_SPEC

        assert (
            "https://www.googleapis.com/auth/gmail.modify"
            in GOOGLE_SPEC.available_scopes
        )

    def test_google_catalog_declares_calendar_events_scope(self):
        # Calendar mutations (create_event, accept/decline invite) need this.
        # Already present pre-#962, but pin it so a future scope-trim doesn't
        # regress the email agent.
        from gaia.connectors.catalog.google import GOOGLE_SPEC

        assert (
            "https://www.googleapis.com/auth/calendar.events"
            in GOOGLE_SPEC.available_scopes
        )
