# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship sidecar's binding of the shared caller-auth layer.

The mechanism lives in :mod:`gaia.sidecar.caller_auth` and is shared with the
email sidecar so the two cannot drift. This module supplies only what is
specific to this agent: which env vars carry the token, and which paths skip it.

This sidecar is the one that most needs the layer — it exposes shell and file
tools and a bypass-permissions mode, so an unauthenticated loopback port lets
any web page the user visits drive it (see the shared module for the
DNS-rebinding and drive-by vectors).
"""

from __future__ import annotations

from typing import FrozenSet

from gaia.sidecar.caller_auth import (  # noqa: F401  (re-exported)
    CallerAuthConfig,
    HostOriginMiddleware,
    config_from_env,
    configure,
    generate_session_token,
    get_config,
    is_exempt_path,
    reset,
    token_ok,
)

# Preferred channel: the spawning parent writes the token to a 0600, owner-only
# file and passes its PATH — the secret never sits in the environment. MUST
# equal the daemon's mirrored literals in gaia.daemon.sidecars.spec
# (_GAIA_TOKEN_FILE_ENV_VAR / _GAIA_TOKEN_ENV_VAR), which are kept as plain
# strings there so core never imports this wheel.
TOKEN_FILE_ENV_VAR = "GAIA_GAIA_SIDECAR_TOKEN_FILE"

# Legacy channel: the token directly in the environment.
TOKEN_ENV_VAR = "GAIA_GAIA_SIDECAR_TOKEN"

SURFACE = "GAIA agent sidecar"

# Paths that never require a token: the liveness and version probes a host polls
# during the attach handshake, before any query is in play. None of them expose
# user data or accept work. Host/Origin controls still apply to them.
EXEMPT_PATHS: FrozenSet[str] = frozenset(
    {
        "/health",
        "/version",
        "/v1/gaia/version",
    }
)


def config_from_environment() -> CallerAuthConfig:
    """Build this sidecar's policy from the environment."""
    return config_from_env(
        token_file_env_var=TOKEN_FILE_ENV_VAR,
        token_env_var=TOKEN_ENV_VAR,
        surface=SURFACE,
        exempt_paths=EXEMPT_PATHS,
    )


__all__ = [
    "TOKEN_ENV_VAR",
    "TOKEN_FILE_ENV_VAR",
    "SURFACE",
    "EXEMPT_PATHS",
    "CallerAuthConfig",
    "HostOriginMiddleware",
    "config_from_environment",
    "configure",
    "generate_session_token",
    "get_config",
    "is_exempt_path",
    "reset",
    "token_ok",
]
