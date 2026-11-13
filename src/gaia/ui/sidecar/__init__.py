# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Sidecar support for the Agent UI backend (daemon-client side).

Since #2142 the GAIA daemon owns fetch/spawn/health/tree-kill
(:mod:`gaia.daemon.sidecars`); the UI acquires a running sidecar through
:mod:`gaia.ui.sidecar.daemon_client` and keeps only the request-side
pieces here (proxy, relay, profiles, grants).
"""

from gaia.daemon.sidecars.errors import (
    BinaryNotFoundError,
    HealthTimeoutError,
    IntegrityError,
    PlatformError,
    RouteNotAvailableError,
    SidecarError,
    SidecarHTTPError,
    SidecarSpawnError,
    VersionMismatchError,
)
from gaia.ui.sidecar.daemon_client import SidecarHandle
from gaia.ui.sidecar.proxy import SidecarProxy

__all__ = [
    "SidecarError",
    "PlatformError",
    "IntegrityError",
    "BinaryNotFoundError",
    "HealthTimeoutError",
    "SidecarSpawnError",
    "RouteNotAvailableError",
    "SidecarHTTPError",
    "VersionMismatchError",
    "SidecarHandle",
    "SidecarProxy",
]
