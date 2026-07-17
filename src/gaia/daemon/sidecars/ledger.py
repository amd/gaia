# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``sidecars.json`` — the daemon's spawn ledger, for crash-reap (#2142 D-4a).

Every sidecar spawn is recorded (``agent_id``, ``pid``, ``port``, ``mode``,
``argv``, ``started_at`` — NEVER the token); a clean stop removes the entry.
After a hard daemon death (SIGKILL / OOM / power loss) the next daemon start
runs :func:`reap_stale`: each recorded pid is identity-checked (health probe
first, cmdline fallback) and tree-killed only when confirmed to still be ours —
a reused pid is left alone. The ledger is then truncated to ``[]``; survivors
are never silently adopted.

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


def reap_stale(specs: "dict[str, AgentSidecarSpec]") -> "list[int]":
    """Kill identity-confirmed survivors of a dead daemon; truncate the ledger.

    Identity, per entry: (1) PRIMARY — the recorded port's ``/health`` answers
    with the spec's ``service_id``; (2) FALLBACK (port dead/foreign) — the live
    pid's cmdline contains both the recorded ``argv[0]`` and ``--port <port>``.
    Confirmed → tree-kill. No match → the pid was reused; leave it alone. The
    ledger is truncated to ``[]`` afterwards regardless — no silent adoption.
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
            body = _probe_health(port)
            confirmed = bool(body) and body.get("service") == spec.service_id
            if not confirmed:
                cmdline = _pid_cmdline(pid)
                confirmed = bool(
                    cmdline
                    and argv
                    and str(argv[0]) in cmdline
                    and f"--port {port}" in cmdline
                )
            if confirmed:
                logger.info(
                    "sidecar ledger: reaping stale %s sidecar pid=%s port=%s",
                    agent_id,
                    pid,
                    port,
                )
                _tree_kill(pid)
                killed.append(pid)
            else:
                logger.info(
                    "sidecar ledger: pid=%s no longer matches %s (reused); "
                    "leaving it alone",
                    pid,
                    agent_id,
                )
        _write_entries([])
        return killed
