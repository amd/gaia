# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia.connectors.errors``.

Acceptance: every error type subclasses ``ConnectorsError``, AuthRequiredError
exposes a ``Reason`` enum with exactly the four documented values, and every
error message names what failed / what to do / where to look (per CLAUDE.md
"fail loudly" rule).
"""

from __future__ import annotations

import pytest

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectorsError,
    ConsentDeniedError,
    FlowInProgressError,
    FlowTimeoutError,
    OAuthClientNotConfiguredError,
    ScopeMismatchError,
)


class TestHierarchy:
    def test_every_error_is_a_connections_error(self):
        assert issubclass(AuthRequiredError, ConnectorsError)
        assert issubclass(ConnectionRevokedError, ConnectorsError)
        assert issubclass(ScopeMismatchError, ConnectorsError)
        assert issubclass(ConsentDeniedError, ConnectorsError)
        assert issubclass(FlowTimeoutError, ConnectorsError)
        assert issubclass(FlowInProgressError, ConnectorsError)
        assert issubclass(ConfigurationError, ConnectorsError)

    def test_connections_error_is_an_exception(self):
        assert issubclass(ConnectorsError, Exception)


class TestAuthRequiredErrorReason:
    def test_reason_enum_has_exactly_four_values(self):
        values = {r.value for r in AuthRequiredError.Reason}
        assert values == {
            "not_connected",
            "agent_not_granted",
            "connection_missing_scopes",
            "reauth_required",
        }

    def test_reason_enum_is_string_serializable(self):
        # Router serializes reasons into JSON; enum must coerce to str cleanly.
        assert str(AuthRequiredError.Reason.NOT_CONNECTED.value) == "not_connected"

    def test_construction_records_reason_and_metadata(self):
        err = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="builtin:chat",
        )
        assert err.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
        assert err.provider == "google"
        assert err.agent_id == "builtin:chat"

    def test_message_names_what_to_do(self):
        # Per CLAUDE.md, every error message names: what failed, what to do,
        # where to look. AGENT_NOT_GRANTED messages must mention granting.
        err = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="inbox_zero",
        )
        msg = str(err).lower()
        assert "google" in msg
        assert "grant" in msg

    def test_not_connected_reason_directs_to_connect(self):
        err = AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED,
            provider="google",
        )
        msg = str(err).lower()
        assert "connect" in msg
        assert "google" in msg

    def test_reauth_required_reason_mentions_reauthorize(self):
        err = AuthRequiredError(
            AuthRequiredError.Reason.REAUTH_REQUIRED,
            provider="google",
        )
        msg = str(err).lower()
        # Acceptable: "reauth", "re-auth", "reauthorize", "re-authorize",
        # "reconnect", or "authenticate again". Must direct user to act.
        assert any(token in msg for token in ("reauth", "re-auth", "reconnect"))


class TestScopeMismatchError:
    def test_required_and_granted_attributes_set(self):
        err = ScopeMismatchError(
            required=["gmail.readonly", "gmail.send"],
            granted=["gmail.readonly"],
            provider="google",
        )
        assert err.required == ["gmail.readonly", "gmail.send"]
        assert err.granted == ["gmail.readonly"]
        assert err.provider == "google"

    def test_message_names_missing_scopes(self):
        err = ScopeMismatchError(
            required=["gmail.send"],
            granted=["gmail.readonly"],
            provider="google",
        )
        assert "gmail.send" in str(err)

    def test_missing_scopes_property(self):
        err = ScopeMismatchError(
            required=["a", "b", "c"],
            granted=["a"],
            provider="google",
        )
        assert sorted(err.missing_scopes) == ["b", "c"]


class TestConnectionRevokedError:
    def test_provider_attribute_set(self):
        err = ConnectionRevokedError(provider="google")
        assert err.provider == "google"

    def test_message_directs_to_reconnect(self):
        err = ConnectionRevokedError(provider="google")
        msg = str(err).lower()
        assert "google" in msg
        assert any(token in msg for token in ("reconnect", "reauth", "re-auth"))


class TestConsentDeniedError:
    def test_subclass(self):
        # OAuth ?error=access_denied surfaces here.
        with pytest.raises(ConnectorsError):
            raise ConsentDeniedError("user denied consent")


class TestFlowTimeoutAndInProgress:
    def test_flow_timeout_subclass(self):
        with pytest.raises(ConnectorsError):
            raise FlowTimeoutError("flow exceeded 120s")

    def test_flow_in_progress_subclass(self):
        with pytest.raises(ConnectorsError):
            raise FlowInProgressError("a flow is already pending")


class TestConfigurationError:
    def test_message_names_env_var_when_provided(self):
        err = ConfigurationError(
            "GAIA_GOOGLE_CLIENT_ID is not set; see "
            "docs/runbooks/google-oauth-client.md"
        )
        s = str(err)
        assert "GAIA_GOOGLE_CLIENT_ID" in s
        assert "docs/runbooks/google-oauth-client.md" in s


class TestOAuthClientNotConfiguredError:
    """The self-documenting missing-client error (#2347): a headless user must
    be able to unblock themselves from the message alone."""

    def _err(self, **overrides):
        kwargs = dict(
            provider_id="google",
            provider_label="Google",
            console_steps="  1. Do a thing at https://console.example",
            docs="https://amd-gaia.ai/docs/connectors/google",
            example="  For the email agent:\n    gaia connectors connect google ...",
        )
        kwargs.update(overrides)
        return OAuthClientNotConfiguredError(kwargs.pop("provider_id"), **kwargs)

    def test_is_a_configuration_error(self):
        # Subclass so the CLI (exit 3) and the UI router (503) keep handling it.
        err = self._err()
        assert isinstance(err, ConfigurationError)
        assert isinstance(err, ConnectorsError)
        assert err.provider_id == "google"
        assert err.provider_label == "Google"

    def test_message_is_self_documenting(self):
        s = str(self._err())
        assert "not configured" in s
        assert "https://console.example" in s  # console setup steps
        # Exact CLI commands, spec-driven off provider_id.
        assert "gaia connectors configure google --client-id" in s
        # connect MUST authorize scopes (the #2347 correctness gap) ...
        assert "gaia connectors connect google --scopes" in s
        # ... and the grant must use the SAME scopes.
        assert "gaia connectors grants grant google" in s
        assert "SAME scopes on connect and grant" in s
        assert "amd-gaia.ai/docs/connectors/google" in s
        # UI path named too.
        assert "Settings -> Connections -> Google" in s

    def test_example_block_is_optional(self):
        # Omitting the example drops the copy-paste block but keeps the generic
        # command template (still names --scopes on connect).
        s = str(self._err(example=None))
        assert "For the email agent" not in s
        assert "gaia connectors connect google --scopes" in s
        assert "gaia connectors grants grant google <agent-id>" in s
