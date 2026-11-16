# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Sign-in secrets never reach a log, an error message or a UI event.

Drives the real loopback callback and token exchange (token endpoint mocked)
and checks every captured log record at every level, the raised error text and
every emitted event for the authorization code, the state, the PKCE verifier
and the tokens.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from gaia.connectors import flow
from gaia.connectors.errors import OAuthProviderError
from gaia.connectors.flow import complete_authorization, start_authorization
from gaia.log_reader import redact, redact_lines

pytestmark = pytest.mark.allow_network

CODE = "sec-test-auth-code-4f1e9a"
ACCESS = "sec-test-access-token-77ab"
REFRESH = "sec-test-refresh-token-91cd"
TOKEN_URL = "https://oauth2.googleapis.com/token"


@pytest.fixture(autouse=True)
def _no_browser(no_browser):
    """Every flow test runs with the browser launch stubbed (see conftest)."""


@pytest.fixture
def events(monkeypatch):
    seen: list = []

    async def _capture(event_type, payload):
        seen.append((event_type, payload))

    monkeypatch.setattr(flow, "emit", _capture)
    return seen


def _all_log_text(caplog) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        if record.exc_text:
            parts.append(record.exc_text)
    return "\n".join(parts)


def _assert_no_secrets(text: str, secrets) -> None:
    for secret in secrets:
        assert secret not in text, f"secret value leaked: {secret!r}"


async def _browser_get(url: str) -> int:
    """Hit the callback the way a browser would; urllib logs nothing itself."""

    def _get() -> int:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    return await asyncio.to_thread(_get)


async def _start():
    info = await start_authorization("google", scopes=["openid"])
    params = parse_qs(urlparse(info["authorization_url"]).query)
    pending = flow._pending[info["flow_id"]]
    return info, params["redirect_uri"][0], params["state"][0], pending.code_verifier


class TestCallbackLogging:
    @respx.mock
    async def test_successful_sign_in_logs_no_secret(
        self, google_provider, caplog, events
    ):
        caplog.set_level(logging.DEBUG)
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": ACCESS,
                    "refresh_token": REFRESH,
                    "expires_in": 3600,
                    "scope": "openid",
                    "id_token": "h.eyJlbWFpbCI6ICJhbGljZUBleGFtcGxlLmNvbSJ9.s",
                },
            )
        )
        respx.route(host="127.0.0.1").pass_through()

        info, redirect_uri, state, verifier = await _start()
        assert await _browser_get(f"{redirect_uri}?code={CODE}&state={state}") == 200
        result = await asyncio.wait_for(
            complete_authorization(info["flow_id"]), timeout=2.0
        )
        assert result["account_email"] == "alice@example.com"

        # The exchange really carried the code and verifier: the test would
        # prove nothing if they never existed in this process.
        sent = respx.calls.last.request.content.decode()
        assert CODE in sent and verifier in sent

        secrets = (CODE, state, verifier, ACCESS, REFRESH)
        _assert_no_secrets(_all_log_text(caplog), secrets)
        _assert_no_secrets(repr(events), secrets)
        assert {name for name, _ in events} == {
            "connector.oauth.completed",
            "connection.connected",
        }

    @respx.mock
    async def test_callback_route_is_still_logged_without_its_values(
        self, google_provider, caplog, events
    ):
        caplog.set_level(logging.DEBUG)
        respx.route(host="127.0.0.1").pass_through()

        info, redirect_uri, state, _ = await _start()
        assert (
            await _browser_get(f"{redirect_uri}?code={CODE}&state=wrong-{state}") == 400
        )

        text = _all_log_text(caplog)
        _assert_no_secrets(text, (CODE, state))
        assert "flow: callback" in text
        assert "code" in text and "state" in text
        await flow.cancel_flow(info["flow_id"])

    @respx.mock
    async def test_rejected_exchange_keeps_echoed_secrets_out_of_the_error(
        self, google_provider, caplog, events
    ):
        caplog.set_level(logging.DEBUG)
        respx.route(host="127.0.0.1").pass_through()
        info, redirect_uri, state, verifier = await _start()
        # A provider that echoes request context back into its error body.
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": (
                        f"code {CODE} with verifier {verifier} is not valid"
                    ),
                },
            )
        )

        assert await _browser_get(f"{redirect_uri}?code={CODE}&state={state}") == 502

        with pytest.raises(OAuthProviderError) as exc_info:
            await asyncio.wait_for(complete_authorization(info["flow_id"]), timeout=2.0)

        secrets = (CODE, state, verifier)
        err = exc_info.value
        _assert_no_secrets(str(err), secrets)
        _assert_no_secrets(err.error_description, secrets)
        assert "is not valid" in err.error_description
        _assert_no_secrets(_all_log_text(caplog), secrets)
        _assert_no_secrets(repr(events), secrets)

    async def test_browser_launch_failure_does_not_log_the_sign_in_link(
        self, google_provider, caplog, monkeypatch
    ):
        caplog.set_level(logging.DEBUG)

        def _fail(url):
            raise OSError(2, "The system cannot find the file specified", url)

        monkeypatch.setattr("webbrowser.open", _fail)
        info = await start_authorization("google", scopes=["openid"])
        state = parse_qs(urlparse(info["authorization_url"]).query)["state"][0]
        for _ in range(50):
            if any("webbrowser.open failed" in r.getMessage() for r in caplog.records):
                break
            await asyncio.sleep(0.01)

        text = _all_log_text(caplog)
        assert "webbrowser.open failed" in text
        assert "FileNotFoundError" in text
        _assert_no_secrets(text, (state,))
        await flow.cancel_flow(info["flow_id"])


