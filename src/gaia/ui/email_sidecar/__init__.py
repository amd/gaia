# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Node-free email-agent sidecar support for the Agent UI backend.

The Python UI backend fetches (verified download), spawns, health-checks,
proxies to, and tree-kills the email sidecar directly — no Node.js, no npm
package on the runtime path. The npm ``fetch.ts``/``lifecycle.ts`` are the
external Node-integrator channel and the reference for this Python port.
"""

from gaia.ui.email_sidecar.errors import (
    BinaryNotFoundError,
    HealthTimeoutError,
    IntegrityError,
    PlatformError,
    RouteNotAvailableError,
    SidecarError,
    SidecarSpawnError,
)

__all__ = [
    "SidecarError",
    "PlatformError",
    "IntegrityError",
    "BinaryNotFoundError",
    "HealthTimeoutError",
    "SidecarSpawnError",
    "RouteNotAvailableError",
]
