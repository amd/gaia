# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the Microsoft OAuth provider (#1105) — the foundation for the
Outlook mailbox (#1275) and Outlook calendar (#1276) agents.

All network/OAuth is mocked; there are NO live calls. Coverage mirrors the
Google provider tests in ``test_providers.py`` plus the Microsoft-specific
invariants that the later mail/calendar leads depend on:

- Tenant defaults to ``common`` (personal Outlook.com/Hotmail/Live AND
  work/school Entra ID) — in BOTH the authorize and token endpoint URLs;
  overridable via ``GAIA_MICROSOFT_TENANT``.
- Public/native PKCE client: ``token_request_body`` / ``refresh_request_body``
  carry NO ``client_secret`` unless one is explicitly configured (Microsoft
  forbids secrets for public clients — unlike Google, which requires one).
- ``default_scopes`` include ``offline_access`` (so the shared flow obtains a
  refresh token) and ``openid`` (so the shared flow can decode the account
  email from the id_token) — without these the shared ``flow.py`` would raise.
- The catalog declares Mail.Read, Mail.Send, Calendars.ReadWrite so the grant
  ledger accepts those scopes for the future Outlook agents.
"""

from __future__ import annotations

import importlib
import zlib

import pytest

from gaia.connectors import providers
from gaia.connectors.errors import ConfigurationError
from gaia.connectors.providers.base import OAuthProvider

COMMON_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
COMMON_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

MAIL_READ = "https://graph.microsoft.com/Mail.Read"
MAIL_SEND = "https://graph.microsoft.com/Mail.Send"
CALENDARS_RW = "https://graph.microsoft.com/Calendars.ReadWrite"


@pytest.fixture(autouse=True)
def _reset_registry():
    """Clear the providers registry between tests so lazy registration is observable."""
    saved = dict(providers._registry)  # type: ignore[attr-defined]
    providers._registry.clear()  # type: ignore[attr-defined]
    yield
    providers._registry.clear()  # type: ignore[attr-defined]
    providers._registry.update(saved)  # type: ignore[attr-defined]


@pytest.fixture
def _ms_env(monkeypatch):
    monkeypatch.setenv(
        "GAIA_MICROSOFT_CLIENT_ID", "11112222-bbbb-3333-cccc-4444dddd5555"
    )
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    # Default tenant must resolve to ``common`` unless a test opts into an
    # override — clear any ambient value from the developer's shell.
    monkeypatch.delenv("GAIA_MICROSOFT_TENANT", raising=False)
    return "11112222-bbbb-3333-cccc-4444dddd5555"


class TestRegistry:
    def test_lazy_microsoft_registration(self, _ms_env):
        # When the registry is empty for "microsoft", get() instantiates and
        # registers MicrosoftOAuthProvider on demand — SDK/CLI/UI consumers do
        # not need explicit setup, exactly as for Google.
        prov = providers.get("microsoft")
        assert prov.provider_id == "microsoft"
        # Second call returns the SAME cached instance.
        assert providers.get("microsoft") is prov

    def test_unknown_provider_message_lists_microsoft(self, monkeypatch):
        # After registering microsoft, the "unknown provider" message should
        # include it so the error is actionable.
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "id")
        providers.get("microsoft")
        with pytest.raises(KeyError) as exc:
            providers.get("definitely-not-a-provider")
        assert "microsoft" in str(exc.value)

    def test_lazy_missing_creds_raises_configuration_error(self, monkeypatch):
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
        with pytest.raises(ConfigurationError) as exc:
            providers.get("microsoft")
        msg = str(exc.value)
        # Actionable error names the env vars and points at the setup form.
        assert "GAIA_MICROSOFT_CLIENT_ID" in msg
        assert "Settings" in msg
        assert "Connections" in msg

    def test_microsoft_loads_from_keyring_without_env(self, monkeypatch):
        from gaia.connectors.store import save_provider_credentials

        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
        save_provider_credentials(
            "microsoft",
            client_id="from-keyring-client-id",
            client_secret="",
        )
        prov = providers.get("microsoft")
        assert prov.client_id == "from-keyring-client-id"


class TestProtocol:
    def test_microsoft_satisfies_oauth_provider_protocol(self, _ms_env):
        prov = providers.get("microsoft")
        # runtime_checkable structural Protocol.
        assert isinstance(prov, OAuthProvider)


class TestEndpointsAndTenant:
    def test_endpoints_default_to_common_tenant(self, _ms_env):
        prov = providers.get("microsoft")
        assert prov.auth_url == COMMON_AUTH_URL
        assert prov.token_url == COMMON_TOKEN_URL
        # ``common`` accepts personal AND work/school accounts; it must be in
        # both URLs so neither account type is rejected before token exchange.
        assert "/common/" in prov.auth_url
        assert "/common/" in prov.token_url

    def test_tenant_override_from_env(self, monkeypatch, _ms_env):
        # An org can pin a single Entra tenant id (or organizations/consumers)
        # via GAIA_MICROSOFT_TENANT; both endpoint URLs must reflect it.
        monkeypatch.setenv("GAIA_MICROSOFT_TENANT", "organizations")
        providers._registry.clear()  # type: ignore[attr-defined]
        prov = providers.get("microsoft")
        assert prov.tenant == "organizations"
        assert "/organizations/" in prov.auth_url
        assert "/organizations/" in prov.token_url

    def test_tenant_override_accepts_bare_guid(self, monkeypatch, _ms_env):
        monkeypatch.setenv(
            "GAIA_MICROSOFT_TENANT", "aaaabbbb-cccc-dddd-eeee-ffff00001111"
        )
        providers._registry.clear()  # type: ignore[attr-defined]
        prov = providers.get("microsoft")
        assert "/aaaabbbb-cccc-dddd-eeee-ffff00001111/" in prov.token_url

    def test_client_id_hash_is_stable_crc32(self, monkeypatch):
        client_id = "11112222-bbbb-3333-cccc-4444dddd5555"
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", client_id)
        prov = providers.get("microsoft")
        expected = format(zlib.crc32(client_id.encode()), "08x")
        assert prov.client_id_hash == expected


class TestDefaultScopes:
    def test_default_scopes_include_offline_access_and_openid(self, _ms_env):
        # offline_access => refresh_token is returned by the token endpoint;
        # openid => id_token is returned so the shared flow can decode the
        # account email. The shared flow.py REQUIRES a refresh_token, so a
        # Microsoft connect without offline_access would raise — pin it here.
        prov = providers.get("microsoft")
        assert "offline_access" in prov.default_scopes
        assert "openid" in prov.default_scopes


class TestAuthorizationUrl:
    def test_authorization_url_has_pkce_state_and_query_response_mode(self, _ms_env):
        prov = providers.get("microsoft")
        url = prov.authorization_url(
            redirect_uri="http://127.0.0.1:54321/callback",
            challenge="abcCHAL",
            state="state-nonce",
            scopes=[MAIL_READ, "offline_access", "openid"],
        )
        assert url.startswith(prov.auth_url)
        assert "code_challenge=abcCHAL" in url
        assert "code_challenge_method=S256" in url
        assert "state=state-nonce" in url
        assert "response_type=code" in url
        assert "client_id=11112222-bbbb-3333-cccc-4444dddd5555" in url
        # Loopback /callback handler reads ?code=... from the query string;
        # MS defaults to fragment in some hybrid cases, so pin query mode.
        assert "response_mode=query" in url

    def test_authorization_url_space_delimits_scopes(self, _ms_env):
        from urllib.parse import parse_qs, urlparse

        prov = providers.get("microsoft")
        url = prov.authorization_url(
            redirect_uri="http://127.0.0.1:1/callback",
            challenge="c",
            state="s",
            scopes=[MAIL_READ, MAIL_SEND, "offline_access"],
        )
        scope_value = parse_qs(urlparse(url).query)["scope"][0]
        # Scopes are space-separated per the MS v2.0 spec.
        assert scope_value == f"{MAIL_READ} {MAIL_SEND} offline_access"


class TestTokenRequestBody:
    def test_public_client_token_body_has_no_client_secret(self, _ms_env):
        # Microsoft forbids client_secret for public/native PKCE clients
        # (unlike Google, which requires it). With no secret configured the
        # body must omit it entirely.
        prov = providers.get("microsoft")
        body = prov.token_request_body(
            code="auth-code-x",
            verifier="VERIFIER-VAL",
            redirect_uri="http://127.0.0.1:54321/callback",
        )
        assert body["code"] == "auth-code-x"
        assert body["code_verifier"] == "VERIFIER-VAL"
        assert body["redirect_uri"] == "http://127.0.0.1:54321/callback"
        assert body["grant_type"] == "authorization_code"
        assert body["client_id"] == "11112222-bbbb-3333-cccc-4444dddd5555"
        assert "client_secret" not in body

    def test_refresh_body_has_no_client_secret(self, _ms_env):
        prov = providers.get("microsoft")
        body = prov.refresh_request_body("refresh-tok")
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "refresh-tok"
        assert body["client_id"] == "11112222-bbbb-3333-cccc-4444dddd5555"
        assert "client_secret" not in body

    def test_confidential_client_includes_secret_when_configured(self, monkeypatch):
        # Edge case: a confidential web-app registration where the operator
        # set GAIA_MICROSOFT_CLIENT_SECRET. Then the secret IS sent. This is
        # opt-in, never the default public-client posture.
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "conf-client")
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_SECRET", "super-secret")
        prov = providers.get("microsoft")
        token_body = prov.token_request_body(
            code="c", verifier="v", redirect_uri="http://127.0.0.1:1/callback"
        )
        refresh_body = prov.refresh_request_body("r")
        assert token_body["client_secret"] == "super-secret"
        assert refresh_body["client_secret"] == "super-secret"


class TestAuthorizationParams:
    def test_authorization_params_pins_query_response_mode(self, _ms_env):
        prov = providers.get("microsoft")
        params = prov.authorization_params()
        assert params.get("response_mode") == "query"


class TestDeviceCodeFlow:
    def test_device_code_url_uses_resolved_tenant(self, _ms_env):
        prov = providers.get("microsoft")
        assert prov.device_code_url == (
            "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"
        )

    def test_device_code_url_honors_tenant_override(self, monkeypatch, _ms_env):
        monkeypatch.setenv("GAIA_MICROSOFT_TENANT", "organizations")
        providers._registry.clear()  # type: ignore[attr-defined]
        prov = providers.get("microsoft")
        assert "/organizations/" in prov.device_code_url

    def test_device_code_request_body_carries_client_id_and_scopes(self, _ms_env):
        prov = providers.get("microsoft")
        body = prov.device_code_request_body([MAIL_READ, "offline_access"])
        assert body["client_id"] == "11112222-bbbb-3333-cccc-4444dddd5555"
        # Space-delimited scope string per the MS v2.0 spec.
        assert body["scope"] == f"{MAIL_READ} offline_access"

    def test_device_token_body_public_client_has_no_secret(self, _ms_env):
        prov = providers.get("microsoft")
        body = prov.device_token_request_body("DEV-CODE-123")
        assert body["grant_type"] == ("urn:ietf:params:oauth:grant-type:device_code")
        assert body["device_code"] == "DEV-CODE-123"
        assert body["client_id"] == "11112222-bbbb-3333-cccc-4444dddd5555"
        assert "client_secret" not in body

    def test_device_token_body_confidential_includes_secret(self, monkeypatch):
        monkeypatch.delenv("GAIA_MICROSOFT_TENANT", raising=False)
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "conf-client")
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_SECRET", "super-secret")
        prov = providers.get("microsoft")
        body = prov.device_token_request_body("DEV")
        assert body["client_secret"] == "super-secret"


class TestUserinfoFallback:
    def test_userinfo_url_targets_graph_me(self, _ms_env):
        prov = providers.get("microsoft")
        assert prov.userinfo_url.startswith("https://graph.microsoft.com/v1.0/me")

    def test_parse_account_email_prefers_mail(self, _ms_env):
        prov = providers.get("microsoft")
        assert (
            prov.parse_account_email(
                {"mail": "a@example.com", "userPrincipalName": "b@example.com"}
            )
            == "a@example.com"
        )

    def test_parse_account_email_falls_back_to_upn(self, _ms_env):
        prov = providers.get("microsoft")
        assert (
            prov.parse_account_email(
                {"mail": None, "userPrincipalName": "b@example.com"}
            )
            == "b@example.com"
        )

    def test_parse_account_email_none_when_absent(self, _ms_env):
        prov = providers.get("microsoft")
        assert prov.parse_account_email({}) is None


class TestCatalog:
    def test_catalog_declares_required_graph_scopes(self):
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        for scope in (MAIL_READ, MAIL_SEND, CALENDARS_RW):
            assert scope in MICROSOFT_SPEC.available_scopes, scope

    def test_catalog_default_scopes_enable_refresh_and_account(self):
        # The shared flow requires a refresh_token and decodes the account
        # email from the id_token; both depend on these two scopes being in
        # the default set used by a first connect.
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        assert "offline_access" in MICROSOFT_SPEC.default_scopes
        assert "openid" in MICROSOFT_SPEC.default_scopes

    def test_catalog_is_oauth_pkce_pointing_at_microsoft_provider(self):
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        assert MICROSOFT_SPEC.id == "microsoft"
        assert MICROSOFT_SPEC.type == "oauth_pkce"
        assert MICROSOFT_SPEC.oauth_provider_ref == "microsoft"

    def test_catalog_setup_form_requires_client_id_only(self):
        # Public PKCE client: the user pastes only a Client ID. A client
        # secret must NOT be a required setup field (MS forbids secrets for
        # public clients). Any secret field, if present, must be optional.
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        fields = {f.key: f for f in MICROSOFT_SPEC.oauth_setup_fields}
        assert "client_id" in fields
        assert fields["client_id"].required is True
        if "client_secret" in fields:
            assert fields["client_secret"].required is False

    def test_catalog_registered_in_global_registry(self):
        import gaia.connectors.catalog  # noqa: F401  populate REGISTRY
        from gaia.connectors.registry import REGISTRY

        assert "microsoft" in REGISTRY
        spec = REGISTRY.get("microsoft")
        assert spec.display_name == "Microsoft"


class TestNoImportSideEffects:
    def test_importing_microsoft_module_does_not_register(self, monkeypatch):
        # Mirror A-Crit-3 from the Google work: providers/microsoft.py must
        # have NO side effects on import — registration is lazy via get().
        from gaia.connectors.providers import microsoft as ms_mod

        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        importlib.reload(ms_mod)
        assert "microsoft" not in providers._registry  # type: ignore[attr-defined]
