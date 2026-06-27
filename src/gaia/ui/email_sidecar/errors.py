# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Loud, actionable error types for the email sidecar (no silent fallbacks)."""


class SidecarError(Exception):
    """Base for all email-sidecar failures."""


class PlatformError(SidecarError):
    """Unsupported platform, unreadable/invalid lock, or placeholder entry."""


class IntegrityError(SidecarError):
    """A downloaded binary's SHA-256 did not match binaries.lock.json.

    The security boundary: a tampered/truncated download is rejected before it
    can ever be spawned. There is no 'use it anyway' path.
    """


class BinaryNotFoundError(SidecarError):
    """The frozen binary is not present where it was expected."""


class HealthTimeoutError(SidecarError):
    """The sidecar did not report healthy within the health-poll deadline."""


class SidecarSpawnError(SidecarError):
    """The sidecar process could not be launched (dev env missing, port in use)."""


class RouteNotAvailableError(SidecarError):
    """A UI capability whose REST route does not exist on the sidecar yet."""
