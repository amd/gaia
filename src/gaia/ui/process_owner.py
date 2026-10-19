# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which server process owns a row of work in progress, and whether it is gone.

Rows that say "this is running" (a chat turn, a document being indexed) carry
the owner that wrote them, so any server sharing the database can tell work
that is still running elsewhere from work whose process has ended.
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

import psutil

#: This process. With the pid it tells a server from an earlier process that
#: had the same pid; tests patch it to stand for a new server.
_TOKEN = secrets.token_urlsafe(8)
#: Two reads of one process's start time can differ (Linux derives it from
#: the boot time, which moves with clock updates); a reused pid starts later.
_START_TOLERANCE = 1.0


def _started(pid: int) -> Optional[float]:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def current_owner() -> str:
    """This process, as stored in an ``owner`` column."""
    pid = os.getpid()
    return f"{pid}:{_started(pid)}:{_TOKEN}"


def owner_is_gone(owner: Optional[str]) -> bool:
    """True when *owner* is not a process that could still be doing the work.

    A row with no owner predates owners and cannot be running. A process that
    exists but cannot be inspected counts as alive: wrongly leaving a row for
    later is harmless, wrongly closing a live turn is not.
    """
    if not owner:
        return True
    try:
        pid_text, started_text, token = owner.split(":", 2)
        pid = int(pid_text)
    except ValueError:
        return True
    if token == _TOKEN:
        return False
    if pid == os.getpid():
        # Our pid but not our token: a process before us that had this pid.
        return True
    try:
        recorded = float(started_text)
    except ValueError:
        return True
    try:
        started = psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return True
    except psutil.AccessDenied:
        return False
    return abs(started - recorded) > _START_TOLERANCE
