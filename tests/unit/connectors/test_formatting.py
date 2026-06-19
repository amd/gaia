# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia.connectors.formatting.format_connector_error``.

Guards the public formatter against silent re-inlining (e.g. someone
copy-pasting the body back into ``connectors_demo/agent.py`` without
realising the email agent now imports from this module).
"""

from __future__ import annotations

import pytest

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectorsError,
)
from gaia.connectors.formatting import format_connector_error


def test_format_connector_error_public_api():
    """One assertion per error class — keeps the surface stable."""
    # AuthRequiredError variants
    not_connected = AuthRequiredError(
        AuthRequiredError.Reason.NOT_CONNECTED, provider="google"
    )
    assert "NOT_CONNECTED" in format_connector_error(not_connected)
    assert "google" in format_connector_error(not_connected)

    not_granted = AuthRequiredError(
        AuthRequiredError.Reason.AGENT_NOT_GRANTED,
        provider="google",
        agent_id="installed:connectors-demo",
        missing_scopes=["scope-a"],
    )
    assert "AGENT_NOT_GRANTED" in format_connector_error(not_granted)
    assert "scope-a" in format_connector_error(not_granted)

    reauth = AuthRequiredError(
        AuthRequiredError.Reason.REAUTH_REQUIRED, provider="google"
    )
    assert "NOT_CONNECTED" in format_connector_error(reauth)

    missing_scopes = AuthRequiredError(
        AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES,
        provider="google",
    )
    assert "AUTH_REQUIRED" in format_connector_error(missing_scopes)

    # ConfigurationError
    cfg = ConfigurationError("missing GAIA_FOO")
    assert "CONFIG_ERROR" in format_connector_error(cfg)
    assert "missing GAIA_FOO" in format_connector_error(cfg)

    # ConnectorsError (base class — should still be tagged)
    base = ConnectorsError("something broke")
    assert "CONNECTOR_ERROR" in format_connector_error(base)

    # Subclass of ConnectorsError that isn't AuthRequired/Configuration
    revoked = ConnectionRevokedError("google")
    assert "CONNECTOR_ERROR" in format_connector_error(revoked)

    # Unexpected non-connectors error
    plain = ValueError("oops")
    assert "UNEXPECTED_ERROR" in format_connector_error(plain)
    assert "ValueError" in format_connector_error(plain)


def test_format_connector_error_email_agent_grant_migration():
    """
    A user whose Google grant predates #962 (no ``gmail.modify``) sees a
    re-grant message tailored to the email agent — generic
    ``AGENT_NOT_GRANTED`` wording would not tell them which scopes to
    grant or that "Reconnect" is the path.
    """
    err = AuthRequiredError(
        AuthRequiredError.Reason.AGENT_NOT_GRANTED,
        provider="google",
        agent_id="installed:email",
        missing_scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    msg = format_connector_error(err)
    assert "Email agent" in msg
    assert "gmail.modify" in msg
    assert "Reconnect" in msg


def test_format_connector_error_email_agent_microsoft_falls_through():
    """
    A Microsoft grant gap for ``installed:email`` must NOT show the Google
    "Reconnect" migration text (issue #1751). The override is keyed by
    ``(agent_id, provider)``, so a Microsoft failure falls through to the
    generic provider-aware branch — naming Microsoft and the missing Graph
    scopes, not Google.
    """
    err = AuthRequiredError(
        AuthRequiredError.Reason.AGENT_NOT_GRANTED,
        provider="microsoft",
        agent_id="installed:email",
        missing_scopes=["Mail.ReadWrite", "Mail.Send"],
    )
    msg = format_connector_error(err)
    assert "AGENT_NOT_GRANTED" in msg
    assert "microsoft" in msg
    assert "Mail.ReadWrite" in msg
    assert "Mail.Send" in msg
    # The Google migration string must not leak onto a Microsoft failure.
    assert "Google" not in msg
    assert "gmail.modify" not in msg


def test_format_connector_error_unknown_agent_falls_back_to_generic():
    """An agent without a registered override gets the generic message."""
    err = AuthRequiredError(
        AuthRequiredError.Reason.AGENT_NOT_GRANTED,
        provider="google",
        agent_id="builtin:future-agent",
        missing_scopes=["scope-x"],
    )
    msg = format_connector_error(err)
    assert "AGENT_NOT_GRANTED" in msg
    assert "scope-x" in msg


@pytest.mark.parametrize(
    "exc",
    [
        AuthRequiredError(AuthRequiredError.Reason.NOT_CONNECTED, provider="google"),
        ConfigurationError("missing"),
        ConnectorsError("broke"),
        ValueError("oops"),
    ],
)
def test_format_connector_error_returns_single_line(exc):
    """Output is a single line — agents stuff this into a JSON envelope."""
    result = format_connector_error(exc)
    assert "\n" not in result, f"multi-line output for {type(exc).__name__}: {result!r}"
