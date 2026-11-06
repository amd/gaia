# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
ConnectorSpec and ConfigField — typed manifest for a GAIA connector.

Every connector in the catalog is described by a frozen ``ConnectorSpec``.
The spec drives both the UI (tile grid, detail view, configure body) and the
handler dispatch (`get_credential`, `configure`, `disconnect`, `test`).

Only two connector types are implemented in v1 (plan amendment A1):
- ``oauth_pkce``  — OAuth 2.0 PKCE flow (e.g. Google)
- ``mcp_server``  — stdio / SSE MCP server with env-block configuration

Fields that belong only to one type are ``None`` / empty on the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# v1 connector types only (per plan amendment A1)
ConnectorType = Literal["oauth_pkce", "mcp_server"]

_VALID_KINDS = frozenset(
    {"text", "secret", "url", "email", "select", "bool", "textarea"}
)
_VALID_TYPES: frozenset[str] = frozenset({"oauth_pkce", "mcp_server"})

# The implicit scope an ``mcp_server`` connector carries. MCP servers have no
# OAuth-style scope list — holding the credential IS the capability — so the
# grant ledger records a single "may use this server" scope for them. Agents
# declare it as ``ConnectorRequirement(connector_id="mcp-…", scopes=("use",))``.
MCP_SERVER_SCOPES: tuple[str, ...] = ("use",)


@dataclass(frozen=True)
class ConfigField:
    """
    A single field in a connector's configure form.

    ``secret=True`` means the value is stored in the OS keyring, not in
    ``mcp_servers.json``. The UI renders it as a password input and
    never shows the stored value after first save.
    """

    key: str
    label: str
    kind: Literal["text", "secret", "url", "email", "select", "bool", "textarea"]
    required: bool = True
    placeholder: str = ""
    help_md: str = ""
    options: tuple[str, ...] | None = None
    secret: bool = False

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise ValueError("ConfigField.key must not be empty")
        if self.kind not in _VALID_KINDS:
            raise ValueError(
                f"ConfigField.kind {self.kind!r} is not one of {sorted(_VALID_KINDS)}"
            )
        if self.options is not None:
            object.__setattr__(self, "options", tuple(self.options))


