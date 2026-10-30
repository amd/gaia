# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Find GAIA daemons running on this machine, and stop the orphaned ones.

Backs ``gaia kill --daemons``. A daemon is *orphaned* when its home folder or
its ``instance.json`` is gone: no client can find it, so nothing will ever ask
it to stop. Test runs that deleted their temporary home used to leave these
behind by the dozen.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Tuple

from gaia.config import default_gaia_home
from gaia.logger import get_logger

logger = get_logger(__name__)

#: A daemon younger than this may not have written instance.json yet.
STARTUP_GRACE_SECONDS = 120.0


@dataclass(frozen=True)
class DaemonProcess:
    """One running ``python -m gaia.daemon``."""

    pid: int
    started: float
    #: The daemon's state folder, or None when its environment is unreadable.
    home: Optional[Path]
    #: The interpreter a Windows venv launcher runs the daemon in, which is
    #: the pid its instance.json records.
    inner_pids: Tuple[int, ...] = ()

    @property
    def pids(self) -> Tuple[int, ...]:
        return (self.pid, *self.inner_pids)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.started)

    def is_orphaned(self) -> bool:
        """Its home or instance.json is gone, and it is past startup."""
        if self.home is None or self.age_seconds < STARTUP_GRACE_SECONDS:
            return False
        return not (self.home / "instance.json").is_file()


def is_daemon_cmdline(cmdline: List[str]) -> bool:
    """True for ``<python> -m gaia.daemon`` and nothing else."""
    return len(cmdline) >= 3 and cmdline[-2:] == ["-m", "gaia.daemon"]


def home_from_environ(environ: dict) -> Path:
    """The daemon home a process with *environ* uses (see :func:`paths.host_dir`)."""
    override = environ.get("GAIA_DAEMON_HOME")
    if override:
        return Path(override)
    gaia_home = environ.get("GAIA_HOME", "").strip()
    if gaia_home:
        return Path(os.path.expandvars(os.path.expanduser(gaia_home))) / "host"
    default = default_gaia_home()
    user_home = environ.get("USERPROFILE" if os.name == "nt" else "HOME")
    if user_home:
        default = Path(user_home) / default.relative_to(Path.home())
    return default / "host"


def find_daemons() -> List[DaemonProcess]:
    """Every GAIA daemon this user can see, oldest first.

    A Windows venv's ``python.exe`` runs the real interpreter as a child with
    the same command line; only the outermost process of such a pair is listed.
    """
    import psutil

    found = {}
    for proc in psutil.process_iter(["pid", "ppid", "cmdline", "create_time"]):
        if not is_daemon_cmdline(proc.info["cmdline"] or []):
            continue
        try:
            home = home_from_environ(proc.environ())
        except (psutil.AccessDenied, psutil.ZombieProcess):
            home = None
        except psutil.NoSuchProcess:
            continue
        found[proc.info["pid"]] = (
            proc.info["ppid"],
            DaemonProcess(proc.info["pid"], proc.info["create_time"], home),
        )
    inner = {}
    for pid, (ppid, _) in found.items():
        if ppid in found:
            inner.setdefault(ppid, []).append(pid)
    daemons = [
        replace(d, inner_pids=tuple(inner.get(d.pid, ())))
        for ppid, d in found.values()
        if ppid not in found
    ]
    return sorted(daemons, key=lambda d: d.started)


def stop_daemon(daemon: DaemonProcess, timeout: float = 10.0) -> bool:
    """Kill *daemon*. True once it is gone.

    A hard kill, and only the daemon itself (with the interpreter a Windows
    venv launcher runs it in): a graceful exit, or killing its process tree,
    would stop the Lemonade Server it started, which may be the one your own
    GAIA now uses.
    """
    import psutil

    try:
        proc = psutil.Process(daemon.pid)
        if abs(proc.create_time() - daemon.started) > 1.0:
            return True  # the pid was reused; ours is already gone
        procs = [proc]
        for child in proc.children():
            try:
                if is_daemon_cmdline(child.cmdline()):
                    procs.append(child)
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        return True
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    if alive:
        logger.error(
            "daemon pid %s: %s did not exit", daemon.pid, [p.pid for p in alive]
        )
    return not alive


def same_home(a: Path, b: Path) -> bool:
    """Whether two daemon homes are the same folder."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def format_age(seconds: float) -> str:
    """``3d 4h``, ``2h 5m``, ``7m 3s`` or ``45s``."""
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


__all__ = [
    "DaemonProcess",
    "STARTUP_GRACE_SECONDS",
    "find_daemons",
    "format_age",
    "home_from_environ",
    "is_daemon_cmdline",
    "same_home",
    "stop_daemon",
]
