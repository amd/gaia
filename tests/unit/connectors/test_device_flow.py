# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the device-code flow (#1275) — the zero-Azure-app-registration
sign-in used for Microsoft work/school + personal accounts.

All HTTP is mocked; there are NO live calls. Coverage:

- ``start_device_flow`` returns the user code / verification URI and raises
  loudly on a non-200 devicecode response or a provider with no device support.
- ``poll_device_flow`` honors ``authorization_pending`` / ``slow_down``, then
  persists the connection on success (via the real in-memory keyring) and
  commits per-agent grants atomically.
- The unhappy paths (declined, expired, missing refresh_token) each raise a
  typed error rather than returning a partial / empty result.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from gaia.connectors import flow as flow_mod
from gaia.connectors.errors import (
    ConnectorsError,
    ConsentDeniedError,
    FlowTimeoutError,
    GrantAfterConnectError,
)

pytestmark = pytest.mark.allow_network

MAIL_READ = "https://graph.microsoft.com/Mail.Read"


@pytest.fixture(autouse=True)
def _ms_env(monkeypatch):
    monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    # Reset the provider registry so each test builds a fresh provider.
    from gaia.connectors import providers

    providers._registry.clear()  # type: ignore[attr-defined]
    yield
    providers._registry.clear()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _instant_sleep(monkeypatch):
    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(flow_mod.asyncio, "sleep", _no_wait)


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeAsyncClient:
    """Pops a queued response per ``post`` call; ``get`` pops from a separate
    queue (used for the userinfo /me fallback). Records the requests made."""

    _queue: list = []
    _get_queue: list = []
    calls: list = []
    get_calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None):
        _FakeAsyncClient.calls.append((url, data))
        return _FakeAsyncClient._queue.pop(0)

    async def get(self, url, headers=None):
        _FakeAsyncClient.get_calls.append((url, headers))
        return _FakeAsyncClient._get_queue.pop(0)


def _install_responses(monkeypatch, responses, get_responses=None):
    _FakeAsyncClient._queue = list(responses)
    _FakeAsyncClient._get_queue = list(get_responses or [])
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.get_calls = []
    monkeypatch.setattr(flow_mod.httpx, "AsyncClient", _FakeAsyncClient)


def _id_token(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.sig"


class TestStartDeviceFlow:
    def test_returns_user_code_and_uri(self, monkeypatch):
        _install_responses(
            monkeypatch,
            [
                _FakeResp(
                    200,
                    {
                        "device_code": "DEV",
                        "user_code": "ABCD-EFGH",
                        "verification_uri": "https://microsoft.com/devicelogin",
                        "expires_in": 900,
                        "interval": 5,
                        "message": "Go to ... and enter ABCD-EFGH",
                    },
                )
            ],
        )
        info = asyncio.run(flow_mod.start_device_flow("microsoft", [MAIL_READ]))
        assert info["user_code"] == "ABCD-EFGH"
        assert info["device_code"] == "DEV"
        assert info["verification_uri"].endswith("devicelogin")
        # Hit the tenant-scoped devicecode endpoint — "microsoft" resolves
        # to "consumers" from its own spec data (D1/#2628), not "common".
        assert _FakeAsyncClient.calls[0][0].endswith(
            "/consumers/oauth2/v2.0/devicecode"
        )

    def test_non_200_raises(self, monkeypatch):
        _install_responses(
            monkeypatch, [_FakeResp(400, {"error": "invalid_client"}, "bad")]
        )
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(flow_mod.start_device_flow("microsoft", [MAIL_READ]))
        assert "Device-code request" in str(exc.value)

    def test_non_200_remedy_names_the_connector_specific_client_id_var(
        self, monkeypatch
    ):
        # The generic (non-AADSTS9002346) failure branch must name the
        # CONNECTOR-SPECIFIC client-id var (D9), not always the personal one.
        _install_responses(
            monkeypatch, [_FakeResp(400, {"error": "invalid_client"}, "bad")]
        )
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(flow_mod.start_device_flow("microsoft", [MAIL_READ]))
        msg = str(exc.value)
        assert "GAIA_MICROSOFT_CLIENT_ID" in msg

    def test_non_200_remedy_for_work_connector_names_its_own_env_var(self, monkeypatch):
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-client-id")
        _install_responses(
            monkeypatch, [_FakeResp(400, {"error": "invalid_client"}, "bad")]
        )
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(flow_mod.start_device_flow("microsoft_work", [MAIL_READ]))
        msg = str(exc.value)
        assert "GAIA_MICROSOFT_WORK_CLIENT_ID" in msg

    def test_personal_account_only_app_points_at_personal_connector(self, monkeypatch):
        # D11 (#2628): AADSTS9002346 fires when a personal-account-only app
        # registration is used against a non-consumers authority — under
        # the split, that is exactly "microsoft_work" (organizations). The
        # error must point at the "microsoft" connector to use instead. Same
        # mocked AADSTS9002346 response shape as before (A8) — this still
        # proves the real Microsoft error routes correctly, only the
        # remediation text changed.
        monkeypatch.setenv("GAIA_MICROSOFT_WORK_CLIENT_ID", "work-client-id")
        aad_error = (
            "AADSTS9002346: Application 'x' is configured for use by Microsoft "
            "Account users only. Please use the /consumers endpoint to serve "
            "this request."
        )
        _install_responses(
            monkeypatch,
            [_FakeResp(400, {"error": "invalid_request"}, aad_error)],
        )
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(flow_mod.start_device_flow("microsoft_work", [MAIL_READ]))
        msg = str(exc.value)
        assert "personal Microsoft accounts only" in msg
        assert "gaia connectors connect microsoft --device" in msg

    def test_provider_without_device_support_raises(self, monkeypatch):
        # Google has no device_code_url -> loud, actionable error.
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "g")
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_SECRET", "s")
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(flow_mod.start_device_flow("google", []))
        assert "does not support the device-code flow" in str(exc.value)


