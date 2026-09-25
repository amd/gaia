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


def _logged_expressions() -> list[str]:
    """Every value expression passed to a ``logger.*`` call inside
    ``revoke_provider_token`` (the %-format template itself excluded)."""
    import ast
    import inspect

    import gaia.connectors.flow as flow_mod

    tree = ast.parse(inspect.getsource(flow_mod))
    func = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
        and n.name == "revoke_provider_token"
    )
    exprs: list[str] = []
    for node in ast.walk(func):
        fn = getattr(node, "func", None)
        if not isinstance(node, ast.Call) or not isinstance(fn, ast.Attribute):
            continue
        if not isinstance(fn.value, ast.Name) or fn.value.id != "logger":
            continue
        assert isinstance(node.args[0], ast.Constant), (
            "log templates must be plain literals with %-args, never f-strings "
            f"(an f-string smuggles values past this guard): {ast.unparse(node.args[0])}"
        )
        exprs.extend(ast.unparse(a) for a in node.args[1:])
    return exprs


class TestRevokeLoggingCarriesNoCredentialDerivedValues:
    """The revoke path's WARNING logs must stay free of anything derived from
    the stored connection or the connector spec.

    ``provider_id`` comes from ``ConnectorSpec.oauth_provider_ref``, and the
    credential-name heuristics in common static analysis (CodeQL's included)
    read any name containing ``oauth`` as secret material — so logging it marks
    the whole revoke path as leaking credentials, drowning out the real leak
    this file exists to prevent. The logs name the revoke endpoint's host
    instead: just as exact about which provider failed, with no such lineage.
    """

    # Everything the two WARNING calls in ``revoke_provider_token`` may log.
    # Anything else — the provider id, the blob, the token, the response body,
    # a stringified exception — is a regression.
    ALLOWED_LOG_ARGS = {
        "revoke_host",
        "type(exc).__name__",
        "response.status_code",
    }

    def test_only_allowlisted_expressions_are_logged(self):
        logged = _logged_expressions()
        assert logged, "expected revoke_provider_token to still log its failures"
        assert set(logged) <= self.ALLOWED_LOG_ARGS, (
            "revoke_provider_token logs an expression outside the allowlist: "
            f"{sorted(set(logged) - self.ALLOWED_LOG_ARGS)}"
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_rejection_log_names_the_endpoint(self, seeded_google, caplog):
        respx.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(400, text="invalid_token")
        )
        with caplog.at_level("WARNING", logger="gaia.connectors.flow"):
            await revoke_provider_token("google")
        # Assert the %-args themselves, not a substring of the rendered line:
        # exact, and it can't pass on an unrelated line that happens to
        # mention the host.
        rejected = [r for r in caplog.records if "revoke rejected" in r.msg]
        assert len(rejected) == 1
        assert rejected[0].args == ("oauth2.googleapis.com", 400)


class TestProviderRevokeUrlContract:
    """``revoke_url`` is part of the ``OAuthProvider`` protocol, not an
    optional extra: ``revoke_provider_token`` reads it directly, so a provider
    that forgets it raises instead of quietly reporting "revoke unsupported"
    for a grant that is still live."""

    def test_declared_on_the_protocol(self):
        from gaia.connectors.providers.base import OAuthProvider

        assert "revoke_url" in OAuthProvider.__annotations__

    def test_every_builtin_provider_declares_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-ms-client")
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        _provider_registry.clear()
        from gaia.connectors.providers import get as get_provider

        for provider_id in ("google", "microsoft"):
            # Attribute access, not getattr-with-default — the exact read
            # revoke_provider_token performs.
            assert hasattr(get_provider(provider_id), "revoke_url")

    @pytest.mark.asyncio
    async def test_provider_without_revoke_url_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)

        class _ForgetfulProvider:
            provider_id = "forgetful"
            client_id_hash = "hash"

        _provider_registry["forgetful"] = _ForgetfulProvider()
        try:
            with pytest.raises(AttributeError):
                await revoke_provider_token("forgetful")
        finally:
            _provider_registry.pop("forgetful", None)


class TestForwardedGuardIsIndependentOfGaiaCredentials:
    """The forwarded check must not depend on GAIA's own OAuth client being
    configured. It runs before provider resolution, so a machine with no
    ``GAIA_GOOGLE_CLIENT_ID`` still reports the forwarded reason rather than
    the unrelated "provider could not be resolved"."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_forwarded_reason_without_gaia_client_id(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        _provider_registry.clear()
        save_connection(
            provider="google",
            account_email="alice@example.com",
            refresh_token="host-app-rt",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            client_id_hash="host-app-client-hash",
            forwarded=True,
        )
        # respx has no route for the revoke endpoint — any outbound call fails
        # the test, proving the host app's grant is never touched.
        result = await revoke_provider_token("google")
        assert result["revoke_supported"] is False
        assert result["revoked_remotely"] is False
        assert "forwarded" in result["revoke_error"]
        assert "could not be resolved" not in result["revoke_error"]
        assert peek_connection("google")["refresh_token"] == "host-app-rt"
