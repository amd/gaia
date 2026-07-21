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
    mcp_env_keys: tuple[str, ...] = field(default_factory=tuple)

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
