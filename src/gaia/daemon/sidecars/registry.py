# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``SidecarRegistry`` — one :class:`AgentSidecarManager` per agent_id, plus the
policy the daemon enforces around it (#2142 D-4): atomic get-or-create, the
live-sidecar cap, the resolved-mode conflict check, and stop with a post-kill
liveness verification (D-4b).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

import psutil

from gaia.daemon.sidecars.errors import (
    CapacityError,
    ModeConflictError,
    StopFailedError,
    UnknownAgentError,
)
from gaia.daemon.sidecars.manager import AgentSidecarManager
from gaia.daemon.sidecars.spec import AgentSidecarSpec
from gaia.logger import get_logger

logger = get_logger(__name__)

# Hard cap on concurrently-running sidecars. No eviction on overflow — the
# idle reaper is V2-15's; an in-flight long-running clock inside a sidecar
# must never be silently killed to make room.
MAX_LIVE_SIDECARS = 3


class SidecarRegistry:
    def __init__(
        self,
        specs: "dict[str, AgentSidecarSpec]",
        max_live: int = MAX_LIVE_SIDECARS,
        *,
        on_spawn: Optional[Callable[[str, AgentSidecarManager], None]] = None,
        on_stop: Optional[Callable[[str], None]] = None,
    ):
        self._specs = dict(specs)
        self.max_live = max_live
        self._on_spawn = on_spawn
        self._on_stop = on_stop
        # agent_id -> (manager, per-agent lock). The registry lock guards this
        # map (atomic get-or-create); the per-agent lock serializes the slow
        # is_running-check + start() so N concurrent first ensures spawn ONE
        # process without blocking ensures of OTHER agents.
        self._managers: "dict[str, tuple]" = {}
        self._lock = threading.Lock()
        # Injection seam for tests: constructs the manager for a spec.
        self._manager_factory = AgentSidecarManager

    def _spec(self, agent_id: str) -> AgentSidecarSpec:
        spec = self._specs.get(agent_id)
        if spec is None:
            raise UnknownAgentError(
                f"unknown agent '{agent_id}'; registered agents: "
                + ", ".join(sorted(self._specs))
            )
        return spec

    def _resolve_mode(self, spec: AgentSidecarSpec, mode: Optional[str]) -> str:
        return mode or os.environ.get(spec.mode_env_var) or "user"

    def _running_ids(self) -> "list[str]":
        return [aid for aid, (m, _) in self._managers.items() if m.is_running]

    def ensure(self, agent_id: str, mode: Optional[str] = None) -> dict:
        """Spawn-or-attach *agent_id*'s sidecar; return its fields + token."""
        spec = self._spec(agent_id)
        with self._lock:
            holder = self._managers.get(agent_id)
            if holder is None:
                holder = (
                    self._manager_factory(
                        spec, mode=mode, expected_api_version=spec.expected_api_major
                    ),
                    threading.Lock(),
                )
                self._managers[agent_id] = holder
        manager, agent_lock = holder
        with agent_lock:
            requested = self._resolve_mode(spec, mode)
            if manager.is_running:
                if requested != manager.resolved_mode:
                    raise ModeConflictError(
                        f"agent '{agent_id}' is already running in "
                        f"'{manager.resolved_mode}' mode but '{requested}' was "
                        f"requested. Stop it first (`gaia daemon stop-agent "
                        f"{agent_id}`), then re-ensure in the new mode."
                    )
                return self._entry(agent_id, manager, include_token=True)
            with self._lock:
                running = self._running_ids()
                if len(running) >= self.max_live:
                    raise CapacityError(
                        f"sidecar capacity reached (max {self.max_live}); "
                        f"running: {', '.join(sorted(running))}. Stop one "
                        "(`gaia daemon stop-agent <id>`) before starting another."
                    )
            if mode is not None and self._manager_mode(manager) != requested:
                # A stopped manager built for another mode: replace it so the
                # explicit request wins (fresh token, fresh state).
                manager = self._manager_factory(
                    spec, mode=mode, expected_api_version=spec.expected_api_major
                )
                with self._lock:
                    self._managers[agent_id] = (manager, agent_lock)
            manager.start()
            if self._on_spawn is not None:
                self._on_spawn(agent_id, manager)
            return self._entry(agent_id, manager, include_token=True)

    @staticmethod
    def _manager_mode(manager) -> str:
        return manager.mode

    def list_agents(self) -> "list[dict]":
        """One entry per registered spec, running or not. NEVER includes tokens."""
        with self._lock:
            managers = dict(self._managers)
        entries = []
        for agent_id in sorted(self._specs):
            holder = managers.get(agent_id)
            manager = holder[0] if holder is not None else None
            if manager is not None and manager.is_running:
                entries.append(self._entry(agent_id, manager, include_token=False))
            else:
                entries.append(
                    {
                        "agent_id": agent_id,
                        "state": "stopped",
                        "mode": None,
                        "pid": None,
                        "port": None,
                        "base_url": None,
                        "api_version": None,
                        "agent_version": None,
                        "started_at": None,
                        "dev_src_dir": self._dev_src_dir(agent_id),
                    }
                )
        return entries

    def stop(self, agent_id: str) -> dict:
        """Tree-kill *agent_id*'s sidecar and VERIFY the pid is gone (D-4b).

        The manager's ``shutdown()`` never raises — every kill error is
        swallowed there. This post-kill liveness check is what turns a silent
        survivor into a loud failure the caller can act on.
        """
        self._spec(agent_id)
        with self._lock:
            holder = self._managers.get(agent_id)
        if holder is None or not holder[0].is_running:
            return {"agent_id": agent_id, "state": "stopped"}
        manager, agent_lock = holder
        with agent_lock:
            if not manager.is_running:
                return {"agent_id": agent_id, "state": "stopped"}
            pid = manager.pid
            manager.shutdown()
            if pid is not None and psutil.pid_exists(pid):
                raise StopFailedError(
                    f"agent '{agent_id}' sidecar pid {pid} survived the "
                    "tree-kill and is still alive. Inspect the process and "
                    "kill it manually before retrying."
                )
            if self._on_stop is not None:
                self._on_stop(agent_id)
        return {"agent_id": agent_id, "state": "stopped"}

    def shutdown_all(self) -> None:
        """Tree-kill every running sidecar (daemon shutdown path)."""
        with self._lock:
            managers = list(self._managers.items())
        for agent_id, (manager, _) in managers:
            if manager.is_running:
                logger.info("registry: shutting down %s sidecar", agent_id)
                manager.shutdown()
                if self._on_stop is not None:
                    self._on_stop(agent_id)

    def _dev_src_dir(self, agent_id: str) -> Optional[str]:
        spec = self._specs[agent_id]
        return str(spec.dev_src_dir) if spec.dev_src_dir is not None else None

    def _entry(self, agent_id: str, manager, *, include_token: bool) -> dict:
        entry = {
            "agent_id": agent_id,
            "state": "running" if manager.is_running else "stopped",
            "mode": manager.resolved_mode,
            "pid": manager.pid,
            "port": manager.port,
            "base_url": manager.base_url,
            "api_version": manager.api_version,
            "agent_version": manager.agent_version,
            "started_at": manager.started_at,
            "dev_src_dir": self._dev_src_dir(agent_id),
        }
        if include_token:
            entry["token"] = manager.auth_token
        return entry


def _now() -> float:  # pragma: no cover - trivial seam
    return time.time()