# Graph /me: the stable id is what tells one Microsoft account from another.
_ME = {"id": "user-oid", "mail": "user@example.com"}


class TestPollDeviceFlow:
    def _success_payload(self):
        return {
            "access_token": "AT",
            "refresh_token": "RT-value",
            "expires_in": 3600,
            "id_token": _id_token({"preferred_username": "user@example.com"}),
            "scope": MAIL_READ,
        }

    def test_pending_then_success_persists_connection(self, monkeypatch):
        _install_responses(
            monkeypatch,
            [
                _FakeResp(400, {"error": "authorization_pending"}),
                _FakeResp(400, {"error": "slow_down"}),
                _FakeResp(200, self._success_payload()),
            ],
            get_responses=[_FakeResp(200, _ME)],
        )
        result = asyncio.run(
            flow_mod.poll_device_flow(
                "microsoft", "DEV", scopes=[MAIL_READ], interval=1, expires_in=900
            )
        )
        assert result["account_email"] == "user@example.com"

        # Connection persisted through the real (in-memory) keyring store.
        from gaia.connectors.store import peek_connection

        blob = peek_connection("microsoft")
        assert blob is not None
        assert blob["refresh_token"] == "RT-value"
        # D8: the device-code exchange records the minting tenant too — not
        # just the loopback path.
        assert blob["tenant"] == "consumers"

    def test_narrower_returned_scope_is_what_gets_persisted(self, monkeypatch):
        """D6/AC-10 (#2730), device-flow path: the connection must record
        what the token endpoint actually returned, not the full request."""
        other_scope = "https://graph.microsoft.com/Calendars.ReadWrite"
        payload = self._success_payload()
        payload["scope"] = MAIL_READ  # the calendar scope was declined
        _install_responses(
            monkeypatch, [_FakeResp(200, payload)], get_responses=[_FakeResp(200, _ME)]
        )

        result = asyncio.run(
            flow_mod.poll_device_flow(
                "microsoft",
                "DEV",
                scopes=[MAIL_READ, other_scope],
                interval=1,
                expires_in=900,
            )
        )
        assert result["scopes"] == [MAIL_READ]

        from gaia.connectors.store import peek_connection

        blob = peek_connection("microsoft")
        assert blob["scopes"] == [MAIL_READ]

    def test_grant_agents_committed_on_success(self, monkeypatch):
        other_scope = "https://graph.microsoft.com/Calendars.ReadWrite"
        payload = self._success_payload()
        payload["scope"] = MAIL_READ
        _install_responses(
            monkeypatch, [_FakeResp(200, payload)], get_responses=[_FakeResp(200, _ME)]
        )
        asyncio.run(
            flow_mod.poll_device_flow(
                "microsoft",
                "DEV",
                scopes=[MAIL_READ, other_scope],
                grant_agents={"installed:email": [MAIL_READ, other_scope]},
            )
        )
        from gaia.connectors.grants import list_agent_grants

        grants = list_agent_grants("microsoft")
        assert grants.get("installed:email") == [MAIL_READ]

    def test_empty_effective_grant_fails_loudly(self, monkeypatch):
        payload = self._success_payload()
        payload["scope"] = ""
        _install_responses(
            monkeypatch, [_FakeResp(200, payload)], get_responses=[_FakeResp(200, _ME)]
        )

        with pytest.raises(GrantAfterConnectError, match="granted none"):
            asyncio.run(
                flow_mod.poll_device_flow(
                    "microsoft",
                    "DEV",
                    scopes=[MAIL_READ],
                    grant_agents={"installed:email": [MAIL_READ]},
                )
            )

    def test_account_email_falls_back_to_userinfo(self, monkeypatch):
        # Device-code id_token often carries no decodable email — the flow then
        # GETs the provider userinfo_url (Graph /me) with the access token.
        payload = self._success_payload()
        payload["id_token"] = ""  # no claim to decode
        _install_responses(
            monkeypatch,
            [_FakeResp(200, payload)],
            get_responses=[
                _FakeResp(
                    200,
                    {
                        "id": "user-oid",
                        "mail": "user@example.com",
                        "userPrincipalName": "u@example.com",
                    },
                )
            ],
        )
        result = asyncio.run(
            flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
        )
        assert result["account_email"] == "user@example.com"
        # The /me endpoint was queried with the bearer token.
        assert _FakeAsyncClient.get_calls
        assert "/me" in _FakeAsyncClient.get_calls[0][0]

    def test_sign_in_fails_when_the_account_cannot_be_identified(self, monkeypatch):
        payload = self._success_payload()
        payload["id_token"] = ""
        _install_responses(
            monkeypatch,
            [_FakeResp(200, payload)],
            get_responses=[_FakeResp(403, {}, "forbidden")],
        )
        with pytest.raises(ConnectorsError, match="HTTP 403"):
            asyncio.run(
                flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
            )
        from gaia.connectors.store import peek_connection

        assert peek_connection("microsoft") is None

    def test_declined_raises_consent_denied(self, monkeypatch):
        _install_responses(
            monkeypatch, [_FakeResp(400, {"error": "authorization_declined"})]
        )
        with pytest.raises(ConsentDeniedError):
            asyncio.run(
                flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
            )

    def test_expired_token_raises_timeout(self, monkeypatch):
        _install_responses(monkeypatch, [_FakeResp(400, {"error": "expired_token"})])
        with pytest.raises(FlowTimeoutError):
            asyncio.run(
                flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
            )

    def test_missing_refresh_token_raises(self, monkeypatch):
        payload = self._success_payload()
        del payload["refresh_token"]
        _install_responses(
            monkeypatch, [_FakeResp(200, payload)], get_responses=[_FakeResp(200, _ME)]
        )
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(
                flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
            )
        assert "offline_access" in str(exc.value)


