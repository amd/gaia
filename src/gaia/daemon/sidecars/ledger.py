# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``sidecars.json`` — the daemon's spawn ledger, for crash-reap (#2142 D-4a).

Every sidecar spawn is recorded (``agent_id``, ``pid``, ``port``, ``mode``,
``argv``, ``started_at`` — NEVER the token); a clean stop removes the entry.
After a hard daemon death (SIGKILL / OOM / power loss) the next daemon start
runs :func:`reap_stale`: a live recorded pid is killed only on a cmdline
identity match; a dead leader whose port still serves our service gets a
POSIX group-kill (pid as pgid). A reused pid is left alone — never killed on
port evidence. The ledger is then truncated to ``[]``; survivors are never
silently adopted.

Every read-modify-write holds one dedicated process-wide lock (separate from
the registry lock and any manager's RLock) so two agents spawning concurrently
cannot lose each other's entries.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Optional

from gaia.daemon import paths
from gaia.daemon.sidecars.spec import AgentSidecarSpec
from gaia.logger import get_logger

logger = get_logger(__name__)

_LEDGER_LOCK = threading.Lock()

_PROBE_TIMEOUT = 1.5


def read_entries() -> "list[dict]":
    """All recorded spawn entries (empty when the ledger is absent/empty)."""
    path = paths.sidecars_ledger_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("sidecar ledger: cannot read %s: %s", path, e)
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(
            "sidecar ledger: %s is malformed (%s); treating as empty", path, e
        )
        return []
    if not isinstance(entries, list):
        logger.warning("sidecar ledger: %s is not a list; treating as empty", path)
        return []
    return entries


def _write_entries(entries: "list[dict]") -> None:
    paths.ensure_host_dir()
    paths.atomic_write_json(paths.sidecars_ledger_path(), entries)


def record_spawn(
    *,
    agent_id: str,
    pid: int,
    port: int,
    mode: str,
    argv: "list[str]",
    started_at: float,
) -> None:
    """Record (or replace) the spawn entry for *agent_id*. Never records tokens."""
    entry = {
        "agent_id": agent_id,
        "pid": pid,
        "port": port,
        "mode": mode,
        "argv": list(argv),
        "started_at": started_at,
    }
    with _LEDGER_LOCK:
        entries = [e for e in read_entries() if e.get("agent_id") != agent_id]
        entries.append(entry)
        _write_entries(entries)


def remove_entry(agent_id: str) -> None:
    """Drop *agent_id*'s entry after a clean stop."""
    with _LEDGER_LOCK:
        entries = [e for e in read_entries() if e.get("agent_id") != agent_id]
        _write_entries(entries)


# -- reap seams (monkeypatched in unit tests) --------------------------------


def _probe_health(port: int) -> Optional[dict]:
    """GET /health on loopback *port*; JSON body on 200, else None. Never raises."""
    import requests

    try:
        r = requests.get(f"http://127.0.0.1:{port}/health", timeout=_PROBE_TIMEOUT)
    except requests.exceptions.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    """True when *pid* refers to a live process."""
    import psutil

    return psutil.pid_exists(pid)


def _pid_cmdline(pid: int) -> Optional[str]:
    """The live process's full cmdline, or None when the pid is gone/foreign."""
    import psutil

    try:
        return " ".join(psutil.Process(pid).cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _tree_kill(pid: int) -> None:
    """Kill the whole process GROUP (killpg / taskkill /T) — a snapshot walk of
    children would miss re-parented uvicorn workers."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(os.getpgid(pid), 9)
    except (ProcessLookupError, OSError):
        pass


def _group_kill(pid: int) -> None:
    """POSIX: SIGKILL the process group whose id IS *pid* — the group id
    survives its (dead) leader, so this reaps re-parented children the leader
    left behind. Guarded: the group may be fully gone already."""
    try:
        os.killpg(pid, 9)
    except (ProcessLookupError, OSError):
        pass


def _cmdline_matches(cmdline: Optional[str], argv: list, port) -> bool:
    return bool(
        cmdline and argv and str(argv[0]) in cmdline and f"--port {port}" in cmdline
    )


def reap_stale(specs: "dict[str, AgentSidecarSpec]") -> "list[int]":
    """Kill identity-confirmed survivors of a dead daemon; truncate the ledger.

    Kill gates, per entry (D-4a as amended — pid identity and port identity
    are separate pieces of evidence and never conflated):

    - pid ALIVE → kill (tree-kill) iff the CMDLINE gate passes: the recorded
      ``argv[0]`` AND ``--port <port>`` both appear in the live cmdline. A live
      pid is NEVER killed on port evidence alone — the pid may have been reused
      by an innocent process while our orphaned child still serves the port.
      If the cmdline gate fails but the port probe confirms our service, log a
      loud warning and leave both alone.
    - pid DEAD but the port's ``/health`` answers with our ``service_id`` → the
      leader died but a re-parented child survives. POSIX: the process-group id
      survives its leader, so SIGKILL the group using the RECORDED pid as the
      pgid (it was the session leader via ``start_new_session``), then re-probe
      and log the outcome. Windows: no group survives the parent — log the
      survivor loudly as a known gap, never guess-kill.

    The ledger is truncated to ``[]`` afterwards regardless — no silent
    adoption.
    """
    with _LEDGER_LOCK:
        entries = read_entries()
        killed: "list[int]" = []
        for entry in entries:
            agent_id = entry.get("agent_id")
            pid = entry.get("pid")
            port = entry.get("port")
            argv = entry.get("argv") or []
            spec = specs.get(agent_id)
            if spec is None or pid is None or port is None:
                logger.warning("sidecar ledger: skipping unreapable entry %r", entry)
                continue
            if _pid_alive(pid):
                if _cmdline_matches(_pid_cmdline(pid), argv, port):
                    logger.info(
                        "sidecar ledger: reaping stale %s sidecar pid=%s port=%s",
                        agent_id,
                        pid,
                        port,
                    )
                    _tree_kill(pid)
                    killed.append(pid)
                    continue
                body = _probe_health(port)
                if bool(body) and body.get("service") == spec.service_id:
                    logger.warning(
                        "sidecar ledger: port %s still serves %s but live pid %s "
                        "is NOT ours (cmdline mismatch — pid reused). Not killing "
                        "either; the survivor on the port must be stopped "
                        "manually.",
                        port,
                        spec.service_id,
                        pid,
                    )
                else:
                    logger.info(
                        "sidecar ledger: pid=%s no longer matches %s (reused); "
                        "leaving it alone",
                        pid,
                        agent_id,
                    )
                continue
            # pid dead — check whether a re-parented child still owns the port.
            body = _probe_health(port)
            if bool(body) and body.get("service") == spec.service_id:
                if os.name == "nt":
                    logger.warning(
                        "sidecar ledger: %s leader pid=%s is dead but port %s "
                        "still serves %s; Windows cannot group-kill a dead "
                        "leader's survivors — kill the process on port %s "
                        "manually (known gap).",
                        agent_id,
                        pid,
                        port,
                        spec.service_id,
                        port,
                    )
                    continue
                logger.info(
                    "sidecar ledger: %s leader pid=%s dead but port %s still "
                    "serves %s; group-killing pgid=%s",
                    agent_id,
                    pid,
                    port,
                    spec.service_id,
                    pid,
                )
                _group_kill(pid)
                killed.append(pid)
                if _probe_health(port) is not None:
                    logger.warning(
                        "sidecar ledger: port %s STILL answering after group-kill "
                        "of pgid=%s — a survivor remains; kill it manually.",
                        port,
                        pid,
                    )
                else:
                    logger.info("sidecar ledger: port %s freed after group-kill", port)
        _write_entries([])
        return killed
