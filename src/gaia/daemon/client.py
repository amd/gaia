# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Client-side daemon helpers: start-or-attach, attach, shutdown request.

``start_or_attach()`` is the entry point every client (the web UI and the
``gaia <agent>`` CLIs) calls. It returns the running daemon, starting one only if
needed — and does so under an exclusive start lock so two concurrent callers yield
exactly ONE daemon (the second attaches to the first's instance.json).

Recovery follows §0.25: a recorded instance is trusted only when its pid is alive
AND a token-authed probe succeeds. A dead pid → reclaim by spawning fresh; a
live-but-unresponsive pid → terminate it, then spawn fresh.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional

from gaia.daemon import paths
from gaia.daemon.constants import API_PREFIX, AUTH_SCHEME, DAEMON_API_VERSION
from gaia.daemon.errors import DaemonError, DaemonStartError, DaemonVersionError
from gaia.daemon.instance import (
    DaemonInstance,
    is_live,
    pid_alive,
    probe,
    read_instance,
    terminate_instance,
)
from gaia.daemon.lock import StartLock
from gaia.logger import get_logger

logger = get_logger(__name__)

_START_TIMEOUT = 30.0

# The daemon MINOR that introduced the /daemon/v1/agents control plane + the
# /v1/<agent>/* relay (#2142 / #2150). A pre-#2142 daemon passes the MAJOR gate
# but would 404 every agents/relay route — fail loudly instead. A stale daemon
# surviving an app auto-update is the EXPECTED path, not an edge case.
_REQUIRED_AGENTS_MINOR = 1

# Ensure: connect fast; read generously — a first-run ensure may lazily fetch the
# sidecar binary before answering (mirrors gaia.ui.email_sidecar.daemon_client).
_ENSURE_TIMEOUT = (5.0, 900.0)


def _major(version: str) -> int:
    try:
        return int(str(version).split(".")[0])
    except (ValueError, IndexError) as e:
        raise DaemonVersionError(
            f"cannot parse a MAJOR daemon API version from '{version}'"
        ) from e


def _check_version(inst: DaemonInstance) -> None:
    """Fail loudly on a daemon↔client MAJOR skew (§0.25) — no silent stale attach."""
    if _major(inst.api_version) != _major(DAEMON_API_VERSION):
        raise DaemonVersionError(
            f"the running daemon speaks host API v{inst.api_version} but this client "
            f"speaks v{DAEMON_API_VERSION}. An app update replaced the client while the "
            "old daemon kept running. Restart it with `gaia daemon restart`, then retry."
        )


def attach(timeout: float = 1.5) -> Optional[DaemonInstance]:
    """Return the running daemon if one is live and version-compatible, else None."""
    inst = read_instance()
    if inst is None or not is_live(inst, timeout=timeout):
        return None
    _check_version(inst)
    return inst


def start_or_attach(timeout: float = _START_TIMEOUT) -> DaemonInstance:
    """Return the running daemon, starting one if needed. Single-instance guaranteed."""
    inst = attach()
    if inst is not None:
        return inst

    with StartLock(timeout=timeout):
        # Re-check under the lock: a concurrent caller may have just started it.
        inst = read_instance()
        if inst is not None and is_live(inst):
            _check_version(inst)
            return inst
        # Stale registry. If the pid is alive but unresponsive, it is our own
        # daemon gone bad — terminate it before reclaiming (§0.25).
        if inst is not None and pid_alive(inst.pid):
            logger.warning(
                "daemon: instance pid=%s is alive but unresponsive; reclaiming",
                inst.pid,
            )
            terminate_instance(inst)
        return _spawn_and_wait(timeout)


def _spawn_and_wait(timeout: float) -> DaemonInstance:
    """Spawn the daemon detached, wait until it registers a live instance.json."""
    paths.ensure_host_dir()
    log_file = open(paths.log_path(), "ab")
    # 0600: the daemon log lives beside token-minting code (D-5, #2142) and
    # would otherwise land world-readable under umask 022.
    try:
        os.chmod(paths.log_path(), 0o600)
    except OSError:
        pass
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: outlive the launcher.
        creationflags = 0x00000008 | 0x00000200
    else:
        start_new_session = True
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "gaia.daemon"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
    except OSError as e:
        log_file.close()
        raise DaemonStartError(
            f"failed to launch the daemon ({sys.executable} -m gaia.daemon): {e}. "
            f"Check the Python environment and `gaia daemon logs` at {paths.log_path()}."
        ) from e
    finally:
        # The child inherits the fd; the parent no longer needs it.
        log_file.close()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            raise DaemonStartError(
                f"the daemon process exited early (code {rc}) before registering. "
                f"See the daemon log at {paths.log_path()}."
            )
        inst = read_instance()
        # Trust only OUR child's registration (pid match), and only once live.
        if inst is not None and inst.pid == proc.pid and is_live(inst):
            _check_version(inst)
            logger.info("daemon: started pid=%s port=%s", inst.pid, inst.port)
            return inst
        time.sleep(0.1)

    raise DaemonStartError(
        f"the daemon did not become healthy within {timeout}s. It may be stuck "
        f"binding a port or importing. Inspect the daemon log at {paths.log_path()}."
    )


def _check_agents_floor(inst: DaemonInstance) -> None:
    """Fail loudly if the running daemon predates the agents/relay control plane."""
    parts = str(inst.api_version).split(".")
    try:
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        minor = 0
    if minor < _REQUIRED_AGENTS_MINOR:
        raise DaemonVersionError(
            f"the running daemon (host API v{inst.api_version}) predates the "
            "sidecar control plane + relay (needs v1.1+) — it would 404 every "
            "agents route. Run `gaia daemon restart`, then retry."
        )


def _error_detail(response) -> str:
    """The actionable ``detail`` from a daemon error response (or raw status)."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return f"HTTP {response.status_code}"


def ensure_agent(
    agent_id: str,
    *,
    mode: Optional[str] = None,
    timeout=_ENSURE_TIMEOUT,
) -> DaemonInstance:
    """Start-or-attach the daemon and ensure *agent_id*'s sidecar is running.

    Returns the :class:`DaemonInstance` — ``base_url`` + the DAEMON client token —
    which a thin client drives the sidecar through, exclusively via the daemon
    relay (``ANY /v1/<agent>/*``). The sidecar's own port/bearer in the ensure
    response are deliberately DISCARDED here so a thin client never holds sidecar
    credentials (design §0.0 "one contract, many front-doors"): it presents only
    the daemon client token, and the daemon swaps it for the sidecar bearer
    server-side.

    Blocking (daemon start + sidecar spawn + possible first-run binary fetch) —
    call it off any event loop. Every failure raises :class:`DaemonError` with an
    actionable remedy; there is no silent in-process fallback.
    """
    import requests

    inst = start_or_attach()
    _check_agents_floor(inst)
    url = f"{inst.base_url}{API_PREFIX}/agents/{agent_id}/ensure"
    payload = {} if mode is None else {"mode": mode}
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to ensure the "
            f"'{agent_id}' sidecar: {e}. Check `gaia daemon status` and the "
            f"daemon log at {paths.log_path()}."
        ) from e
    if r.status_code != 200:
        raise DaemonError(
            f"the daemon refused to ensure the '{agent_id}' sidecar: "
            f"{_error_detail(r)}"
        )
    # Deliberately DO NOT read/return the response body — it carries the sidecar
    # bearer token, which a thin client must never learn or hold.
    return inst


def request_shutdown(inst: DaemonInstance, timeout: float = 5.0) -> bool:
    """Ask the daemon to shut down gracefully via its authed shutdown route.

    Returns True if the daemon accepted the request. Raises DaemonError on a
    transport failure so the caller can decide whether to escalate to a kill.
    """
    import requests

    url = f"{inst.base_url}{API_PREFIX}/shutdown"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to request shutdown: {e}. "
            "It may already be dead; `gaia daemon status` will confirm."
        ) from e
    return r.status_code == 200


def wait_until_gone(inst: DaemonInstance, timeout: float = 10.0) -> bool:
    """Poll until the daemon pid is no longer alive. Returns True if it exited."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(inst.pid):
            return True
        time.sleep(0.1)
    return not pid_alive(inst.pid)


def status_report() -> dict:
    """Structured status for the CLI: running/stale/absent + identity + uptime."""
    inst = read_instance()
    if inst is None:
        return {"state": "not_running"}
    if not pid_alive(inst.pid):
        return {"state": "stale", "reason": "pid_dead", "instance": inst}
    body = probe(inst)
    if body is None:
        return {"state": "stale", "reason": "unresponsive", "instance": inst}
    return {"state": "running", "instance": inst, "status": body}