class TestProviderBodyNeverLeaks:
    """#3875: a failed device-code request answers a POST that carried the
    device code, and providers echo request context into error bodies. When
    the body has no structured ``error_description`` the code used to fall
    back to ``resp.text`` — putting the raw body into the exception message,
    which ``authorize_device`` forwards to the Agent UI over SSE. Only the
    status code and the provider's own bounded RFC 6749 fields may surface.
    """

    #: Shaped like a credential echoed back in an unstructured error body.
    LEAKY_BODY = (
        "invalid_grant: device_code "
        "OAQABAAEAAAD--token-shaped-secret-value was rejected"
    )

    def test_poll_failure_without_structured_fields_omits_the_body(
        self, monkeypatch, caplog
    ):
        _install_responses(
            monkeypatch, [_FakeResp(400, {"error": "invalid_grant"}, self.LEAKY_BODY)]
        )
        with caplog.at_level("WARNING", logger="gaia.connectors.flow"):
            with pytest.raises(ConnectorsError) as exc:
                asyncio.run(
                    flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
                )

        message = str(exc.value)
        assert self.LEAKY_BODY not in message
        assert "token-shaped-secret-value" not in message
        assert exc.value.error_description == ""
        # Still reported honestly: status code + the provider's error code.
        assert "400" in message
        assert "invalid_grant" in message

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "token-shaped-secret-value" not in log_text

    def test_poll_failure_with_non_string_description_omits_the_body(self, monkeypatch):
        # A provider that nests an object under error_description must yield
        # no detail, not a stringified fragment of its body.
        _install_responses(
            monkeypatch,
            [
                _FakeResp(
                    400,
                    {"error": "invalid_grant", "error_description": {"code": 70016}},
                    self.LEAKY_BODY,
                )
            ],
        )
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(
                flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
            )
        assert exc.value.error_description == ""
        assert "70016" not in str(exc.value)

    def test_start_failure_omits_the_body(self, monkeypatch, caplog):
        _install_responses(
            monkeypatch, [_FakeResp(400, {"error": "invalid_client"}, self.LEAKY_BODY)]
        )
        with caplog.at_level("WARNING", logger="gaia.connectors.flow"):
            with pytest.raises(ConnectorsError) as exc:
                asyncio.run(flow_mod.start_device_flow("microsoft", [MAIL_READ]))

        message = str(exc.value)
        assert self.LEAKY_BODY not in message
        assert "token-shaped-secret-value" not in message
        assert "400" in message
        assert "invalid_client" in message
        assert "GAIA_MICROSOFT_CLIENT_ID" in message

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "token-shaped-secret-value" not in log_text

    def test_start_failure_with_unparseable_body_still_names_the_status(
        self, monkeypatch
    ):
        # No JSON at all — there is nothing bounded to report but the status,
        # and the remediation must still be there.
        _install_responses(monkeypatch, [_FakeResp(502, None, self.LEAKY_BODY)])
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(flow_mod.start_device_flow("microsoft", [MAIL_READ]))
        message = str(exc.value)
        assert self.LEAKY_BODY not in message
        assert "502" in message
        assert "GAIA_MICROSOFT_CLIENT_ID" in message


