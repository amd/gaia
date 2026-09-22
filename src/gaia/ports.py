# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Finding — and safely terminating — the process listening on a TCP port.

One implementation for every "stop whatever is on this port" path in GAIA
(``gaia kill --port``, ``gaia api stop``, ``python -m gaia.api.app stop``).
They each grew their own copy, and each copy substring-matched the port
number against whole ``netstat`` lines: ``":80"`` also matches a ``:8009``
foreign address, a ``:8080`` listener, and every ESTABLISHED / TIME_WAIT row,
so a mistyped port terminated an unrelated program.

Two rules make the targeting safe, and both live here:

1. Only a socket in the LISTENING state whose *local* port is exactly the
   requested one counts. Columns are parsed; nothing is substring-matched.
2. The owning process must be GAIA's or Lemonade's. Same identity check
   :meth:`gaia.llm.lemonade_embedded.LemonadeEmbedded._daemon_alive` makes
   before trusting a recorded pid.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import List, Tuple

log = logging.getLogger(__name__)

# Process image names a "stop what's on this port" command may terminate.
# The check is interpreter-level, not process-level: GAIA's own servers run as
# `python.exe` / `node`, so any Python or Node listener passes while a native
# service (svchost, nginx, sshd, a database) does not.
KILLABLE_PROCESS_NAMES = (
    "gaia",
    "lemonade",
    "lemond",
    "llama-server",
    "python",
    "pythonw",
    "node",
    "electron",
)


def address_port(address: str) -> str:
    """Return the port field of a netstat address (``0.0.0.0:80``, ``[::]:80``)."""
    return address.rsplit(":", 1)[-1] if ":" in address else ""


def is_listening_row(state: str, foreign_address: str) -> bool:
    """Whether a netstat TCP row is a listening socket rather than a connection.

    The state column is localized on non-English Windows, so a listener is also
    recognised structurally: only a listening socket has no foreign port.
    """
    if state.upper() in ("LISTENING", "LISTEN"):
        return True
    return address_port(foreign_address) in ("0", "*")


def parse_windows_netstat_listeners(output: str, port: int) -> List[int]:
    """PIDs of TCP sockets listening on exactly ``port`` in ``netstat -ano`` output."""
    pids: List[int] = []
    for line in output.splitlines():
        parts = line.split()
        # A TCP row is exactly: proto, local, foreign, state, pid. UDP rows
        # have no state column and are never what a server is listening on.
        if len(parts) != 5 or not parts[0].upper().startswith("TCP"):
            continue
        _, local, foreign, state, pid_field = parts
        if address_port(local) != str(port):
            continue
        if not is_listening_row(state, foreign):
            continue
        try:
            pid = int(pid_field)
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def parse_unix_netstat_listeners(output: str, port: int) -> List[Tuple[int, str]]:
    """``(pid, process_name)`` pairs listening on ``port`` in ``netstat -tulpn`` output."""
    found: List[Tuple[int, str]] = []
    for line in output.splitlines():
        parts = line.split()
        # proto, recv-q, send-q, local, foreign, state, pid/program
        if len(parts) < 7 or not parts[0].lower().startswith("tcp"):
            continue
        local, foreign, state, program = parts[3], parts[4], parts[5], parts[6]
        if address_port(local) != str(port):
            continue
        if not is_listening_row(state, foreign):
            continue
        pid_field, _, name = program.partition("/")
        try:
            pid = int(pid_field)
        except ValueError:
            continue
        if pid > 0 and pid not in [p for p, _ in found]:
            found.append((pid, name.strip()))
    return found


def process_image_name(pid: int) -> str:
    """Best-effort lowercased image name of ``pid``; empty string when unknown.

    An unknown name is never killable, so failing to read one fails closed.
    """
    try:
        if sys.platform.startswith("win"):
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
                check=False,
            )
            first = result.stdout.strip().splitlines()[:1]
            if not first or not first[0].startswith('"'):
                return ""
            return first[0].split('","')[0].strip('"').lower()
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            check=False,
        )
        return result.stdout.strip().lower()
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Could not read the process name for pid %s: %s", pid, e)
        return ""


def is_killable_process(name: str) -> bool:
    """Whether a process image name belongs to GAIA or Lemonade."""
    lowered = (name or "").lower()
    return any(allowed in lowered for allowed in KILLABLE_PROCESS_NAMES)


def listeners_on_port(port: int) -> List[Tuple[int, str]]:
    """Return ``(pid, process_name)`` for every process listening on ``port``.

    Raises:
        FileNotFoundError: neither lsof nor netstat is available.
        subprocess.CalledProcessError: the listing tool failed outright.
    """
    if sys.platform.startswith("win"):
        output = subprocess.check_output(
            ["netstat", "-ano"], text=True, errors="replace"
        )
        return [
            (pid, process_image_name(pid))
            for pid in parse_windows_netstat_listeners(output, port)
        ]

    try:
        # -sTCP:LISTEN keeps the client end of every connection out of the
        # result; without it lsof returns both ends and kill -9 took out the
        # Agent UI backend and any `gaia chat` talking to Lemonade.
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        # lsof is not installed. netstat names the owning process itself.
        netstat_output = subprocess.check_output(
            ["netstat", "-tulpn"], text=True, errors="replace"
        )
        return parse_unix_netstat_listeners(netstat_output, port)

    if result.returncode not in (0, 1):
        # 1 is lsof's "no matching sockets"; anything else is a real failure.
        raise subprocess.CalledProcessError(
            result.returncode, "lsof", output=result.stdout, stderr=result.stderr
        )

    listeners: List[Tuple[int, str]] = []
    for entry in result.stdout.split():
        try:
            pid = int(entry.strip())
        except ValueError:
            continue
        if pid > 0:
            listeners.append((pid, process_image_name(pid)))
    return listeners


def terminate_pid(pid: int) -> None:
    """Terminate ``pid`` with the platform's forceful kill."""
    if sys.platform.startswith("win"):
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], shell=False, check=True)
    else:
        subprocess.run(["kill", "-9", str(pid)], shell=False, check=True)
