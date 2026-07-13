# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA headless custody daemon (Agent UI v2, Phase 1 skeleton).

The daemon is the always-on machine-wide custody/supervisor process. This package
is the SKELETON: single-instance identity, client-token auth, and the
``gaia daemon`` lifecycle CLI. Sidecar supervision, ``/host/v1/*`` custody, the
model broker, and the scheduler clock each land in their own later issue and are
deliberately NOT implemented here.

Public surface used by clients (the web UI and the ``gaia <agent>`` CLIs):

    from gaia.daemon import start_or_attach, read_instance, DaemonInstance

``start_or_attach()`` starts a daemon or attaches to the one already running —
two concurrent callers yield exactly one daemon.
"""

from __future__ import annotations

from gaia.daemon.constants import DAEMON_API_VERSION, SERVICE_ID
from gaia.daemon.errors import (
    DaemonError,
    DaemonLockError,
    DaemonStartError,
    DaemonVersionError,
)
from gaia.daemon.instance import (
    DaemonInstance,
    is_live,
    probe,
    read_instance,
    write_instance,
)

__all__ = [
    "DAEMON_API_VERSION",
    "SERVICE_ID",
    "DaemonError",
    "DaemonLockError",
    "DaemonStartError",
    "DaemonVersionError",
    "DaemonInstance",
    "is_live",
    "probe",
    "read_instance",
    "write_instance",
    "start_or_attach",
    "attach",
    "request_shutdown",
]


def __getattr__(name: str):
    # Lazy re-export of client helpers so importing gaia.daemon does not pull in
    # the client/subprocess machinery until a caller actually needs it.
    if name in ("start_or_attach", "attach", "request_shutdown"):
        from gaia.daemon import client

        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
