# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
gaia.connectors — OAuth-bound external API access for any GAIA caller.

This package implements OAuth 2.0 PKCE for desktop apps (RFC 7636/8252) with
refresh tokens stored in the OS keychain (macOS Keychain, Windows DPAPI, Linux
SecretService) and per-agent grants in ``~/.gaia/connectors/grants.json``.

The module is **self-contained**: SDK, CLI, and AgentUI are equal callers.
Nothing about the OAuth flow, keyring storage, grants ledger, or token-fetch
path requires the AgentUI FastAPI server to be running. Any Python process
running as the user can drive the full flow.

Scope assumption: the in-memory token cache is process-local. Two GAIA
processes running concurrently (e.g. ``gaia chat --ui`` and ``gaia connectors
status``) each maintain their own cache and share the keyring; if both refresh
concurrently and the provider rotates the refresh token, one process may
observe ``invalid_grant`` and reconnect transparently. See
``docs/security/connections.mdx`` for the cross-process race discussion.

The internal modules (``tokens``, ``flow``, ``store``, ``grants``, ``pkce``,
``context``, ``events``) are NOT part of the public surface and may change
without notice. Only the names re-exported here are stable.
"""

from __future__ import annotations

# Read-only contextvar accessor — public by design; agents and tools may
# read the current agent identity but cannot set it. The setter
# (``_agent_context``) is intentionally NOT re-exported.
from gaia.connectors.context import current_agent_id

# Error types — caught by router/CLI/SDK consumers.
from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectorsError,
    ConsentDeniedError,
    FlowInProgressError,
    FlowTimeoutError,
    ScopeMismatchError,
)

# Event-emitter Protocol — re-exported so the FastAPI router can wire its
# implementation into ``set_emitter`` at app startup.
from gaia.connectors.events import EventEmitter, set_emitter

# Provider abstraction — agents declare REQUIRED_CONNECTORS using the
# frozen ConnectorRequirement dataclass; the OAuthProvider Protocol is
# what custom provider implementations satisfy.
from gaia.connectors.providers.base import (
    ConnectorRequirement,
    OAuthProvider,
)

# Spec types + registry — added in T-1 (ConnectorSpec, ConfigField, REGISTRY).
from gaia.connectors.registry import REGISTRY, ConnectorRegistry
from gaia.connectors.spec import ConfigField, ConnectorSpec

# Deferred API names — require ``keyring`` transitively (api→flow→store→keyring).
# Imported lazily via __getattr__ so that ``import gaia.connectors`` does NOT
# pull in keyring at package-load time. This allows ``gaia eval --help`` and
# other subcommands to work on environments where keyring is not installed.
_API_NAMES: frozenset[str] = frozenset(
    {
        "activate",
        "activate_agent",
        "cancel_flow",
        "complete_authorization",
        "deactivate",
        "deactivate_agent",
        "get_access_token",
        "get_access_token_sync",
        "get_connection",
        "grant_agent",
        "import_forwarded_connection",
        "is_agent_active",
        "list_agent_activations",
        "list_agent_grants",
        "list_connections",
        "load_activations",
        "load_grants",
        "poll_device_flow",
        "revoke_agent_grant",
        "revoke_connection",
        "start_authorization",
        "start_device_flow",
        "tripwire_check",
    }
)


def __getattr__(name: str):  # pylint: disable=invalid-name
    if name in _API_NAMES:
        import importlib

        _api = importlib.import_module("gaia.connectors.api")
        return getattr(_api, name)
    raise AttributeError(f"module 'gaia.connectors' has no attribute {name!r}")


__all__ = [
    # Spec types + registry (T-1)
    "ConfigField",
    "ConnectorRegistry",
    "ConnectorSpec",
    "REGISTRY",
    # Errors
    "AuthRequiredError",
    "ConfigurationError",
    "ConnectionRevokedError",
    "ConnectorsError",
    "ConsentDeniedError",
    "FlowInProgressError",
    "FlowTimeoutError",
    "ScopeMismatchError",
    # Provider abstraction
    "ConnectorRequirement",
    "OAuthProvider",
    # current_agent_id is eagerly imported above; the OAuth API functions
    # (cancel_flow, get_access_token, etc.) are available via explicit import
    # ``from gaia.connectors import <name>`` but are omitted from __all__
    # because they are provided lazily via __getattr__ and Pylint's static
    # analysis would flag them as undefined-all-variable (E0603).
    "current_agent_id",
    # Event-emitter Protocol (router wires its impl)
    "EventEmitter",
    "set_emitter",
]
