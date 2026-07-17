# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Loud, actionable error types for daemon-supervised sidecar agents (no
silent fallbacks).

Relocated verbatim from ``gaia.ui.email_sidecar.errors`` (issue #2142, T1) —
``gaia.ui.email_sidecar.errors`` is now a pure re-export shim over this
module so existing importers keep working unchanged.
"""

from __future__ import annotations


class SidecarError(Exception):
    """Base for all sidecar-agent failures."""


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


class SidecarHTTPError(SidecarError):
    """The sidecar answered with a non-2xx status.

    Carries the status code and the sidecar's own actionable ``detail`` message
    (e.g. ``502 local LLM triage failed: Lemonade not reachable``) so the loud,
    fixable error the sidecar produced is preserved instead of being flattened
    into a generic ``HTTPError``.
    """

    def __init__(self, status_code: int, detail: str, *, path: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.path = path
        where = f" from {path}" if path else ""
        super().__init__(f"email sidecar returned HTTP {status_code}{where}: {detail}")


class VersionMismatchError(SidecarError):
    """The running sidecar speaks a different MAJOR contract version than expected."""


class UnknownAgentError(SidecarError):
    """The requested agent_id is not in the daemon's registered specs."""


class ModeConflictError(SidecarError):
    """The sidecar is running in a different mode than the ensure requested."""


class CapacityError(SidecarError):
    """Starting another sidecar would exceed the live-sidecar cap."""


class StopFailedError(SidecarError):
    """The sidecar process survived a tree-kill — it is still alive."""
