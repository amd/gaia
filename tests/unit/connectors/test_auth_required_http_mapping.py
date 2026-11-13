# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A connector auth failure reaches the Agent UI as a status it can act on."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from gaia.connectors.errors import AuthRequiredError  # noqa: E402
from gaia.ui.routers.connectors import _raise_http_for  # noqa: E402

FLAGSHIP = "installed:gaia"
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR = "https://www.googleapis.com/auth/calendar.events"


def test_a_missing_grant_is_a_403_that_names_the_agent_and_connector():
    http = _raise_http_for(
        AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id=FLAGSHIP,
            missing_scopes=[GMAIL_READ],
        )
    )
    assert http.status_code == 403
    assert http.detail == {
        "error": "agent_not_granted",
        "connector_id": "google",
        "agent_id": FLAGSHIP,
        "missing_scopes": [GMAIL_READ],
    }


def test_a_connection_short_of_scopes_is_a_403_that_lists_them():
    http = _raise_http_for(
        AuthRequiredError(
            AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES,
            provider="google",
            agent_id=FLAGSHIP,
            missing_scopes=[CALENDAR],
        )
    )
    assert http.status_code == 403
    assert http.detail["error"] == "connection_missing_scopes"
    assert http.detail["missing_scopes"] == [CALENDAR]
    assert http.detail["agent_id"] == FLAGSHIP


@pytest.mark.parametrize(
    "reason",
    [AuthRequiredError.Reason.NOT_CONNECTED, AuthRequiredError.Reason.REAUTH_REQUIRED],
)
def test_no_usable_connection_is_a_401(reason):
    http = _raise_http_for(
        AuthRequiredError(reason, provider="microsoft", agent_id=FLAGSHIP)
    )
    assert http.status_code == 401
    assert http.detail == {
        "error": reason.value,
        "connector_id": "microsoft",
        "agent_id": FLAGSHIP,
    }
