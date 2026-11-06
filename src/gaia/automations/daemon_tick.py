# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon clock's automations pass: run what is due, queue the output.

Called on every pass of :class:`gaia.daemon.scheduler.clock.DaemonClock`. It
takes the tick (:mod:`.tick`), then runs what is due through the
:class:`~gaia.automations.outbox.OutboxSink`; the Discord bridge delivers the
outbox when it is connected.

With no messaging app paired nothing could ever deliver, so nothing is taken:
what is due stays due, runs once a messaging app is connected, and the daemon
log says why it waits.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, Optional

from gaia.automations import files, outbox, runner, store, tick
from gaia.logger import get_logger

log = get_logger(__name__)

NO_DELIVERER = (
    "Automations are due, but nothing can deliver them: they reach you through "
    "Discord, and it isn't connected. Connect it under Settings → Messaging in "
    "the GAIA app; what is due runs then."
)


def _delivery_ready() -> bool:
    from gaia.messaging.discord import state

    return state.load_config().paired


def _bridge_running() -> bool:
    from gaia.messaging.discord import state

    return state.is_running()


def _check_env() -> Optional[Dict[str, str]]:
    from gaia.messaging.discord.adapter import agent_env

    return agent_env()


class AutomationsTick:
    """One daemon's automations pass; says each new problem once, not every pass."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._said = ""

    def __call__(self, now: Optional[float] = None) -> None:
        if not self._lock.acquire(blocking=False):
            log.info("Previous automations pass still running; skipping this one")
            return
        try:
            self._pass(time.time() if now is None else now)
        finally:
            self._lock.release()

    def _say(self, problem: str) -> None:
        if problem and problem != self._said:
            log.error(problem)
        self._said = problem

    def _pass(self, now: float) -> None:
        try:
            if not tick.claim(tick.OWNER_DAEMON, now):
                raise RuntimeError(
                    "another daemon process holds the automations tick, so this "
                    "one runs none while it does"
                )
            if not any(not a.paused and a.next_at <= now for a in store.snapshot()):
                self._say("")
                return
            if not _delivery_ready():
                self._say(NO_DELIVERER)
                return
            ran = runner.run_due(outbox.OutboxSink(_check_env), now)
        except files.DamagedStateFile as e:
            # The file was moved aside; the user hears it in Discord, once.
            log.error("Automations file was damaged: %s", e)
            outbox.enqueue(
                outbox.Delivery(
                    id=uuid.uuid4().hex[:8],
                    automation_id="",
                    name="automations",
                    kind=outbox.KIND_POST,
                    text=f"⚠️ {e} Ask me to set up your automations again.",
                    due_at=now,
                )
            )
            return
        except (files.StateFileError, OSError, RuntimeError) as e:
            self._say(f"Automations could not run: {e}")
            return
        self._say("")
        if ran:
            waiting = len(outbox.pending())
            if _bridge_running():
                log.info("Ran %d automation(s) for the Discord bridge", len(ran))
            else:
                log.warning(
                    "Ran %d automation(s); %d message(s) wait until the Discord "
                    "bridge starts (Settings → Messaging in the GAIA app)",
                    len(ran),
                    waiting,
                )

    def stop(self) -> None:
        """Give the tick back, so a Discord bridge takes over at once."""
        try:
            tick.release(tick.OWNER_DAEMON)
        except (files.StateFileError, OSError) as e:
            log.error("Could not release the automations tick: %s", e)
