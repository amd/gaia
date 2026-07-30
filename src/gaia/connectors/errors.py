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
        example: str | None = None,
    ):
        self.provider_id = provider_id
        self.provider_label = provider_label
        self.console_steps = console_steps
        super().__init__(
            self._build_message(
                provider_id, provider_label, console_steps, docs, example
            )
        )

    @staticmethod
    def _build_message(
        pid: str,
        label: str,
        console_steps: str,
        docs: str,
        example: str | None,
    ) -> str:
        # Registering the client, authorizing scopes, and granting the agent is
        # two commands: `--grant-agent` folds the grant into `connect` so the
        # scopes can never drift (the mismatch was the #1 setup failure, #2347).
        example_block = f"{example}\n" if example else ""
        env_prefix = f"GAIA_{pid.upper()}"
        return (
            f"{label} OAuth client is not configured, so GAIA cannot start the "
            f"sign-in flow. GAIA ships no OAuth credentials — create your own "
            f"client once (free), then register it with GAIA:\n"
            f"{console_steps}\n"
            f"Then register the client and sign in — no Agent UI required:\n"
            f"  gaia connectors configure {pid} --client-id <ID> "
            f"--client-secret <SECRET>\n"
            f"  gaia connectors connect {pid} --scopes <scope> ... "
            f"--grant-agent <agent-id>\n"
            f"{example_block}"
            f"(Power users / CI can instead set {env_prefix}_CLIENT_ID and "
            f"{env_prefix}_CLIENT_SECRET in the environment before launching "
            f"GAIA.) In the Agent UI you can instead use Settings -> Connections "
            f"-> {label}. Full walkthrough: {docs}"
        )


