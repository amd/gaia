# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2116 — Google's API-not-enabled 403 maps to an actionable error.

A fresh BYO Google Cloud project without the Gmail/Calendar API enabled
returns Google's verbatim 403 (``reason == "accessNotConfigured"``). The
backends must re-raise with GAIA's three-part actionable message naming
the enable URL Google returns in ``extendedHelp`` + the 1-2 minute
propagation note — not dump the raw JSON body.
"""

from __future__ import annotations

import json

import httpx
import pytest

pytest.importorskip("gaia_agent_email")

from gaia.connectors.errors import ConnectorsError  # noqa: E402
from gaia_agent_email.google_errors import (  # noqa: E402
    access_not_configured_message,
    access_not_configured_url,
)

GMAIL_ENABLE_URL = (
    "https://console.developers.google.com/apis/api/"
    "gmail.googleapis.com/overview?project=123456789"
)
CALENDAR_ENABLE_URL = (
    "https://console.developers.google.com/apis/api/"
    "calendar-json.googleapis.com/overview?project=123456789"
)


def _access_not_configured_body(api_label: str, enable_url: str) -> dict:
    return {
        "error": {
            "code": 403,
            "message": (
                f"{api_label} has not been used in project 123456789 before "
                "or it is disabled."
            ),
            "errors": [
                {
                    "message": f"{api_label} has not been used ...",
                    "domain": "usageLimits",
                    "reason": "accessNotConfigured",
                    "extendedHelp": enable_url,
                }
            ],
            "status": "PERMISSION_DENIED",
        }
    }


def _mock_client(status: int, body: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


# -- helper unit tests ------------------------------------------------------


def test_helper_returns_url_for_access_not_configured():
    resp = httpx.Response(
        403, json=_access_not_configured_body("Gmail API", GMAIL_ENABLE_URL)
    )
    assert access_not_configured_url(resp) == GMAIL_ENABLE_URL


def test_helper_returns_none_for_other_403():
    resp = httpx.Response(
        403,
        json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}},
    )
    assert access_not_configured_url(resp) is None


def test_helper_returns_none_for_non_403():
    resp = httpx.Response(500, text="boom")
    assert access_not_configured_url(resp) is None


def test_helper_tolerates_malformed_body():
    resp = httpx.Response(403, text="not json")
    assert access_not_configured_url(resp) is None


def test_message_is_three_part_actionable():
    msg = access_not_configured_message("Gmail API", GMAIL_ENABLE_URL)
    assert "Gmail API is not enabled" in msg  # what failed
    assert GMAIL_ENABLE_URL in msg  # what to do (enable URL)
    assert "1-2 minutes" in msg  # propagation note
    assert "amd-gaia.ai/docs/guides/email" in msg  # where to look


# -- backend integration ----------------------------------------------------


def test_gmail_backend_maps_access_not_configured():
    from gaia_agent_email.gmail_backend import LiveGmailBackend

    client = _mock_client(
        403, _access_not_configured_body("Gmail API", GMAIL_ENABLE_URL)
    )
    backend = LiveGmailBackend(lambda: "tok", http_client=client)
    with pytest.raises(ConnectorsError) as exc:
        backend.get_user_email()
    msg = str(exc.value)
    assert "Gmail API is not enabled" in msg
    assert GMAIL_ENABLE_URL in msg
    assert "1-2 minutes" in msg
    # Raw Google JSON must NOT leak through.
    assert "accessNotConfigured" not in msg
    assert "PERMISSION_DENIED" not in msg


def test_calendar_backend_maps_access_not_configured():
    from gaia_agent_email.calendar_backend import LiveCalendarBackend

    client = _mock_client(
        403, _access_not_configured_body("Calendar API", CALENDAR_ENABLE_URL)
    )
    backend = LiveCalendarBackend(lambda: "tok", http_client=client)
    with pytest.raises(ConnectorsError) as exc:
        backend.list_calendars()
    msg = str(exc.value)
    assert "Calendar API is not enabled" in msg
    assert CALENDAR_ENABLE_URL in msg
    assert "1-2 minutes" in msg
    assert "accessNotConfigured" not in msg


def test_gmail_backend_other_403_still_raw():
    """A non-accessNotConfigured 403 keeps the existing raw-body behavior."""
    from gaia_agent_email.gmail_backend import LiveGmailBackend

    body = {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}
    client = _mock_client(403, body)
    backend = LiveGmailBackend(lambda: "tok", http_client=client)
    with pytest.raises(ConnectorsError) as exc:
        backend.get_user_email()
    assert "returned 403" in str(exc.value)
