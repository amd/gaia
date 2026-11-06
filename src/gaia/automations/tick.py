# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Who runs what is due: one owner of the automations tick per PC.

The daemon owns it whenever it runs. The Discord bridge runs it only when no
daemon holds it, so a PC with the bridge but no daemon still gets its
reminders. The lease is a small file each owner refreshes on its tick; one
that stops refreshing, or whose process is gone, is free to take.

The daemon outranks the bridge: it takes the tick even from a bridge that
still holds it. Taking what is due is itself locked (``store.take_due``), so a
handover can never run one item twice; the lease keeps the agent turns in one
place, and makes which one that is plain.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gaia.automations import files
from gaia.logger import get_logger

log = get_logger(__name__)

OWNER_DAEMON = "daemon"
OWNER_DISCORD = "discord"

#: A lease not refreshed for this long is free. Both owners tick every minute
#: or faster, so this is three missed ticks.
STALE_SECONDS = 180.0


@dataclass
class Lease:
    owner: str
    pid: int
    at: float


def path() -> Path:
    from gaia.home import gaia_home

    return gaia_home() / "automations" / "tick.json"


def _locked():
    from gaia.utils.file_lock import file_lock

    return file_lock(path())


def _alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).status() not in (
            psutil.STATUS_ZOMBIE,
            psutil.STATUS_DEAD,
        )
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def _read() -> Optional[Lease]:
    data = files.read_json(path(), "Delete it; the next tick writes a new one.")
    if data is None:
        return None
    try:
        return Lease(str(data["owner"]), int(data["pid"]), float(data["at"]))
    except (KeyError, TypeError, ValueError) as e:
        raise files.CorruptStateFile(
            f"{path()} is not a tick lease: {e}. Delete it; the next tick writes "
            "a new one."
        ) from e


def holder(now: Optional[float] = None) -> Optional[Lease]:
    """Who holds the tick right now, or None when nobody does."""
    now = time.time() if now is None else now
    with _locked():
        lease = _read()
    if lease is None or _free(lease, now):
        return None
    return lease


def _free(lease: Lease, now: float) -> bool:
    return now - lease.at > STALE_SECONDS or not _alive(lease.pid)


def claim(owner: str, now: Optional[float] = None, pid: Optional[int] = None) -> bool:
    """Hold the tick for *owner*, or say another process holds it.

    Returns whether *owner* may run what is due on this tick.
    """
    if owner not in (OWNER_DAEMON, OWNER_DISCORD):
        raise ValueError(f"unknown automations tick owner {owner!r}")
    now = time.time() if now is None else now
    pid = os.getpid() if pid is None else pid
    with _locked():
        lease = _read()
        ours = lease is not None and lease.owner == owner and lease.pid == pid
        if not ours and lease is not None and not _free(lease, now):
            if not (owner == OWNER_DAEMON and lease.owner != OWNER_DAEMON):
                return False
            log.info("The daemon takes the automations tick from the %s", lease.owner)
        elif not ours:
            log.info("The %s now runs automations (pid %s)", owner, pid)
        files.write_json(path(), {"owner": owner, "pid": pid, "at": now})
        return True


def release(owner: str, pid: Optional[int] = None) -> None:
    """Give the tick up, so another owner takes it at once."""
    pid = os.getpid() if pid is None else pid
    with _locked():
        lease = _read()
        if lease is not None and lease.owner == owner and lease.pid == pid:
            path().unlink()
