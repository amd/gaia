# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
#2591 — disconnect must revoke the provider-side OAuth grant, and report
honestly when it can't.

Before this fix, ``OAuthPkceHandler.disconnect`` (the real path behind both
``gaia connectors disconnect`` and the AgentUI's Settings -> Connections ->
Disconnect button) only deleted the local keyring entry — the app's live
Google grant stayed in place, and GAIA reported success as if it had actually
been revoked. Coverage here:

- ``flow.revoke_provider_token`` calls Google's real revoke endpoint with
  the stored refresh token, and reports the outcome structurally rather
  than raising or silently succeeding.
- A provider with no ``revoke_url`` (Microsoft) reports
  ``revoke_supported=False`` rather than implying a revoke happened.
- A failed revoke call is reported as ``revoked_remotely=False`` with the
  error message preserved — never swallowed into a bare success.
- ``OAuthPkceHandler.disconnect`` always clears local state regardless of
  the remote outcome, and returns that outcome to its caller.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from gaia.connectors.flow import revoke_provider_token
from gaia.connectors.oauth_pkce import OAuthPkceHandler
from gaia.connectors.providers import _registry as _provider_registry
from gaia.connectors.spec import ConnectorSpec
from gaia.connectors.store import peek_connection, save_connection


def _make_spec(*, id: str = "google", oauth_provider_ref: str | None = "google"):
    return ConnectorSpec(
        id=id,
        display_name="Google",
        icon="G",
        category="productivity",
        tier=1,
        type="oauth_pkce",
        description="Google connector",
        default_scopes=("openid", "email"),
        oauth_provider_ref=oauth_provider_ref,
    )


@pytest.fixture
def google_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    _provider_registry.clear()
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


@pytest.fixture
def seeded_google(google_provider):
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="seed-rt",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        client_id_hash=google_provider.client_id_hash,
    )
    return google_provider


class TestRevokeProviderToken:
    @pytest.mark.asyncio
    @respx.mock
    async def test_calls_google_revoke_endpoint_with_stored_token(self, seeded_google):
        route = respx.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(200)
        )
        result = await revoke_provider_token("google")
        assert route.called
        sent = route.calls.last.request
        assert b"token=seed-rt" in sent.content
        assert result == {
            "revoke_supported": True,
            "revoked_remotely": True,
            "revoke_error": None,
        }

    @pytest.mark.asyncio
    @respx.mock
    async def test_reports_failure_without_raising(self, seeded_google):
        respx.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(400, text="invalid_token")
        )
        result = await revoke_provider_token("google")
        assert result["revoke_supported"] is True
        assert result["revoked_remotely"] is False
        assert "400" in result["revoke_error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_failure_never_leaks_response_body_or_token(
        self, seeded_google, caplog
    ):
        """A CodeQL-flagged leak path: the failed-revoke request just posted
        the refresh token, and the provider's raw error body used to be
        glued straight into ``revoke_error`` (and a WARNING log line) —
        which #3816 threads out to the CLI and the Agent UI. If a provider
        ever echoes request context into its error body, that would leak
        the token to ``~/.gaia/gaia.log`` and to the user's screen. Neither
        the response body nor the token value may appear anywhere."""
        token_shaped_body = (
            "invalid_token: seed-rt was rejected "
            "(ya29.a0AfH6SMBx-token-shaped-secret-value)"
        )
        respx.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(400, text=token_shaped_body)
        )
        with caplog.at_level("WARNING", logger="gaia.connectors.flow"):
            result = await revoke_provider_token("google")

        assert result["revoked_remotely"] is False
        assert result["revoke_error"] is not None
        assert "seed-rt" not in result["revoke_error"]
        assert token_shaped_body not in result["revoke_error"]
        assert "400" in result["revoke_error"]

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "seed-rt" not in log_text
        assert token_shaped_body not in log_text

    @pytest.mark.asyncio
    async def test_no_stored_token_is_trivially_revoked(self, google_provider):
        # Nothing was ever connected — there is no live grant to leave
        # behind, so this is not a failure.
        result = await revoke_provider_token("google")
        assert result == {
            "revoke_supported": True,
            "revoked_remotely": True,
            "revoke_error": None,
        }

    @pytest.mark.asyncio
    async def test_microsoft_reports_not_supported(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-ms-client")
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        _provider_registry.clear()
        from gaia.connectors.providers import get as get_provider

        ms = get_provider("microsoft")
        save_connection(
            provider="microsoft",
            account_email="bob@example.com",
            refresh_token="ms-rt",
            scopes=["offline_access"],
            client_id_hash=ms.client_id_hash,
        )
        result = await revoke_provider_token("microsoft")
        # Microsoft has no public per-app revoke endpoint — this must be
        # reported as unsupported, never as a silent success.
        assert result == {
            "revoke_supported": False,
            "revoked_remotely": False,
            "revoke_error": None,
        }

    @pytest.mark.asyncio
    async def test_unknown_provider_reports_not_supported(self):
        result = await revoke_provider_token("not-a-real-provider")
        assert result["revoke_supported"] is False
        assert result["revoked_remotely"] is False
        # Distinct from "provider has no revoke endpoint" (Microsoft): this
        # is "we couldn't even determine that" (#2591 review), so the reason
        # must be reported, not collapsed into the same silent False.
        assert result["revoke_error"] is not None
        assert "could not be resolved" in result["revoke_error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_forwarded_connection_is_never_revoked_remotely(
        self, google_provider
    ):
        """#2591 review's critical finding: a connection forwarded by a host
        app shares that app's OAuth grant. Revoking it here would sign the
        host app itself out of the user's account — unrecoverable without
        re-consenting through that other app. This must never happen."""
        save_connection(
            provider="google",
            account_email="alice@example.com",
            refresh_token="host-app-rt",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            client_id_hash=google_provider.client_id_hash,
            forwarded=True,
        )
        # No route registered for the revoke endpoint at all — respx raises
        # if anything tries to call it, proving the network call never fires.
        result = await revoke_provider_token("google")
        assert result["revoke_supported"] is False
        assert result["revoked_remotely"] is False
        assert result["revoke_error"] is not None
        assert "forwarded" in result["revoke_error"]
        # The stored (host app's) refresh token is untouched.
        assert peek_connection("google")["refresh_token"] == "host-app-rt"


class TestOAuthPkceDisconnectRevokes:
    @pytest.mark.asyncio
    @respx.mock
    async def test_disconnect_calls_revoke_and_clears_local_state(self, seeded_google):
        respx.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(200)
        )
        handler = OAuthPkceHandler()
        result = await handler.disconnect(_make_spec())
        assert result["revoked_remotely"] is True
        assert peek_connection("google") is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_disconnect_clears_local_state_even_when_revoke_fails(
        self, seeded_google
    ):
        # #2591's core fix: the user's intent (stop using this connection
        # locally) is still honored on a remote failure — but the caller
        # gets the true outcome back instead of a blanket "disconnected".
        respx.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(500)
        )
        handler = OAuthPkceHandler()
        result = await handler.disconnect(_make_spec())
        assert result["revoked_remotely"] is False
        assert result["revoke_error"]
        assert peek_connection("google") is None