class TestDeviceSignInIntent:
    """``--add`` and ``--account`` mean the same with ``--device`` as without."""

    _DEVICE = {
        "device_code": "DEV",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://microsoft.com/devicelogin",
        "expires_in": 900,
        "interval": 5,
    }

    def _old_account_without_an_address(self):
        # Old builds stored "default" when the Graph /me lookup failed.
        from gaia.connectors.providers import get as get_provider
        from gaia.connectors.store import save_connection

        save_connection(
            provider="microsoft",
            account_email="default",
            refresh_token="rt-old",
            scopes=[MAIL_READ],
            client_id_hash=get_provider("microsoft").client_id_hash,
        )

    def _sign_in(self, monkeypatch, **intent):
        payload = TestPollDeviceFlow()._success_payload()
        payload["id_token"] = ""
        _install_responses(
            monkeypatch,
            [_FakeResp(200, self._DEVICE), _FakeResp(200, payload)],
            get_responses=[_FakeResp(200, _ME)],
        )

        async def _run():
            info = await flow_mod.start_device_flow("microsoft", [MAIL_READ], **intent)
            return await flow_mod.poll_device_flow(
                "microsoft",
                info["device_code"],
                scopes=info["scopes"],
                intent=info["intent"],
            )

        return asyncio.run(_run())

    def test_adding_never_replaces_an_old_account_stored_without_an_address(
        self, monkeypatch
    ):
        from gaia.connectors import accounts
        from gaia.connectors.store import peek_connection

        self._old_account_without_an_address()

        result = self._sign_in(monkeypatch, add_account=True)

        assert result["added"] is True
        assert peek_connection("microsoft")["refresh_token"] == "rt-old"
        assert sorted(a.email for a in accounts.list_accounts("microsoft")) == [
            "default",
            "user@example.com",
        ]

    def test_reconnecting_names_the_account_asked_for(self, monkeypatch):
        from gaia.connectors import accounts
        from gaia.connectors.providers import get as get_provider

        accounts.save_signed_in_account(
            "microsoft",
            account_id="pat-id",
            email="pat@outlook.com",
            refresh_token="rt-pat",
            scopes=[MAIL_READ],
            client_id_hash=get_provider("microsoft").client_id_hash,
        )

        result = self._sign_in(monkeypatch, account="pat@outlook.com")

        assert result["added"] is True
        assert result["requested_account_id"] == "pat-id"
        assert result["is_default"] is False

    def test_an_unknown_account_fails_before_the_code_is_requested(self, monkeypatch):
        from gaia.connectors.errors import UnknownAccountError

        _install_responses(monkeypatch, [])
        with pytest.raises(UnknownAccountError):
            asyncio.run(
                flow_mod.start_device_flow(
                    "microsoft", [MAIL_READ], account="nobody@example.com"
                )
            )
        assert _FakeAsyncClient.calls == []

    def test_add_and_account_together_are_refused(self, monkeypatch):
        _install_responses(monkeypatch, [])
        with pytest.raises(ConnectorsError, match="not both"):
            asyncio.run(
                flow_mod.start_device_flow(
                    "microsoft", [MAIL_READ], add_account=True, account="a@b.com"
                )
            )
