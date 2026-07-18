# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Constants for the ``/host/v1/*`` custody API (design §0.31).

This API is versioned **independently** of the client-facing ``/daemon/v1``
surface (``gaia.daemon.constants.DAEMON_API_VERSION``): it is the sidecar↔daemon
reverse contract and carries its own MAJOR, negotiated on that leg (§0.31, §0.15
evolution rules). A daemon that speaks a different custody MAJOR than a sidecar
was built against is a breaking contract change — surfaced loudly, never a silent
skew.
"""

from __future__ import annotations

# Route prefix for the custody reverse contract.
HOST_API_PREFIX = "/host/v1"

# MAJOR.MINOR contract version of the sidecar↔daemon custody boundary. Own
# MAJOR, independent of DAEMON_API_VERSION (§0.31). v1.0: rag/query, memory
# GET/POST, sessions/{id}, audit append (plain append-only — hash-chain deferred
# per §0.35.5).
HOST_API_VERSION = "1.0"

# Custody auth uses the same header/scheme shape as the client API so callers
# have one bearer-token convention to implement.
CUSTODY_AUTH_SCHEME = "Bearer"

# Private env channel (daemon → sidecar, at spawn) carrying the delegated-custody
# wiring. Presence of the URL is what selects the Delegated provider (§0.37);
# absence selects Embedded. The secret is the per-spawn credential bound to the
# agent id at mint (§0.11) — it never travels through a client.
CUSTODY_URL_ENV_VAR = "GAIA_HOST_CUSTODY_URL"
CUSTODY_SECRET_ENV_VAR = "GAIA_HOST_CUSTODY_SECRET"

# Explicit opt-in to the stateless Ephemeral provider (§0.37): nothing persists,
# the caller passes context per request. Set to "1"/"true" to force it even when
# a custody URL is present.
CUSTODY_EPHEMERAL_ENV_VAR = "GAIA_CUSTODY_EPHEMERAL"

# Memory scopes. "agent" is the agent-private default; "user" is the shared,
# cross-agent user scope, reachable only when the agent's manifest declares the
# sharedScopes grant (§0.28/§0.11). Grant enforcement lands with the manifest
# work (V2-3); v1 accepts the scope literal and stores it, defaulting to "agent".
MEMORY_SCOPE_AGENT = "agent"
MEMORY_SCOPE_USER = "user"
VALID_MEMORY_SCOPES = (MEMORY_SCOPE_AGENT, MEMORY_SCOPE_USER)
