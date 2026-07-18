# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Daemon-wide constants (identity, versioning, network binding)."""

from __future__ import annotations

# The daemon binds loopback only. NEVER 4001 (repo-wide reserved port).
HOST = "127.0.0.1"
RESERVED_PORT = 4001

# Stable service identifier stamped into instance.json and returned by
# /daemon/v1/status. A client probing the recorded port trusts the process only
# if it answers with THIS id and the recorded pid — a foreign server that grabbed
# the freed port after a crash cannot impersonate the daemon.
SERVICE_ID = "gaia-daemon"

# MAJOR.MINOR contract version of the UI/CLI <-> daemon boundary (design §0.25
# skew rule). An app update replaces the client while an old daemon keeps
# running; a differing MAJOR means the client cannot speak the running daemon's
# API and must restart it rather than silently attach to a stale host. MINOR 1
# added the /daemon/v1/agents control plane (#2142) — clients that need it
# floor-check MINOR >= 1 so a pre-#2142 daemon fails loudly instead of 404ing.
DAEMON_API_VERSION = "1.1"

# Client-token auth: header name and scheme.
AUTH_SCHEME = "Bearer"

# Route prefix for the versioned client API surface.
API_PREFIX = "/daemon/v1"

# Route prefix for the daemon-owned callback API surface (§0.31) — the reverse
# leg sidecars and host-side components call back INTO. V2-11 mounts only the
# model-slot broker's lease route here; V2-12 adds memory/rag/sessions/audit.
# Authenticated by the caller's launch token (sidecar) or the daemon client
# token (host-side), NOT the client-plane token contract on API_PREFIX.
HOST_API_PREFIX = "/host/v1"

# Env channel the daemon uses to advertise the broker to the processes it
# spawns (and host-side components discover via start_or_attach). A caller
# routes model loads through the broker only when GAIA_MODEL_BROKER_URL is set;
# when it is set but the broker is unreachable, the caller fails LOUD rather
# than doing a silent direct load that would race-evict (CLAUDE.md).
BROKER_URL_ENV_VAR = "GAIA_MODEL_BROKER_URL"
BROKER_TOKEN_ENV_VAR = "GAIA_MODEL_BROKER_TOKEN"
# 0600-file delivery of the broker credential (#2149 posture): when set, it
# names a file whose contents are the credential. Preferred over the bare-env
# var so a sidecar's launch secret is never copied into inspectable process env.
BROKER_TOKEN_FILE_ENV_VAR = "GAIA_MODEL_BROKER_TOKEN_FILE"
# Optional per-caller default lease priority ("interactive"|"background").
BROKER_PRIORITY_ENV_VAR = "GAIA_MODEL_LEASE_PRIORITY"
