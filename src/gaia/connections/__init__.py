# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
gaia.connections — OAuth-bound external API access for any GAIA caller.

This package implements OAuth 2.0 PKCE for desktop apps (RFC 7636/8252) with
refresh tokens stored in the OS keychain (macOS Keychain, Windows DPAPI, Linux
SecretService) and per-agent grants in ``~/.gaia/connections/grants.json``.

The module is **self-contained**: SDK, CLI, and AgentUI are equal callers.
Nothing about the OAuth flow, keyring storage, grants ledger, or token-fetch
path requires the AgentUI FastAPI server to be running. Any Python process
running as the user can drive the full flow.

Scope assumption: the in-memory token cache is process-local. Two GAIA
processes running concurrently (e.g. ``gaia chat --ui`` and ``gaia connections
status``) each maintain their own cache and share the keyring; if both refresh
concurrently and the provider rotates the refresh token, one process may
observe ``invalid_grant`` and reconnect transparently. See
``docs/security/connections.mdx`` for the cross-process race discussion.

The internal modules (``tokens``, ``flow``, ``store``, ``grants``, ``pkce``,
``context``, ``events``) are NOT part of the public surface and may change
without notice. Only the names re-exported here are stable.
"""

from __future__ import annotations

# Public API — coordination layer.
from gaia.connections.api import (
    cancel_flow,
    complete_authorization,
    get_access_token,
    get_access_token_sync,
    get_connection,
    grant_agent,
    list_agent_grants,
    list_connections,
    load_grants,
    revoke_agent_grant,
    revoke_connection,
    start_authorization,
    tripwire_check,
)

# Read-only contextvar accessor — public by design; agents and tools may
# read the current agent identity but cannot set it. The setter
# (``_agent_context``) is intentionally NOT re-exported.
from gaia.connections.context import current_agent_id

# Error types — caught by router/CLI/SDK consumers.
from gaia.connections.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectionsError,
    ConsentDeniedError,
    FlowInProgressError,
    FlowTimeoutError,
    ScopeMismatchError,
)

# Event-emitter Protocol — re-exported so the FastAPI router can wire its
# implementation into ``set_emitter`` at app startup.
from gaia.connections.events import EventEmitter, set_emitter

# Provider abstraction — agents declare REQUIRED_CONNECTIONS using the
# frozen ConnectionRequirement dataclass; the OAuthProvider Protocol is
# what custom provider implementations satisfy.
from gaia.connections.providers.base import (
    ConnectionRequirement,
    OAuthProvider,
)

__all__ = [
    # Errors
    "AuthRequiredError",
    "ConfigurationError",
    "ConnectionRevokedError",
    "ConnectionsError",
    "ConsentDeniedError",
    "FlowInProgressError",
    "FlowTimeoutError",
    "ScopeMismatchError",
    # Provider abstraction
    "ConnectionRequirement",
    "OAuthProvider",
    # Public API
    "cancel_flow",
    "complete_authorization",
    "current_agent_id",
    "get_access_token",
    "get_access_token_sync",
    "get_connection",
    "grant_agent",
    "list_agent_grants",
    "list_connections",
    "load_grants",
    "revoke_agent_grant",
    "revoke_connection",
    "start_authorization",
    "tripwire_check",
    # Event-emitter Protocol (router wires its impl)
    "EventEmitter",
    "set_emitter",
]