@dataclass(frozen=True)
class ConnectorSpec:
    """
    Immutable manifest for a single connector in the GAIA catalog.

    ``id`` is the stable registry key — it becomes the ``connector_id`` in
    every storage path, grant entry, and API URL. Do not change it after
    publishing; create a new spec instead.

    Fields prefixed ``mcp_`` are used only for ``type="mcp_server"``.
    Fields prefixed ``default_scopes`` / ``available_scopes`` /
    ``oauth_provider_ref`` are used only for ``type="oauth_pkce"``.
    """

    id: str
    display_name: str
    icon: str
    category: str
    tier: int
    type: ConnectorType
    description: str
    instructions_md: str = ""
    config_schema: tuple[ConfigField, ...] = field(default_factory=tuple)
    test_endpoint: str | None = None
    product_url: str | None = None
    # GAIA documentation URL the AgentUI's "Learn more" link points at.
    # Should walk users through obtaining client credentials, API tokens,
    # or whatever else the connector needs. Falls back to ``product_url``
    # in the UI when ``None``, but every connector should set it.
    docs_url: str | None = None
    # oauth_pkce only
    default_scopes: tuple[str, ...] = field(default_factory=tuple)
    available_scopes: tuple[str, ...] = field(default_factory=tuple)
    oauth_provider_ref: str | None = None
    # oauth_pkce only: the OAuth authority segment this connector defaults to
    # (e.g. "consumers" / "organizations" for Microsoft). Per-connector spec
    # data, not an env var — see plan amendment D2/#2628. None for providers
    # with a single fixed authority (e.g. Google).
    oauth_tenant: str | None = None
    # oauth_pkce only: which provider CLASS implements this spec. Distinct
    # from ``oauth_provider_ref`` (the per-connector credential/token STORAGE
    # key) — two specs can share one ``oauth_impl`` while keeping separate
    # storage, which is exactly how Microsoft Personal / Work-School share
    # ``MicrosoftOAuthProvider`` but never share a keyring slot. Defaults to
    # ``id`` when unset (single-audience providers like Google).
    oauth_impl: str | None = None
    # OAuth-app credentials the user pastes in once during first-time
    # setup (e.g. Google Cloud Console "Desktop client" client_id +
    # client_secret). Empty tuple = no setup form required (provider is
    # pre-configured at deploy time). Distinct from ``config_schema``,
    # which is reserved for connection-time fields like API keys for
    # MCP servers — those persist as the connection itself, while OAuth
    # setup fields persist as *provider* credentials reused across many
    # connect/disconnect cycles.
    oauth_setup_fields: tuple[ConfigField, ...] = field(default_factory=tuple)
    # oauth_pkce only: the provider also supports the RFC 8628 device-code flow
    # (a short code entered at a URL — no loopback redirect / no per-user app
    # registration). Surfaced to the UI so it can offer a "sign in with a code"
    # path alongside the browser button. Default False — the provider must
    # implement the device-code endpoints (e.g. Microsoft).
    supports_device_code: bool = False
    # mcp_server only
    mcp_command: str | None = None
    mcp_args: tuple[str, ...] = field(default_factory=tuple)
    # Secret config keys, stored in the keyring. A stdio server gets them as
    # env vars; an http server gets them through ``mcp_headers``.
    mcp_env_keys: tuple[str, ...] = field(default_factory=tuple)
    # mcp_server, remote ("http") only. The user enters a base URL in the
    # ``mcp_url_field`` config field; GAIA appends ``mcp_url_path`` itself, so
    # the endpoint (and what it can reach) is pinned by the catalog.
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_url_field: str | None = None
    mcp_url_path: str = ""
    # (header, secret config key, template) — e.g.
    # ("Authorization", "HA_TOKEN", "Bearer {}").
    mcp_headers: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)
    # Messages for a failed live test, keyed "unauthorized", "not_found",
    # "unreachable"; ``{url}`` is replaced with the base URL.
    mcp_http_hints: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # The server's state-changing tools may only be approved by a person:
    # never by full access, auto-approve, a schedule or a messaging chat.
    mcp_writes_need_person: bool = False
    # Appended to the description of each of the server's state-changing
    # tools: how to call them well, for servers whose own wording falls short.
    mcp_tool_note: str = ""

    def scope_ceiling(self) -> tuple[str, ...]:
        """Every scope a grant for this connector is allowed to carry.

        ``available_scopes`` is the ceiling whenever a spec declares one — the
        same list ``flow._reject_scopes_outside_catalog`` enforces on the OAuth
        authorize path. An ``mcp_server`` spec that declares none falls back to
        the implicit ``MCP_SERVER_SCOPES`` rather than to a deny-all empty
        tuple, so adding an MCP connector to the catalog cannot accidentally
        ship a connector nobody can be granted. An ``oauth_pkce`` spec with no
        ``available_scopes`` IS deny-all: #3247 already requires OAuth entries
        to publish their list, and guessing one would defeat the ceiling.
        """
        if self.available_scopes:
            return self.available_scopes
        if self.type == "mcp_server":
            return MCP_SERVER_SCOPES
        return ()

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("ConnectorSpec.id must not be empty")
        if self.type not in _VALID_TYPES:
            raise ValueError(
                f"ConnectorSpec.type {self.type!r} is not one of {sorted(_VALID_TYPES)}"
            )
        if self.tier < 0:
            raise ValueError(f"ConnectorSpec.tier must be >= 0, got {self.tier}")
        # Normalise all sequence fields to tuples so equality is predictable.
        object.__setattr__(self, "config_schema", tuple(self.config_schema))
        object.__setattr__(self, "default_scopes", tuple(self.default_scopes))
        object.__setattr__(self, "available_scopes", tuple(self.available_scopes))
        object.__setattr__(self, "oauth_setup_fields", tuple(self.oauth_setup_fields))
        object.__setattr__(self, "mcp_args", tuple(self.mcp_args))
        object.__setattr__(self, "mcp_env_keys", tuple(self.mcp_env_keys))
        object.__setattr__(
            self, "mcp_headers", tuple(tuple(h) for h in self.mcp_headers)
        )
        object.__setattr__(
            self, "mcp_http_hints", tuple(tuple(h) for h in self.mcp_http_hints)
        )
        if self.mcp_transport == "http":
            if not self.mcp_url_field or not self.mcp_url_path.startswith("/"):
                raise ValueError(
                    f"ConnectorSpec {self.id!r}: an http MCP server needs "
                    f"mcp_url_field and an mcp_url_path starting with '/'"
                )
            if self.mcp_command:
                raise ValueError(
                    f"ConnectorSpec {self.id!r}: an http MCP server has no "
                    f"mcp_command"
                )
            unknown = [k for _, k, _ in self.mcp_headers if k not in self.mcp_env_keys]
            if unknown:
                raise ValueError(
                    f"ConnectorSpec {self.id!r}: mcp_headers name {unknown!r}, "
                    f"which are not in mcp_env_keys"
                )
