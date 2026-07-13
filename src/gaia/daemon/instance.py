# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``instance.json`` — the daemon's single-instance registry, plus liveness/probe.

The file records the running daemon's ``pid``, loopback ``port``, and a minted
client-auth ``token``. It is written mode ``0600`` via temp-file-then-rename so a
crash mid-write can never leave a half-written (and therefore trusted) file — the
same atomic-write discipline the email sidecar manager uses.

Trusting the file requires TWO checks (design §0.25): the recorded pid must be
alive AND a ``/daemon/v1/status`` probe on the recorded port must answer with our
service id, matching pid, and the recorded token. After SIGKILL/OOM/power-loss the
file points at a dead pid or a freed port some unrelated process now owns — either
check fails, and the caller reclaims the lock.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

from gaia.daemon import paths
from gaia.daemon.constants import DAEMON_API_VERSION, HOST, SERVICE_ID
from gaia.logger import get_logger

logger = get_logger(__name__)

# Fields persisted to instance.json. Kept explicit so a future field addition is a
# deliberate contract change, not an accidental leak of internal state.
_PERSISTED = ("pid", "port", "token", "host", "api_version", "service", "started_at")

# Default timeout for the status probe. Short: a live daemon answers loopback in
# milliseconds; a longer wait just delays reclaim of a dead one.
_PROBE_TIMEOUT = 1.5


@dataclass(frozen=True)
class DaemonInstance:
    """Immutable snapshot of the running daemon's identity."""

    pid: int
    port: int
    token: str
    host: str = HOST
    api_version: str = DAEMON_API_VERSION
    service: str = SERVICE_ID
    started_at: float = field(default=0.0)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k in _PERSISTED}


def write_instance(inst: DaemonInstance) -> None:
    """Atomically persist *inst* to instance.json at mode 0600.

    Writes a uniquely-named temp file in the same directory (so ``os.replace`` is a
    same-filesystem atomic rename), fsyncs it, then renames it over the target. The
    temp file is created ``O_EXCL`` at 0600 so it is never briefly world-readable.
    """
    d = paths.ensure_host_dir()
    target = paths.instance_path()
    tmp = d / f".instance.{os.getpid()}.tmp"
    payload = json.dumps(inst.to_dict(), indent=2)
    # O_EXCL: a leftover temp from a prior crashed writer must not be reused.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC
    if tmp.exists():
        tmp.unlink()
    fd = os.open(str(tmp), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(str(tmp), str(target))
    # os.replace preserves the temp's 0600 mode; re-assert defensively for
    # platforms where the source mode is not carried across the rename.
    try:
        os.chmod(str(target), 0o600)
    except OSError:
        pass


def read_instance() -> Optional[DaemonInstance]:
    """Load instance.json, or ``None`` if absent/corrupt.

    A corrupt or truncated file is treated as "no trustworthy instance" and logged
    (not silently ignored) — the caller then reclaims via a fresh start.
    """
    path = paths.instance_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("daemon: cannot read %s: %s", path, e)
        return None
    try:
        data = json.loads(raw)
        return DaemonInstance(
            pid=int(data["pid"]),
            port=int(data["port"]),
            token=str(data["token"]),
            host=str(data.get("host", HOST)),
            api_version=str(data.get("api_version", DAEMON_API_VERSION)),
            service=str(data.get("service", SERVICE_ID)),
            started_at=float(data.get("started_at", 0.0)),
        )
    except (ValueError, KeyError, TypeError) as e:
        logger.warning(
            "daemon: %s is present but malformed (%s); treating as stale", path, e
        )
        return None


def remove_instance(*, only_pid: Optional[int] = None) -> None:
    """Delete instance.json.

    When *only_pid* is given, delete only if the file still records that pid — so a
    shutting-down daemon never clobbers the registry of a newer daemon that already
    reclaimed the slot.
    """
    path = paths.instance_path()
    if only_pid is not None:
        inst = read_instance()
        if inst is not None and inst.pid != only_pid:
            return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("daemon: could not remove %s: %s", path, e)


def pid_alive(pid: int) -> bool:
    """True if *pid* refers to a running process (cross-platform via psutil)."""
    import psutil

    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).status() not in (
            psutil.STATUS_ZOMBIE,
            psutil.STATUS_DEAD,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        # AccessDenied means the pid exists but is owned by another user — from our
        # perspective that is "not our daemon"; the probe below is the real check.
        return psutil.pid_exists(pid)


def probe(inst: DaemonInstance, timeout: float = _PROBE_TIMEOUT) -> Optional[dict]:
    """Probe the recorded port with the recorded token.

    Returns the status payload only if the server answers 200 with our service id
    and the pid recorded in the file — otherwise ``None`` (an unrelated process
    grabbed the freed port, or the daemon is unresponsive). Never raises.
    """
    import requests

    from gaia.daemon.constants import API_PREFIX, AUTH_SCHEME

    url = f"{inst.base_url}{API_PREFIX}/status"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    if body.get("service") != SERVICE_ID:
        return None
    if int(body.get("pid", -1)) != inst.pid:
        return None
    return body


def is_live(inst: DaemonInstance, timeout: float = _PROBE_TIMEOUT) -> bool:
    """True only if the pid is alive AND the token-authed probe succeeds."""
    return pid_alive(inst.pid) and probe(inst, timeout=timeout) is not None


def terminate_instance(inst: DaemonInstance, timeout: float = 5.0) -> None:
    """Best-effort tree-kill of a stale-but-alive daemon so its slot can be reclaimed.

    Used when the recorded pid is alive but unresponsive to the probe (§0.25). The
    pid comes from our own registry file, so terminating it is legitimate reclaim,
    not killing an arbitrary process.
    """
    import psutil

    try:
        proc = psutil.Process(inst.pid)
    except psutil.NoSuchProcess:
        return
    procs = []
    try:
        procs = proc.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    procs.append(proc)
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
