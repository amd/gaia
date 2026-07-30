# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the Microsoft OAuth provider (#1105, split into two connectors
#2628) — the foundation for the Outlook mailbox (#1275) and Outlook calendar
(#1276) agents.

All network/OAuth is mocked; there are NO live calls. Coverage mirrors the
Google provider tests in ``test_providers.py`` plus the Microsoft-specific
invariants that the later mail/calendar leads depend on:

- One provider CLASS, two connectors: ``microsoft`` (Personal, ``consumers``
  tenant) and ``microsoft_work`` (Work or School, ``organizations`` tenant,
  optionally narrowed by a stored Directory/tenant id override). Tenant is
  spec data (``ConnectorSpec.oauth_tenant``), never an environment variable
  (D6) — ``GAIA_MICROSOFT_TENANT`` only ever REJECTS on conflict (A2/A3),
  covered in ``TestEnvVarConflict`` below.
- Public/native PKCE client: ``token_request_body`` / ``refresh_request_body``
  carry NO ``client_secret`` unless one is explicitly configured (Microsoft
  forbids secrets for public clients — unlike Google, which requires one).
- ``default_scopes`` include ``offline_access`` (so the shared flow obtains a
  refresh token) and ``openid`` (so the shared flow can decode the account
  email from the id_token) — without these the shared ``flow.py`` would raise.
- The catalog declares Mail.Read, Mail.Send, Calendars.ReadWrite so the grant
  ledger accepts those scopes for the future Outlook agents.

Cross-connector isolation (A1's CRITICAL test battery — two connectors must
never share a keyring slot or provider identity) is covered in
``test_providers.py::TestMicrosoftDispatch`` and
``test_oauth_pkce.py``/``test_cli.py``, driven through ``OAuthPkceHandler``
and the CLI rather than ``providers.get()`` alone, per plan amendment A1.
"""

from __future__ import annotations

import importlib
import zlib

import pytest

from gaia.connectors import providers
from gaia.connectors.errors import ConfigurationError, MicrosoftTenantConflictError
from gaia.connectors.providers.base import OAuthProvider

PERSONAL_AUTH_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
PERSONAL_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
WORK_AUTH_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
WORK_TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"

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


@pytest.fixture(autouse=True)
def _reset_env_tenant_deprecation_log():
    """The one-time deprecation-log bookkeeping is process-global; reset it
    between tests so one test's "already logged" state can't hide another
    test's assertion about the first occurrence."""
    from gaia.connectors.providers import microsoft as ms_mod

    ms_mod._env_tenant_deprecation_logged.clear()
    yield
    ms_mod._env_tenant_deprecation_logged.clear()


@pytest.fixture
def _ms_env(monkeypatch):
    monkeypatch.setenv(
        "GAIA_MICROSOFT_CLIENT_ID", "11112222-bbbb-3333-cccc-4444dddd5555"
    )
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    # No test should silently inherit an ambient GAIA_MICROSOFT_TENANT from
    # the developer's shell — it now only ever REJECTS on conflict (A2), so
    # a stray value would turn an unrelated test red.
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

    def test_console_steps_do_not_diverge_from_the_guided_walkthrough(
        self, monkeypatch
    ):
        """#2116 canonical failure: a hand-copied console-steps walkthrough
        drifted from reality and produced a 403 on first use. console_steps
        must be DERIVED from setup_routes.MS_PERSONAL, not a second copy of
        it — assert the exception's steps and the route's steps agree."""
        from gaia.connectors.errors import OAuthClientNotConfiguredError
        from gaia.connectors.setup_routes import MS_PERSONAL, render_console_steps

        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
        with pytest.raises(OAuthClientNotConfiguredError) as exc:
            providers.get("microsoft")

        assert exc.value.console_steps == render_console_steps(MS_PERSONAL)
        # The CLI-facing text is explicitly the LOOPBACK rendering (covers
        # whichever route the user ends up taking) — pin that explicitly, not
        # just the default, so a default-flip can't silently change which
        # steps a CLI user sees.
        assert exc.value.console_steps == render_console_steps(
            MS_PERSONAL, sign_in="loopback"
        )
        # Every LOOPBACK step's instruction text is present verbatim — not
        # just an independently-worded summary that happens to match.
        for step in MS_PERSONAL.steps:
            assert step.instruction in exc.value.console_steps
        # And the device-code rendering must genuinely differ: it drops the
        # loopback-only redirect-URI step device code never uses (#2590) —
        # the console text and the guided walkthrough are two DIFFERENT
        # (but both route-derived) renderings, not one flat unfiltered dump.
        device_rendering = render_console_steps(MS_PERSONAL, sign_in="device_code")
        assert device_rendering != exc.value.console_steps
        redirect_step = next(s for s in MS_PERSONAL.steps if s.loopback_only)
        assert redirect_step.instruction not in device_rendering
        assert redirect_step.instruction in exc.value.console_steps

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


class TestRegistryWorkConnector:
    """microsoft_work mirrors microsoft's registry mechanics but with its
    OWN env var prefix (D9) and NO authored console walkthrough (D10)."""

    def test_lazy_registration_uses_own_provider_id(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-client-id")
        prov = providers.get("microsoft_work")
        assert prov.provider_id == "microsoft_work"
        assert providers.get("microsoft_work") is prov

    def test_missing_creds_names_the_work_specific_env_var(self, monkeypatch):
        # D9: microsoft_work's env fallback is GAIA_MICROSOFT_WORK_CLIENT_ID,
        # NOT the personal connector's GAIA_MICROSOFT_CLIENT_ID.
        monkeypatch.delenv("GAIA_MICROSOFT_WORK_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_MICROSOFT_WORK_CLIENT_SECRET", raising=False)
        with pytest.raises(ConfigurationError) as exc:
            providers.get("microsoft_work")
        msg = str(exc.value)
        assert "GAIA_MICROSOFT_WORK_CLIENT_ID" in msg
        assert "GAIA_MICROSOFT_CLIENT_ID" not in msg.replace(
            "GAIA_MICROSOFT_WORK_CLIENT_ID", ""
        )

    def test_console_steps_fall_back_to_generic_guidance(self, monkeypatch):
        # D10: get_route("microsoft_work") is None (no authored walkthrough
        # yet) — the not-configured error must NOT show microsoft's personal
        # console steps, and must still be positively actionable (mentions
        # Azure and where to find the client id), not a bare "no steps".
        from gaia.connectors.errors import OAuthClientNotConfiguredError
        from gaia.connectors.setup_routes import MS_PERSONAL, render_console_steps

        monkeypatch.delenv("GAIA_MICROSOFT_WORK_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_MICROSOFT_WORK_CLIENT_SECRET", raising=False)
        with pytest.raises(OAuthClientNotConfiguredError) as exc:
            providers.get("microsoft_work")
        steps = exc.value.console_steps
        assert steps != render_console_steps(MS_PERSONAL)
        # Asserted without a URL literal: a `"<url>" in <str>` check trips
        # CodeQL's incomplete-URL-sanitization rule, which is meant for
        # real host checks, not test assertions on generated help text.
        assert "Register an app at" in steps
        assert "Application (client) ID" in steps

    def test_example_block_uses_own_connector_id(self, monkeypatch):
        from gaia.connectors.errors import OAuthClientNotConfiguredError

        monkeypatch.delenv("GAIA_MICROSOFT_WORK_CLIENT_ID", raising=False)
        with pytest.raises(OAuthClientNotConfiguredError) as exc:
            providers.get("microsoft_work")
        msg = str(exc.value)
        assert "gaia connectors configure microsoft_work --client-id" in msg
        assert "gaia connectors connect microsoft_work" in msg
        # Must NOT silently point the user at the personal connector's id.
        assert "configure microsoft --client-id" not in msg


class TestProtocol:
    def test_microsoft_satisfies_oauth_provider_protocol(self, _ms_env):
        prov = providers.get("microsoft")
        # runtime_checkable structural Protocol.
        assert isinstance(prov, OAuthProvider)


class TestEndpointsAndTenant:
    def test_personal_connector_resolves_consumers_tenant(self, _ms_env):
        # D1/D2: "microsoft" always resolves to "consumers" from its OWN
        # spec data — no env var involved.
        prov = providers.get("microsoft")
        assert prov.tenant == "consumers"
        assert prov.auth_url == PERSONAL_AUTH_URL
        assert prov.token_url == PERSONAL_TOKEN_URL

    def test_work_connector_resolves_organizations_tenant(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-id")
        prov = providers.get("microsoft_work")
        assert prov.tenant == "organizations"
        assert prov.auth_url == WORK_AUTH_URL
        assert prov.token_url == WORK_TOKEN_URL

    def test_explicit_tenant_kwarg_wins_over_default(self):
        from gaia.connectors.providers.microsoft import MicrosoftOAuthProvider

        prov = MicrosoftOAuthProvider(
            client_id="x",
            client_secret="",
            tenant="explicit-tenant-value",
            provider_id="microsoft_work",
            default_tenant="organizations",
        )
        assert prov.tenant == "explicit-tenant-value"


class TestStoredTenantOverride:
    """A16: the stored tenant tier (microsoft_work's optional Directory
    (tenant) ID) must actually be reachable — not permanently shadowed by a
    resolved value passed in from providers.get()."""

    def test_stored_tenant_overrides_spec_default(self, monkeypatch):
        from gaia.connectors.store import save_provider_credentials

        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-id")
        save_provider_credentials(
            "microsoft_work",
            client_id="work-id",
            tenant="deadbeef-0000-1111-2222-333344445555",
        )
        prov = providers.get("microsoft_work")
        assert prov.tenant == "deadbeef-0000-1111-2222-333344445555"
        assert "/deadbeef-0000-1111-2222-333344445555/" in prov.auth_url

    def test_no_stored_override_falls_back_to_spec_default(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-id")
        prov = providers.get("microsoft_work")
        assert prov.tenant == "organizations"


class TestEnvVarConflict:
    """A2/A3: GAIA_MICROSOFT_TENANT is validated for CONFLICT only — never
    honoured, never rejected merely for being set."""

    def test_unset_is_a_no_op(self, monkeypatch, _ms_env):
        # _ms_env already deletes it; construction must simply succeed.
        prov = providers.get("microsoft")
        assert prov.tenant == "consumers"

    def test_agreeing_value_is_a_no_op_with_one_time_deprecation_log(
        self, monkeypatch, _ms_env, caplog
    ):
        import logging

        monkeypatch.setenv("GAIA_MICROSOFT_TENANT", "consumers")
        with caplog.at_level(logging.WARNING):
            prov = providers.get("microsoft")
        assert prov.tenant == "consumers"
        deprecation_logs = [
            r for r in caplog.records if "GAIA_MICROSOFT_TENANT" in r.getMessage()
        ]
        assert len(deprecation_logs) == 1

    def test_agreeing_value_logs_only_once_per_provider(
        self, monkeypatch, _ms_env, caplog
    ):
        import logging

        monkeypatch.setenv("GAIA_MICROSOFT_TENANT", "consumers")
        with caplog.at_level(logging.WARNING):
            providers.get("microsoft")
            providers._registry.clear()  # type: ignore[attr-defined]
            providers.get("microsoft")
        deprecation_logs = [
            r for r in caplog.records if "GAIA_MICROSOFT_TENANT" in r.getMessage()
        ]
        assert len(deprecation_logs) == 1

    def test_disagreeing_value_raises_conflict_error(self, monkeypatch, _ms_env):
        monkeypatch.setenv("GAIA_MICROSOFT_TENANT", "organizations")
        with pytest.raises(MicrosoftTenantConflictError) as exc:
            providers.get("microsoft")
        assert isinstance(exc.value, ConfigurationError)
        msg = str(exc.value)
        assert "organizations" in msg
        assert "microsoft_work" in msg
        assert "unset GAIA_MICROSOFT_TENANT" in msg

    def test_disagreeing_value_on_work_connector_names_microsoft(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-id")
        monkeypatch.setenv("GAIA_MICROSOFT_TENANT", "consumers")
        with pytest.raises(MicrosoftTenantConflictError) as exc:
            providers.get("microsoft_work")
        assert "microsoft_work" in str(exc.value)

    def test_bare_guid_always_raises_even_if_it_would_have_matched(self, monkeypatch):
        # A2: a bare tenant GUID is inherently ambiguous between the two
        # connectors — it raises even in the (unlikely) case that it's the
        # SAME GUID a work connector's own tenant_id override would resolve
        # to. Ambiguity, not disagreement, is what triggers this branch.
        from gaia.connectors.store import save_provider_credentials

        guid = "deadbeef-0000-1111-2222-333344445555"
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-id")
        monkeypatch.setenv("GAIA_MICROSOFT_TENANT", guid)
        save_provider_credentials("microsoft_work", client_id="work-id", tenant=guid)
        with pytest.raises(MicrosoftTenantConflictError) as exc:
            providers.get("microsoft_work")
        assert "ambiguous" in str(exc.value).lower()

    def test_conflict_error_is_a_configuration_error_subclass(
        self, monkeypatch, _ms_env
    ):
        # A3: NOT a bare ConnectorsError — api.list_connections and
        # tripwire_check only catch ConfigurationError per-provider.
        from gaia.connectors.errors import ConnectorsError

        monkeypatch.setenv("GAIA_MICROSOFT_TENANT", "organizations")
        with pytest.raises(ConnectorsError) as exc:
            providers.get("microsoft")
        assert isinstance(exc.value, ConfigurationError)


class TestProviderIdInstanceAttribute:
    """D4: provider_id must be a per-INSTANCE attribute, not shared class
    state — two providers constructed in the same process must never
    observe or clobber each other's identity."""

    def test_two_instances_keep_independent_provider_id(self):
        from gaia.connectors.providers.microsoft import MicrosoftOAuthProvider

        personal = MicrosoftOAuthProvider(
            client_id="a",
            client_secret="",
            provider_id="microsoft",
            default_tenant="consumers",
        )
        work = MicrosoftOAuthProvider(
            client_id="b",
            client_secret="",
            provider_id="microsoft_work",
            default_tenant="organizations",
        )
        assert personal.provider_id == "microsoft"
        assert work.provider_id == "microsoft_work"
        # Constructing `work` after `personal` must not have mutated it.
        assert personal.provider_id == "microsoft"
        assert personal.client_id == "a"
        assert work.client_id == "b"


class TestClientIdHash:
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
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
        )

    def test_device_code_url_honors_work_tenant(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-id")
        prov = providers.get("microsoft_work")
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

    def test_catalog_personal_spec_declares_consumers_tenant_and_impl(self):
        # D1/D2: the personal connector always resolves to the "consumers"
        # authority via spec data — no env var involved.
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        assert MICROSOFT_SPEC.oauth_tenant == "consumers"
        assert MICROSOFT_SPEC.oauth_impl == "microsoft"

    def test_catalog_personal_audience_text_points_at_work_connector_only(self):
        # A9: the personal connector's user-facing text must not claim it
        # ALSO covers work/school accounts (that dual-audience claim is
        # exactly the docs-vs-code contradiction A9 flags) — any mention of
        # "work" must be the pointer to the other connector, not a claim
        # that this one handles it.
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        blob = MICROSOFT_SPEC.description + " " + MICROSOFT_SPEC.instructions_md
        assert "personal" in blob.lower()
        assert "Microsoft Work or School" in blob

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
        # A8: was "Microsoft" pre-#2628; now disambiguated from the new
        # microsoft_work connector.
        assert spec.display_name == "Microsoft Personal"


class TestMicrosoftWorkCatalog:
    """microsoft_work — the new Microsoft 365 / Entra ID connector (#2628)."""

    def test_id_and_type(self):
        from gaia.connectors.catalog.microsoft import MICROSOFT_WORK_SPEC

        assert MICROSOFT_WORK_SPEC.id == "microsoft_work"
        assert MICROSOFT_WORK_SPEC.type == "oauth_pkce"
        assert MICROSOFT_WORK_SPEC.display_name == "Microsoft Work or School"

    def test_distinct_storage_key_from_personal_connector(self):
        # A1/A17 (CRITICAL): the two connectors must resolve to DIFFERENT
        # provider ids, or they silently share one keyring slot / token
        # cache entry.
        from gaia.connectors.catalog.microsoft import (
            MICROSOFT_SPEC,
            MICROSOFT_WORK_SPEC,
        )

        assert MICROSOFT_WORK_SPEC.oauth_provider_ref == "microsoft_work"
        assert (
            MICROSOFT_WORK_SPEC.oauth_provider_ref != MICROSOFT_SPEC.oauth_provider_ref
        )

    def test_declares_organizations_tenant_and_shared_impl(self):
        from gaia.connectors.catalog.microsoft import (
            MICROSOFT_SPEC,
            MICROSOFT_WORK_SPEC,
        )

        assert MICROSOFT_WORK_SPEC.oauth_tenant == "organizations"
        # Both specs share the implementation class, dispatched via
        # oauth_impl — NOT inferred from oauth_tenant being non-None (A1).
        assert MICROSOFT_WORK_SPEC.oauth_impl == "microsoft"
        assert MICROSOFT_WORK_SPEC.oauth_impl == MICROSOFT_SPEC.oauth_impl

    def test_declares_optional_tenant_id_setup_field(self):
        # D5/AC4: optional single-tenant override, not required.
        from gaia.connectors.catalog.microsoft import MICROSOFT_WORK_SPEC

        fields = {f.key: f for f in MICROSOFT_WORK_SPEC.oauth_setup_fields}
        assert "tenant_id" in fields
        assert fields["tenant_id"].required is False
        assert "client_id" in fields
        assert fields["client_id"].required is True

    def test_personal_connector_has_no_tenant_id_field(self):
        # The misuse guard (A14) rejects a tenant override on a spec that
        # doesn't declare this field — pin the precondition here.
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        keys = {f.key for f in MICROSOFT_SPEC.oauth_setup_fields}
        assert "tenant_id" not in keys

    def test_supports_device_code(self):
        from gaia.connectors.catalog.microsoft import MICROSOFT_WORK_SPEC

        assert MICROSOFT_WORK_SPEC.supports_device_code is True

    def test_scopes_shared_with_personal_connector(self):
        # The two connectors' Graph scope sets must never drift apart —
        # both specs reference the SAME shared tuples.
        from gaia.connectors.catalog.microsoft import (
            MICROSOFT_SPEC,
            MICROSOFT_WORK_SPEC,
        )

        assert MICROSOFT_WORK_SPEC.default_scopes == MICROSOFT_SPEC.default_scopes
        assert MICROSOFT_WORK_SPEC.available_scopes == MICROSOFT_SPEC.available_scopes

    def test_audience_text_points_at_personal_connector_only(self):
        from gaia.connectors.catalog.microsoft import MICROSOFT_WORK_SPEC

        blob = (
            MICROSOFT_WORK_SPEC.description + " " + MICROSOFT_WORK_SPEC.instructions_md
        )
        assert "work" in blob.lower() or "school" in blob.lower()
        assert "Microsoft Personal" in blob

    def test_registered_in_global_registry(self):
        import gaia.connectors.catalog  # noqa: F401  populate REGISTRY
        from gaia.connectors.registry import REGISTRY

        assert "microsoft_work" in REGISTRY
        assert REGISTRY.get("microsoft_work").display_name == (
            "Microsoft Work or School"
        )

    def test_ids_are_permanent_microsoft_unchanged(self):
        # D1: "microsoft" keeps its pre-#2628 id — renaming would orphan
        # every existing grant/keyring key. This is the negative control.
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        assert MICROSOFT_SPEC.id == "microsoft"


class TestNoImportSideEffects:
    def test_importing_microsoft_module_does_not_register(self, monkeypatch):
        # Mirror A-Crit-3 from the Google work: providers/microsoft.py must
        # have NO side effects on import — registration is lazy via get().
        from gaia.connectors.providers import microsoft as ms_mod

        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        importlib.reload(ms_mod)
        assert "microsoft" not in providers._registry  # type: ignore[attr-defined]
