# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Backward-compat re-export shim (issue #2142, T1).

The sidecar error hierarchy moved to :mod:`gaia.daemon.sidecars.errors` so the
daemon (which owns sidecar spawning) never has to import ``gaia.ui``. This
module re-exports the same class objects so existing
``from gaia.ui.email_sidecar.errors import ...`` callers (``proxy.py``,
``relay.py``, ``router.py``, and their tests) keep working unmodified. Retired
once those callers move to the daemon client (V2-7).
"""

from __future__ import annotations

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
]
