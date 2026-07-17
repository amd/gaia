# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The UI backend's client onto the daemon's sidecar control plane (#2142 T3).

The UI no longer spawns sidecars in-process: :func:`acquire_handle` asks the
always-on GAIA daemon to spawn-or-attach the agent's sidecar
(``POST /daemon/v1/agents/{id}/ensure``) and returns a :class:`SidecarHandle`
the caller builds a request proxy from. Acquisition is per-call — no client
cache, no health state machine; the daemon's fast is_running path keeps the
cost profile of the old idempotent ``manager.start()``.

Names are deliberately generic (``acquire_handle``/``stop_sidecar`` keyed by
``agent_id``) so the V2-7 data-plane relocation does not have to rename them.

Every daemon-side failure is re-raised as :class:`SidecarError` with the
actionable remedy — WITHOUT this seam, daemon-start failures would fall
through the routers' ``except SidecarError`` blocks to a bare 500.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import requests

from gaia.daemon import paths
from gaia.daemon.client import attach, start_or_attach
from gaia.daemon.errors import DaemonError
from gaia.daemon.instance import DaemonInstance
from gaia.daemon.sidecars.errors import SidecarError
from gaia.daemon.sidecars.spec import builtin_specs
from gaia.logger import get_logger

logger = get_logger(__name__)

# Connect fast; read generously — a first-run ensure may lazily fetch the
# sidecar binary before answering.
_ENSURE_TIMEOUT = (5.0, 900.0)
_STOP_TIMEOUT = (5.0, 30.0)

# The daemon MINOR that introduced the /daemon/v1/agents control plane. An
# already-running pre-#2142 daemon passes the MAJOR gate but would 404 every
# agents route with a bare Starlette body — fail loudly instead. A stale
# daemon surviving an app auto-update is the EXPECTED path, not an edge case.
_REQUIRED_DAEMON_MINOR = 1


@dataclass(frozen=True)
class SidecarHandle:
    """One acquisition's view of a running sidecar."""

    base_url: str
    token: str
    api_version: Optional[str]
    agent_version: Optional[str]
    mode: Optional[str]
    pid: Optional[int]

    def proxy(self, **kwargs):
        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        kwargs.setdefault("auth_token", self.token)
        return EmailSidecarProxy(self.base_url, **kwargs)


def _wrap_daemon_error(e: DaemonError) -> SidecarError:
    return SidecarError(
        f"cannot use the GAIA daemon for the sidecar: {e} "
        f"Check `gaia daemon status` and the daemon log at {paths.log_path()}."
    )


def _check_agents_floor(inst: DaemonInstance) -> None:
    parts = str(inst.api_version).split(".")
    try:
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        minor = 0
    if minor < _REQUIRED_DAEMON_MINOR:
        raise SidecarError(
            f"the running daemon (host API v{inst.api_version}) predates the "
            "sidecar control plane (needs v1.1+) — it would 404 every agents "
            "route. Run `gaia daemon restart`, then retry."
        )


def _resolved_mode(agent_id: str) -> str:
    spec = builtin_specs().get(agent_id)
    env_var = spec.mode_env_var if spec is not None else None
    return (os.environ.get(env_var) if env_var else None) or "user"


def _error_detail(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return f"HTTP {response.status_code}"


def acquire_handle(agent_id: str = "email") -> SidecarHandle:
    """Spawn-or-attach *agent_id*'s sidecar via the daemon; return a handle.

    Blocking (daemon start + sidecar spawn + possible first-run binary fetch)
    — call it off the event loop.
    """
    try:
        inst = start_or_attach()
    except DaemonError as e:
        raise _wrap_daemon_error(e) from e
    _check_agents_floor(inst)
    url = f"{inst.base_url}/daemon/v1/agents/{agent_id}/ensure"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {inst.token}"},
            json={"mode": _resolved_mode(agent_id)},
            timeout=_ENSURE_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise SidecarError(
            f"could not reach the daemon at {inst.base_url} to ensure the "
            f"'{agent_id}' sidecar: {e}. Check `gaia daemon status` and the "
            f"daemon log at {paths.log_path()}."
        ) from e
    if r.status_code != 200:
        raise SidecarError(
            f"the daemon refused to ensure the '{agent_id}' sidecar: "
            f"{_error_detail(r)}"
        )
    # Never log this body — it carries the sidecar bearer token.
    body = r.json()
    return SidecarHandle(
        base_url=body["base_url"],
        token=body["token"],
        api_version=body.get("api_version"),
        agent_version=body.get("agent_version"),
        mode=body.get("mode"),
        pid=body.get("pid"),
    )


def stop_sidecar(agent_id: str = "email") -> None:
    """Stop *agent_id*'s sidecar via the daemon. Attach-only.

    No live daemon → genuine no-op (never auto-starts a daemon just to stop
    nothing). A stop-route 500 (the sidecar survived the tree-kill, D-4b)
    raises so callers about to mutate the install dir can abort.
    """
    try:
        inst = attach()
    except DaemonError as e:
        raise _wrap_daemon_error(e) from e
    if inst is None:
        return
    _check_agents_floor(inst)
    url = f"{inst.base_url}/daemon/v1/agents/{agent_id}/stop"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {inst.token}"},
            timeout=_STOP_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise SidecarError(
            f"could not reach the daemon at {inst.base_url} to stop the "
            f"'{agent_id}' sidecar: {e}. Check `gaia daemon status` and the "
            f"daemon log at {paths.log_path()}."
        ) from e
    if r.status_code != 200:
        raise SidecarError(
            f"the daemon failed to stop the '{agent_id}' sidecar: "
            f"{_error_detail(r)}"
        )
