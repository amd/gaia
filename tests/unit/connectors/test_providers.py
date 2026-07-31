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

    def test_required_scopes_defaults_to_scopes(self):
        """#2730 D5 mitigation: every existing construction site (~14 of
        them) builds a ConnectorRequirement without ``required_scopes`` —
        this proves they keep today's all-required semantics unchanged
        rather than assuming it."""
        req = ConnectorRequirement(connector_id="google", scopes=["a", "b"])
        assert req.required_scopes == ("a", "b")

    def test_required_scopes_can_narrow_the_scopes(self):
        req = ConnectorRequirement(
            connector_id="google", scopes=["a", "b"], required_scopes=["a"]
        )
        assert req.scopes == ("a", "b")
        assert req.required_scopes == ("a",)
        assert isinstance(req.required_scopes, tuple)


class TestRegistry:
    def test_get_unknown_provider_raises_keyerror(self):
        # "microsoft" is a known lazy-registered provider since #1105; use an
        # id that is genuinely absent from the registry's lazy-init branches.
        with pytest.raises(KeyError):
            providers.get("definitely-not-a-provider")

    def test_unknown_provider_message_lists_microsoft_work_too(self):
        # The known-ids list must be DERIVED from the catalog (every
        # oauth_pkce spec id), not a hand-maintained set that drifts the
        # moment a third Microsoft-audience connector lands — that would
        # silently undercut the "no providers/__init__.py edit" property
        # A1's dispatch mechanism is supposed to guarantee.
        with pytest.raises(KeyError) as exc:
            providers.get("definitely-not-a-provider")
        msg = str(exc.value)
        assert "google" in msg
        assert "microsoft" in msg
        assert "microsoft_work" in msg

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
        # connect authorizes scopes and grants the agent in one flow (#2347).
        assert "gaia connectors connect google --scopes" in msg
        assert "--grant-agent installed:email" in msg
        # Concrete, copy-paste example with the email agent's FULL scope set.
        assert "gmail.modify" in msg
        assert "gmail.send" in msg
        assert "calendar.events" in msg
        assert "calendar.readonly" in msg
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


class TestMicrosoftDispatch:
    """A1 (CRITICAL): providers.get() dispatches on ConnectorSpec.oauth_impl,
    never on the connector id — and the two Microsoft connectors, despite
    sharing an implementation CLASS, must NEVER share stored state. This is
    the test battery A1 requires: driven through OAuthPkceHandler (the real
    production caller), not providers.get() alone, because a test that only
    calls providers.get() never touches oauth_provider_ref and would pass
    regardless of whether that field is set correctly.
    """

    def test_a_throwaway_third_spec_with_oauth_impl_microsoft_dispatches(
        self, monkeypatch
    ):
        # AC3 structurally: a fourth Microsoft-audience connector needs no
        # providers/__init__.py edit — only a catalog entry with the same
        # oauth_impl.
        import gaia.connectors.catalog  # noqa: F401
        from gaia.connectors.registry import REGISTRY
        from gaia.connectors.spec import ConnectorSpec

        throwaway = ConnectorSpec(
            id="microsoft_throwaway_test",
            display_name="Microsoft Throwaway (test only)",
            icon="",
            category="productivity",
            tier=9,
            type="oauth_pkce",
            description="test-only throwaway spec",
            oauth_provider_ref="microsoft_throwaway_test",
            oauth_tenant="organizations",
            oauth_impl="microsoft",
        )
        try:
            REGISTRY.register(throwaway)
        except RuntimeError:
            pytest.skip("registry frozen in this process; covered elsewhere")
        try:
            monkeypatch.setenv(
                "GAIA_MICROSOFT_THROWAWAY_TEST_CLIENT_ID", "throwaway-id"
            )
            prov = providers.get("microsoft_throwaway_test")
            from gaia.connectors.providers.microsoft import MicrosoftOAuthProvider

            assert isinstance(prov, MicrosoftOAuthProvider)
            assert prov.provider_id == "microsoft_throwaway_test"
        finally:
            REGISTRY._specs.pop("microsoft_throwaway_test", None)  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_configure_stores_distinct_client_ids_per_connector(
        self, monkeypatch
    ):
        # The A1-required battery, verbatim shape: configure both Microsoft
        # connectors through OAuthPkceHandler (the real dispatcher every
        # production caller goes through) and assert each provider's
        # RESOLVED identity ("microsoft" vs "microsoft_work") sees only its
        # own client id — never the other's.
        import gaia.connectors.catalog  # noqa: F401
        from gaia.connectors.oauth_pkce import OAuthPkceHandler
        from gaia.connectors.registry import REGISTRY
        from gaia.connectors.store import peek_provider_credentials

        handler = OAuthPkceHandler()
        microsoft_spec = REGISTRY.get("microsoft")
        work_spec = REGISTRY.get("microsoft_work")

        await handler.configure(microsoft_spec, {"client_id": "A", "save_only": True})
        await handler.configure(work_spec, {"client_id": "B", "save_only": True})

        assert peek_provider_credentials("microsoft")["client_id"] == "A"
        assert peek_provider_credentials("microsoft_work")["client_id"] == "B"

    @pytest.mark.asyncio
    async def test_get_credential_disconnect_test_hit_distinct_store_keys(
        self, monkeypatch
    ):
        # get_credential / disconnect / test for microsoft_work must hit the
        # store with "microsoft_work", never "microsoft".
        import gaia.connectors.catalog  # noqa: F401
        from gaia.connectors.oauth_pkce import OAuthPkceHandler
        from gaia.connectors.registry import REGISTRY

        handler = OAuthPkceHandler()
        work_spec = REGISTRY.get("microsoft_work")

        calls: list[str] = []

        async def _fake_get_or_refresh(provider_id, *, account_email=None):
            calls.append(provider_id)
            return "tok"

        monkeypatch.setattr(
            "gaia.connectors.oauth_pkce.get_or_refresh", _fake_get_or_refresh
        )
        await handler.get_credential(work_spec)
        await handler.test(work_spec)
        assert calls == ["microsoft_work", "microsoft_work"]

        deleted: list[tuple] = []
        monkeypatch.setattr(
            "gaia.connectors.oauth_pkce.delete_connection",
            lambda provider_id, **kw: deleted.append((provider_id, kw)),
        )
        monkeypatch.setattr(
            "gaia.connectors.grants.revoke_all_grants_for", lambda cid: None
        )
        monkeypatch.setattr(
            "gaia.connectors.activations.revoke_all_activations_for", lambda cid: None
        )
        await handler.disconnect(work_spec)
        assert deleted[0][0] == "microsoft_work"

    def test_resolved_provider_ids_are_never_equal(self):
        from gaia.connectors.catalog.microsoft import (
            MICROSOFT_SPEC,
            MICROSOFT_WORK_SPEC,
        )

        personal_id = MICROSOFT_SPEC.oauth_provider_ref or MICROSOFT_SPEC.id
        work_id = MICROSOFT_WORK_SPEC.oauth_provider_ref or MICROSOFT_WORK_SPEC.id
        assert personal_id != work_id

    def test_registry_ends_with_two_distinct_provider_entries(self, monkeypatch):
        # A register() bug could clobber one entry while still producing a
        # plausible client_id_hash — assert both survive as DISTINCT objects
        # with distinct identities, not just "something is registered".
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "personal-id")
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-id")
        personal = providers.get("microsoft")
        work = providers.get("microsoft_work")
        assert personal is not work
        assert personal.provider_id != work.provider_id
        assert personal.client_id != work.client_id
        assert {"microsoft", "microsoft_work"}.issubset(
            set(providers.list_provider_ids())
        )


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
