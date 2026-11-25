# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia.connectors.formatting.format_connector_error``.

Guards the public formatter against silent re-inlining (e.g. someone
copy-pasting the body into an agent without realising the email agent
imports from this module).
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
        agent_id="installed:my-agent",
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


def test_the_removed_email_agent_has_no_message_of_its_own():
    """A grant gap for the removed agent reads like any other agent's."""
    err = AuthRequiredError(
        AuthRequiredError.Reason.AGENT_NOT_GRANTED,
        provider="google",
        agent_id="installed:email",
        missing_scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    msg = format_connector_error(err)
    assert msg.startswith("AGENT_NOT_GRANTED:")
    assert "Email agent" not in msg
    assert "gmail.modify" in msg


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


def test_flagship_grant_gap_never_borrows_the_email_agents_remedy():
    """The email agent's migration text asks for gmail.modify + send. The
    read-only flagship must get the scope it actually lacks instead."""
    readonly = "https://www.googleapis.com/auth/gmail.readonly"
    err = AuthRequiredError(
        AuthRequiredError.Reason.AGENT_NOT_GRANTED,
        provider="google",
        agent_id="installed:gaia",
        missing_scopes=[readonly],
    )
    msg = format_connector_error(err)
    assert readonly in msg
    assert "gmail.send" not in msg
    assert "gmail.modify" not in msg


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
