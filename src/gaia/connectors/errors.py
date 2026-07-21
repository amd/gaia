# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Exception hierarchy for ``gaia.connectors``.

Every error names what failed, what the caller should do, and where to look —
the three things CLAUDE.md "fail loudly" rule requires for actionable errors.
The router in ``src/gaia/ui/routers/connections.py`` maps each type to a
specific HTTP response; the CLI prints them to stderr; the SDK lets callers
catch and react programmatically.

No silent fallbacks. Either the operation succeeds or one of these is raised.
"""

from __future__ import annotations

import enum
from typing import Iterable


class ConnectorsError(Exception):
    """Base class for every error raised by ``gaia.connectors``."""


class ConfigurationError(ConnectorsError):
    """Required configuration (env var, runbook entry) is missing."""


class OAuthClientNotConfiguredError(ConfigurationError):
    """An ``oauth_pkce`` connector has no OAuth *client* credentials configured.

    GAIA ships no OAuth credentials — each user creates their own client once in
    the provider's cloud console, then registers it. The message is
    self-documenting for a headless CLI user (the console setup steps plus the
    exact ``gaia connectors ...`` commands) and also names the Agent UI path, so
    whoever hits it can unblock themselves without leaving the terminal. Inherits
    :class:`ConfigurationError` so the CLI (exit 3) and the UI router (HTTP 503)
    keep handling it unchanged.
    """

    def __init__(
        self,
        provider_id: str,
        *,
        provider_label: str,
        console_steps: str,
        docs: str,
        example_grant: str | None = None,
    ):
        self.provider_id = provider_id
        self.provider_label = provider_label
        super().__init__(
            self._build_message(
                provider_id, provider_label, console_steps, docs, example_grant
            )
        )

    @staticmethod
    def _build_message(
        pid: str,
        label: str,
        console_steps: str,
        docs: str,
        example_grant: str | None,
    ) -> str:
        example = ""
        if example_grant:
            example = (
                f"      e.g. for the email agent:\n"
                f"      gaia connectors grants grant {pid} {example_grant}\n"
            )
        return (
            f"{label} OAuth client is not configured, so GAIA cannot start the "
            f"sign-in flow. GAIA ships no OAuth credentials — create your own "
            f"client once (free), then register it with GAIA:\n"
            f"{console_steps}\n"
            f"Then register the client and sign in — no Agent UI required:\n"
            f"  gaia connectors configure {pid} --client-id <ID> "
            f"--client-secret <SECRET>\n"
            f"  gaia connectors connect {pid}\n"
            f"  gaia connectors grants grant {pid} <agent-id> --scopes <scope> ...\n"
            f"{example}"
            f"In the Agent UI you can instead use Settings -> Connections -> "
            f"{label}. Full walkthrough: {docs}"
        )


class AuthRequiredError(ConnectorsError):
    """
    A caller cannot use a connection right now and must take a specific action.

    Inspect ``.reason`` to decide what to do; the AgentUI router maps each
    Reason value to a distinct HTTP status, the CLI to a tailored stderr
    message, and the SDK lets callers branch on the enum directly.
    """

    class Reason(str, enum.Enum):
        NOT_CONNECTED = "not_connected"
        AGENT_NOT_GRANTED = "agent_not_granted"
        CONNECTION_MISSING_SCOPES = "connection_missing_scopes"
        REAUTH_REQUIRED = "reauth_required"

    def __init__(
        self,
        reason: "AuthRequiredError.Reason",
        *,
        provider: str = "",
        agent_id: str | None = None,
        missing_scopes: Iterable[str] | None = None,
        message: str | None = None,
    ):
        self.reason = reason
        self.provider = provider
        self.agent_id = agent_id
        self.missing_scopes = list(missing_scopes or [])
        super().__init__(message or self._default_message())

    def _default_message(self) -> str:
        prov = self.provider or "the connection"
        if self.reason is AuthRequiredError.Reason.NOT_CONNECTED:
            return (
                f"No {prov} connection. Connect via Settings → Connections in "
                "AgentUI, or run `gaia connectors connect "
                f"{self.provider or '<provider>'}`. "
                "See docs/sdk/infrastructure/connections.mdx."
            )
        if self.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED:
            agent = self.agent_id or "this agent"
            return (
                f"Agent '{agent}' has no grant for {prov}. Grant the required "
                "scopes in Settings → Connections, or run "
                f"`gaia connectors grants grant {self.provider or '<provider>'} "
                f"{agent} --scopes <scope> ...`. "
                "See docs/sdk/infrastructure/connections.mdx."
            )
        if self.reason is AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES:
            scopes = ", ".join(self.missing_scopes) or "<unknown>"
            return (
                f"The {prov} connection lacks required scopes ({scopes}). "
                "Reconnect with the missing scopes from Settings → Connections, "
                f"or run `gaia connectors connect {self.provider or '<provider>'} "
                "--scopes <scope> ...`. "
                "See docs/sdk/infrastructure/connections.mdx."
            )
        if self.reason is AuthRequiredError.Reason.REAUTH_REQUIRED:
            return (
                f"The stored {prov} credentials are no longer valid (client "
                "rotation or remote revocation). Reconnect from Settings → "
                f"Connections, or run `gaia connectors connect "
                f"{self.provider or '<provider>'}`. "
                "See docs/runbooks/google-oauth-client.md."
            )
        # Fallback — should be unreachable since Reason is a closed enum.
        return f"Authentication required for {prov} (reason={self.reason.value})."


class ConnectionRevokedError(ConnectorsError):
    """OAuth grant was revoked or rotated remotely; caller must reconnect."""

    def __init__(self, provider: str, *, message: str | None = None):
        self.provider = provider
        super().__init__(
            message
            or (
                f"The {provider} connection was revoked or its refresh token "
                "is no longer accepted by the provider. Reconnect from "
                f"Settings → Connections, or run `gaia connectors connect "
                f"{provider}`. See docs/security/connections.mdx."
            )
        )


class ScopeMismatchError(ConnectorsError):
    """Stored connection lacks scopes required by the request."""

    def __init__(
        self,
        *,
        required: Iterable[str],
        granted: Iterable[str],
        provider: str = "",
        message: str | None = None,
    ):
        self.required = list(required)
        self.granted = list(granted)
        self.provider = provider
        super().__init__(message or self._default_message())

    @property
    def missing_scopes(self) -> list[str]:
        return sorted(set(self.required) - set(self.granted))

    def _default_message(self) -> str:
        prov = self.provider or "connection"
        missing = ", ".join(self.missing_scopes) or "<none>"
        return (
            f"The {prov} stored connection is missing required scopes "
            f"({missing}). Reconnect with the missing scopes via Settings → "
            f"Connections, or run `gaia connectors connect "
            f"{self.provider or '<provider>'} --scopes <scope> ...`. "
            "See docs/sdk/infrastructure/connections.mdx."
        )


class ConsentDeniedError(ConnectorsError):
    """User denied consent in OAuth flow (``?error=access_denied``)."""


class FlowTimeoutError(ConnectorsError):
    """OAuth flow exceeded its 120-second callback timeout."""


class FlowInProgressError(ConnectorsError):
    """Another OAuth flow is already pending; only one at a time is supported."""
