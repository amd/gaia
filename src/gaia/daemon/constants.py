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

# MAJOR contract version of the UI/CLI <-> daemon boundary (design §0.25 skew
# rule). An app update replaces the client while an old daemon keeps running; a
# differing MAJOR means the client cannot speak the running daemon's API and must
# restart it rather than silently attach to a stale host.
DAEMON_API_VERSION = "1"

# Client-token auth: header name and scheme.
AUTH_SCHEME = "Bearer"

# Route prefix for the versioned client API surface.
API_PREFIX = "/daemon/v1"