class TestDeviceFlowError:
    @respx.mock
    async def test_echoed_device_code_is_scrubbed(self, monkeypatch):
        from gaia.connectors.providers import _registry
        from gaia.connectors.providers import get as get_provider

        monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-ms-client")
        _registry.clear()
        provider = get_provider("microsoft")
        device_code = "sec-test-device-code-5521"
        respx.post(provider.token_url).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": "invalid_request",
                    "error_description": f"device_code {device_code} is malformed",
                },
            )
        )

        with pytest.raises(OAuthProviderError) as exc_info:
            await flow.poll_device_flow(
                "microsoft",
                device_code,
                scopes=provider.default_scopes,
                interval=1,
                expires_in=5,
            )
        assert device_code not in str(exc_info.value)
        assert "is malformed" in str(exc_info.value)


class TestOlderLogLinesRedacted:
    """``gaia diagnostics`` and the log viewer redact what older builds wrote."""

    LINE = (
        "2026-11-10 09:12:44 | INFO | aiohttp.access.log | 127.0.0.1 [10/Nov/2026"
        f':09:12:44 +0000] "GET /callback?code={CODE}&state=tcZrS3cr3tState'
        '&scope=openid HTTP/1.1" 200 312 "-" "Mozilla/5.0"'
    )

    def test_redact_hides_code_and_state_in_a_callback_line(self):
        out = redact(self.LINE)
        assert CODE not in out
        assert "tcZrS3cr3tState" not in out
        assert "GET /callback?code=[redacted]&state=[redacted]&scope=openid" in out

    def test_redact_lines_hides_them_in_a_whole_log(self):
        data = ("first line\n" + self.LINE + "\nlast line\n").encode()
        out = redact_lines(data).decode()
        assert CODE not in out
        assert "tcZrS3cr3tState" not in out
        assert out.startswith("first line\n") and out.endswith("last line\n")

    @pytest.mark.parametrize(
        "line",
        [
            "process exited with code=1",
            "state=ready",
            "llama: n_ctx=65536 state saved",
        ],
    )
    def test_plain_key_value_words_are_left_alone(self, line):
        assert redact(line) == line
