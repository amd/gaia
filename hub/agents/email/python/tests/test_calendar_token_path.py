# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Calendar/Gmail shared-token-path tests (#1232 AC3).

``calendar_backend._get_calendar_token()`` must resolve access tokens through
the same ``gaia.connectors.api.get_access_token_sync`` path Gmail already
uses (``gmail_backend._get_gmail_token()``), instead of the older
``gaia.connectors.handler.get_credential_sync``. This locks in the call
contract (kwargs) and the error-propagation parity, including the new
scope-pre-flight behavior Calendar gains from the switch.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent_email")

from gaia.connectors.errors import AuthRequiredError  # noqa: E402
from gaia_agent_email.scopes import (  # noqa: E402
    AGENT_NAMESPACED_ID,
    CALENDAR_SCOPES,
)


def test_get_calendar_token_happy_path():
    """Token is returned as-is and the call contract matches Gmail's."""
    from gaia_agent_email.calendar_backend import _get_calendar_token

    with patch(
        "gaia_agent_email.calendar_backend.get_access_token_sync",
        return_value="fixed-token-123",
    ) as mock_get_token:
        token = _get_calendar_token()

    assert token == "fixed-token-123"
    mock_get_token.assert_called_once()
    kwargs = mock_get_token.call_args.kwargs
    assert kwargs["provider"] == "google"
    assert kwargs["agent_id"] == AGENT_NAMESPACED_ID
    assert kwargs["scopes"] == list(CALENDAR_SCOPES)


def test_get_calendar_token_propagates_agent_not_granted():
    """AGENT_NOT_GRANTED errors propagate unchanged (parity with Gmail)."""
    from gaia_agent_email.calendar_backend import _get_calendar_token

    err = AuthRequiredError(
        AuthRequiredError.Reason.AGENT_NOT_GRANTED,
        provider="google",
        agent_id="installed:email",
    )
    with patch(
        "gaia_agent_email.calendar_backend.get_access_token_sync",
        side_effect=err,
    ):
        with pytest.raises(AuthRequiredError) as exc:
            _get_calendar_token()

    assert exc.value is err
    assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED


def test_get_calendar_token_propagates_missing_scopes():
    """CONNECTION_MISSING_SCOPES propagates too — Calendar now gets the same
    scope pre-flight Gmail already had (deliberate behavior change)."""
    from gaia_agent_email.calendar_backend import _get_calendar_token

    err = AuthRequiredError(
        AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES,
        provider="google",
        missing_scopes=["https://www.googleapis.com/auth/calendar"],
    )
    with patch(
        "gaia_agent_email.calendar_backend.get_access_token_sync",
        side_effect=err,
    ):
        with pytest.raises(AuthRequiredError) as exc:
            _get_calendar_token()

    assert exc.value is err
    assert exc.value.reason is AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES
