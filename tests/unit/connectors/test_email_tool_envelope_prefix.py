# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests verifying that email tool ConnectorError envelopes carry the correct
prefix for the frontend CTA to detect.  Root cause 2 of #1592.

The frontend ``isAuthRequiredMessage`` in ``EmailConnectCta.tsx`` checks for
``NOT_CONNECTED:``, ``AGENT_NOT_GRANTED:``, or ``AUTH_REQUIRED:`` prefixes.
Before this fix, email tools called ``_envelope_err(str(exc))`` which produced
the default AuthRequiredError message body WITHOUT these prefixes, so the CTA
never fired.

After the fix, email tools call ``_envelope_err(format_connector_error(exc))``,
which always produces one of the expected prefixes.
"""

from __future__ import annotations

import json

from gaia.connectors.errors import AuthRequiredError
from gaia.connectors.formatting import format_connector_error


class TestFormatConnectorErrorPrefixes:
    """format_connector_error must always return a string with a CTA-detectable prefix."""

    def test_agent_not_granted_has_prefix(self):
        exc = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="installed:email",
            missing_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        result = format_connector_error(exc)
        # The frontend matches "AGENT_NOT_GRANTED:" OR the installed:email
        # override message which contains "Email agent needs additional".
        assert (
            "AGENT_NOT_GRANTED:" in result or "Email agent needs additional" in result
        )

    def test_not_connected_has_prefix(self):
        exc = AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED, provider="google"
        )
        result = format_connector_error(exc)
        assert result.startswith("NOT_CONNECTED:")

    def test_reauth_required_has_prefix(self):
        exc = AuthRequiredError(
            AuthRequiredError.Reason.REAUTH_REQUIRED, provider="google"
        )
        result = format_connector_error(exc)
        assert result.startswith("NOT_CONNECTED:")

    def test_generic_agent_not_granted_has_prefix(self):
        exc = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="builtin:other-agent",
            missing_scopes=["scope-x"],
        )
        result = format_connector_error(exc)
        assert result.startswith("AGENT_NOT_GRANTED:")


class TestEmailToolEnvelopePrefix:
    """Simulate what the email tools must produce after the fix.

    The actual tool function bodies call ``format_connector_error(exc)`` instead
    of ``str(exc)``.  We test the contract here to guard against future regressions.
    """

    def _make_envelope_err(self, message: str) -> str:
        return json.dumps({"ok": False, "error": message})

    def test_agent_not_granted_envelope_matches_cta_detector(self):
        exc = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="installed:email",
            missing_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        msg = format_connector_error(exc)
        envelope = self._make_envelope_err(msg)
        parsed = json.loads(envelope)
        error_text = parsed["error"]
        # Either the canonical prefix OR the agent-specific override message.
        has_prefix = (
            "AGENT_NOT_GRANTED:" in error_text
            or "Email agent needs additional" in error_text
            or "NOT_CONNECTED:" in error_text
            or "AUTH_REQUIRED:" in error_text
        )
        assert has_prefix, f"CTA prefix not found in: {error_text!r}"

    def test_not_connected_envelope_matches_cta_detector(self):
        exc = AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED, provider="google"
        )
        msg = format_connector_error(exc)
        envelope = self._make_envelope_err(msg)
        parsed = json.loads(envelope)
        assert "NOT_CONNECTED:" in parsed["error"]

    def test_str_exc_does_not_have_prefix(self):
        """Confirm the bug: raw str(exc) for AGENT_NOT_GRANTED lacks the prefix.
        This test documents why the fix (use format_connector_error) is needed."""
        exc = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="installed:email",
            missing_scopes=["scope-x"],
        )
        raw = str(exc)
        # The default message does NOT have the AGENT_NOT_GRANTED: prefix.
        assert "AGENT_NOT_GRANTED:" not in raw
        # But format_connector_error DOES have it (or the override).
        formatted = format_connector_error(exc)
        assert (
            "AGENT_NOT_GRANTED:" in formatted
            or "Email agent needs additional" in formatted
        )
