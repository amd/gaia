# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which server process owns a row of work in progress, and whether it is gone.

Rows that say "this is running" (a chat turn, a document being indexed) carry
the owner that wrote them, so any server sharing the database can tell work
that is still running elsewhere from work whose process has ended.

An owner is ``pid:start:token``. *start* tells a process from a later one that
reused its pid:

* Linux: the boot id plus the start time in clock ticks since boot, read from
  ``/proc``. Both are exact and never move, unlike ``create_time()``, which
  Linux derives from the wall-clock boot time and so shifts when NTP steps
  the clock.
* Elsewhere: ``create_time()``, which Windows and macOS keep as recorded at
  creation, compared with a small tolerance for float rounding.
"""

from __future__ import annotations

import functools
import os
import secrets
import sys
from typing import Optional

import psutil

#: This process. With the pid it tells a server from an earlier process that
#: had the same pid; tests patch it to stand for a new server.
_TOKEN = secrets.token_urlsafe(8)
#: Float rounding between two reads of one process's ``create_time()``.
_START_TOLERANCE = 0.01

_LINUX = sys.platform.startswith("linux")


@functools.lru_cache(maxsize=1)
def _boot_id() -> str:
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
        return f.read().strip()


def _linux_start(pid: int) -> Optional[str]:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            stat = f.read()
    except FileNotFoundError:
        return None
    # The command name is parenthesized and may hold spaces; field 22 is the
    # start time, the 20th after the closing parenthesis.
    starttime = stat[stat.rindex(")") + 2 :].split()[19]
    return f"{_boot_id()}/{starttime}"


def _started(pid: int) -> Optional[str]:
    """*pid*'s start, as stored in an owner, or None when it is not running."""
    if _LINUX:
        return _linux_start(pid)
    try:
        return repr(psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


@functools.lru_cache(maxsize=1)
def _own_start() -> str:
    started = _started(os.getpid())
    if started is None:
        raise RuntimeError("Could not read this process's own start time")
    return started


def current_owner() -> str:
    """This process, as stored in an ``owner`` column."""
    return f"{os.getpid()}:{_own_start()}:{_TOKEN}"


def owner_is_gone(owner: Optional[str]) -> bool:
    """True when *owner* is not a process that could still be doing the work.

    A row with no owner predates owners and cannot be running. A process that
    exists but cannot be inspected counts as alive: wrongly leaving a row for
    later is harmless, wrongly closing a live turn is not.
    """
    if not owner:
        return True
    try:
        pid_text, recorded, token = owner.split(":", 2)
        pid = int(pid_text)
    except ValueError:
        return True
    if token == _TOKEN:
        return False
    if pid == os.getpid():
        # Our pid but not our token: a process before us that had this pid.
        return True
    try:
        started = _started(pid)
    except psutil.AccessDenied:
        return False
    if started is None:
        return True
    if _LINUX or "/" in recorded:
        return started != recorded
    try:
        return abs(float(started) - float(recorded)) > _START_TOLERANCE
    except ValueError:
        return True
