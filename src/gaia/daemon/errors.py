# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Loud, actionable daemon errors (no silent fallbacks — CLAUDE.md).

Each message names what failed, what to do, and where to look.
"""

from __future__ import annotations


class DaemonError(Exception):
    """Base for all daemon lifecycle/client failures."""


class DaemonLockError(DaemonError):
    """The single-instance start lock could not be acquired.

    Raised when another process holds the start lock longer than the timeout —
    surfaced loudly instead of racing to spawn a rival daemon.
    """


class DaemonStartError(DaemonError):
    """The daemon subprocess could not be launched or never became healthy."""


class DaemonVersionError(DaemonError):
    """The running daemon speaks a different MAJOR host-API version than this client.

    An app update replaced the client while the old daemon kept running (§0.25).
    Restart the daemon so the new client can attach — never silently talk a stale
    contract.
    """
