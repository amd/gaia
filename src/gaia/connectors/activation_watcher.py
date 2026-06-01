# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Background watcher that emits ``connector.activation.changed`` for
out-of-process activation writes (#1226).

The HTTP router and any SDK code running inside the UI-server process emit the
event in-process via ``gaia.connectors.api.activate`` / ``deactivate``. But the
``gaia connectors activations`` CLI runs as a *separate* process and writes the
same ``~/.gaia/connectors/activations.json`` ledger directly, so its change
never reaches the server's SSE clients. This watcher closes that gap: the
long-running server polls the ledger and emits one event per changed
``(connector_id, agent_id)`` pair, so an open Agent UI Settings tab updates
live without a manual refresh.

In-process writes are de-duplicated: ``api`` calls :func:`note_local_write`
after each write to advance the watcher snapshot, so the next poll sees no diff
and the event fires exactly once. Writes are atomic (``os.replace`` under a
per-process lock in :mod:`gaia.connectors.activations`), so each poll reads only
fully-committed state — there is no torn-read race.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from gaia.connectors import activations
from gaia.connectors.events import emit

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 1.0

# {connector_id: {agent_id: active_bool}}
Ledger = Dict[str, Dict[str, bool]]


def _safe_load() -> Ledger:
    try:
        return activations.load_activations()
    except Exception:
        logger.warning(
            "activation-watcher: could not read activations ledger", exc_info=True
        )
        return {}


def diff_ledgers(old: Ledger, new: Ledger) -> List[Tuple[str, str, bool]]:
    """Return ``(connector_id, agent_id, active)`` for every changed pair.

    Absence means inactive (the ledger deletes entries on deactivate), so a
    pair present in ``old`` but missing in ``new`` yields ``active=False``.
    """
    changes: List[Tuple[str, str, bool]] = []
    for connector_id in set(old) | set(new):
        old_agents = old.get(connector_id, {})
        new_agents = new.get(connector_id, {})
        for agent_id in set(old_agents) | set(new_agents):
            before = bool(old_agents.get(agent_id, False))
            after = bool(new_agents.get(agent_id, False))
            if before != after:
                changes.append((connector_id, agent_id, after))
    return changes


class ActivationWatcher:
    """Polls the activations ledger and emits change events."""

    def __init__(self, poll_interval: float = _DEFAULT_POLL_INTERVAL) -> None:
        self._poll_interval = poll_interval
        self._snapshot: Ledger = {}
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Seed the snapshot and launch the polling task on the running loop."""
        self._snapshot = _safe_load()
        self._task = asyncio.create_task(
            self._run(), name="connector-activation-watcher"
        )
        logger.info("activation-watcher: started (poll=%.1fs)", self._poll_interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("activation-watcher: stopped")

    def note_local_write(self, connector_id: str, agent_id: str, active: bool) -> None:
        """Advance the snapshot for one pair after an in-process write.

        Called after an in-process ledger write so the watcher does not
        re-emit an event the in-process path already sent. Only the written
        pair is advanced — a concurrent out-of-process change to a *different*
        pair is still detected on the next poll.
        """
        if active:
            self._snapshot.setdefault(connector_id, {})[agent_id] = True
        elif connector_id in self._snapshot:
            self._snapshot[connector_id].pop(agent_id, None)
            if not self._snapshot[connector_id]:
                del self._snapshot[connector_id]

    async def _run(self) -> None:
        # CancelledError subclasses BaseException (not Exception) on 3.8+, so the
        # broad ``except Exception`` below never swallows task cancellation.
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await self.poll_once()
            except Exception:
                logger.warning("activation-watcher: poll failed", exc_info=True)

    async def poll_once(self) -> List[Tuple[str, str, bool]]:
        """Emit events for any changes since the last snapshot; return them."""
        new = _safe_load()
        changes = diff_ledgers(self._snapshot, new)
        self._snapshot = new
        for connector_id, agent_id, active in changes:
            await emit(
                "connector.activation.changed",
                {
                    "connector_id": connector_id,
                    "agent_id": agent_id,
                    "active": active,
                },
            )
        return changes


# Module-level singleton managed by the UI-server lifespan.
_watcher: Optional[ActivationWatcher] = None


def start_watcher(poll_interval: float = _DEFAULT_POLL_INTERVAL) -> ActivationWatcher:
    """Create, start, and register the singleton watcher. Server lifespan only."""
    global _watcher
    _watcher = ActivationWatcher(poll_interval)
    _watcher.start()
    return _watcher


async def stop_watcher() -> None:
    """Stop and clear the singleton watcher (server shutdown)."""
    global _watcher
    if _watcher is not None:
        await _watcher.stop()
        _watcher = None


def note_local_write(connector_id: str, agent_id: str, active: bool) -> None:
    """Advance the running watcher's snapshot after an in-process write.

    No-op unless a watcher is running (e.g. in the CLI process, where the
    server-side watcher in another process is what reaches the UI).
    """
    if _watcher is not None:
        _watcher.note_local_write(connector_id, agent_id, active)