class MicrosoftTenantConflictError(ConfigurationError):
    """``GAIA_MICROSOFT_TENANT`` disagrees with the tenant a Microsoft
    connector resolves to on its own (plan amendment A2/A3, #2628).

    Subclasses ``ConfigurationError`` (not bare ``ConnectorsError``) —
    ``ConfigurationError`` is what ``api.list_connections``, ``tripwire_check``,
    and the UI router's ``_connector_summary`` already catch per-provider, so
    one misconfigured connector degrades to a warning row instead of taking
    down the whole connections list or sweep.

    Only raised on a genuine CONFLICT: the env var is no longer read for
    tenant resolution at all, so merely setting it (to a value that happens
    to agree with what the connector already resolves to) is a silent no-op
    with a one-time deprecation log, not an error. This fires when the value
    would have changed behaviour, or is a bare tenant GUID that is inherently
    ambiguous between the personal and work/school connectors.
    """

    _OTHER_CONNECTOR = {"microsoft": "microsoft_work", "microsoft_work": "microsoft"}

    def __init__(
        self,
        connector_id: str,
        *,
        env_value: str,
        resolved_tenant: str,
        ambiguous_guid: bool = False,
    ):
        self.connector_id = connector_id
        self.env_value = env_value
        self.resolved_tenant = resolved_tenant
        other = self._OTHER_CONNECTOR.get(connector_id, "the other Microsoft connector")
        if ambiguous_guid:
            what = (
                f"is a single tenant id, which is ambiguous between "
                f"{connector_id!r} and {other!r}"
            )
        else:
            what = (
                f"disagrees with {connector_id!r}, which resolves to "
                f"{resolved_tenant!r} from its own connector definition"
            )
        super().__init__(
            f"GAIA_MICROSOFT_TENANT={env_value!r} {what}. This environment "
            "variable is no longer read for tenant resolution — GAIA now "
            f"resolves the tenant per connector. If you meant {connector_id!r}, "
            f"remove the conflicting value; if you meant {other!r}, use that "
            "connector instead (or set its Directory (tenant) ID setup field "
            "for a single-tenant registration). Then `unset "
            "GAIA_MICROSOFT_TENANT`. See docs/connectors/microsoft.mdx."
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
        # A6: distinct from REAUTH_REQUIRED — the stored connection was
        # minted against a different OAuth tenant than the connector
        # currently resolves to. The remedy differs (use the other
        # Microsoft connector, not "just reconnect this one"), so callers
        # must be able to branch on it separately.
        TENANT_MISMATCH = "tenant_mismatch"

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
        if self.reason is AuthRequiredError.Reason.TENANT_MISMATCH:
            return (
                f"The stored {prov} connection was authenticated against a "
                "different Microsoft tenant than the connector currently "
                "resolves to. Reconnect from Settings → Connections, or run "
                f"`gaia connectors connect {self.provider or '<provider>'}`. "
                "See docs/connectors/microsoft.mdx."
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


class OAuthProviderError(ConnectorsError):
    """A provider's OAuth endpoint rejected a request, with a structured reason.

    Replaces raising ``ConnectorsError`` with the entire unbounded
    ``response.text`` interpolated into the message (the literal #2590 bug):
    ``error`` / ``error_description`` are the RFC 6749 / AADSTS fields the
    provider actually returned, each truncated so a malformed or oversized
    body can never reach model context unbounded — see
    ``flow.classify_oauth_exception``, which is the only code meant to
    inspect these fields programmatically.
    """

    _MAX_FIELD_LEN = 300

    def __init__(
        self,
        provider: str,
        *,
        error: str = "",
        error_description: str = "",
        status_code: int | None = None,
    ):
        self.provider = provider
        self.error = (error or "")[: self._MAX_FIELD_LEN]
        self.error_description = (error_description or "")[: self._MAX_FIELD_LEN]
        self.status_code = status_code
        detail = self.error_description or self.error or "no error detail returned"
        status = f" ({status_code})" if status_code else ""
        super().__init__(f"{provider} OAuth request was rejected{status}: {detail}")


class UnknownAgentError(ConnectorsError):
    """One or more requested agent ids are not registered in the agent registry.

    Distinct from ``gaia.daemon.sidecars.errors.UnknownAgentError`` (a
    ``SidecarError``, unrelated hierarchy) — this one IS a ``ConnectorsError``
    so the CLI's blanket ``except ConnectorsError`` and the router's
    ``ConnectorsError -> HTTPException`` mapping both catch it uniformly.
    """

    def __init__(self, agent_ids: list[str]):
        self.agent_ids = list(agent_ids)
        super().__init__(
            f"Unknown agent id(s): {', '.join(self.agent_ids)}. Check the "
            "namespaced agent id (e.g. 'installed:email') via `gaia connectors "
            "grants list` or the Agent UI's agent picker."
        )


class NoDeclaredScopesError(ConnectorsError):
    """An agent declares no ``REQUIRED_CONNECTORS`` scopes for a connector."""

    def __init__(self, agent_id: str, connector_id: str):
        self.agent_id = agent_id
        self.connector_id = connector_id
        super().__init__(
            f"Agent {agent_id!r} declares no REQUIRED_CONNECTORS scopes for "
            f"connector {connector_id!r}, so there is nothing to authorize or "
            "grant. Pass --scopes explicitly, or check the agent's "
            "REQUIRED_CONNECTORS declaration."
        )


class ScopeNotAllowedError(ConnectorsError):
    """A declared scope is outside the connector's ``available_scopes`` ceiling.

    Raised by ``resolve_declared_scopes`` so an agent's own declaration can
    never put a scope in front of the user's consent screen that the catalog
    entry does not explicitly allow (#2603) — a half-fix would validate agent
    identity but not scope values.
    """

    def __init__(self, agent_id: str, connector_id: str, scopes: list[str]):
        self.agent_id = agent_id
        self.connector_id = connector_id
        self.scopes = list(scopes)
        super().__init__(
            f"Agent {agent_id!r} declares scope(s) {', '.join(self.scopes)} for "
            f"connector {connector_id!r} that are outside its available_scopes "
            "catalog entry. This is a catalog/agent mismatch — file a bug "
            "rather than widening the request."
        )


class GrantAfterConnectError(ConnectorsError):
    """A connection was persisted but the agent grant that should follow it failed.

    Deliberately its own type — never merged into a generic ``ConnectorsError``
    — so a caller that must not echo an arbitrary exception's text (it might
    carry a credential) can still tell apart "the connection itself is fine,
    only the grant write failed" from every other failure, and report that
    honestly instead of a blanket "nothing was changed." See
    ``onboarding_tools._setup_mailbox_access``.
    """

    def __init__(self, provider: str, agent_id: str, *, reason: str):
        self.provider = provider
        self.agent_id = agent_id
        self.reason = reason
        super().__init__(
            f"Connected {provider!r} but failed to grant it to agent "
            f"{agent_id!r}: {reason}."
        )
