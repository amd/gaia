# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The one line every chat turn waits in for the model (#5226, #5040).

One local GPU runs one turn well; two at once slow each other down and
evict each other's prompt cache. The agent's tool registry is also
process-global, so turns must not overlap. ``TurnQueue`` admits one turn
at a time, first come first served, and — unlike the bare semaphore it
replaces — knows who is waiting and in what order, so the UI can show
"2nd in line" instead of two chats quietly competing.

All methods run on the server's event loop; no extra locking is needed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


class TurnCancelled(Exception):
    """A waiting turn was withdrawn (Stop, delete) before it reached the model."""


@dataclass
class _Waiter:
    owner: str
    future: asyncio.Future
    on_position: Optional[Callable[[int], None]] = None
    position: int = field(default=0)
    # Stopped after the slot was handed over but before the waiter resumed.
    withdrawn: bool = False


class TurnQueue:
    """FIFO admission for chat turns: one holder, an ordered line behind it."""

    def __init__(self) -> None:
        self._holder: Optional[str] = None
        self._waiters: List[_Waiter] = []
        # The waiter just given the slot, until its coroutine resumes.
        self._handed: Optional[_Waiter] = None

    # ── Introspection ─────────────────────────────────────────────────
    @property
    def holder(self) -> Optional[str]:
        return self._holder

    def locked(self) -> bool:
        return self._holder is not None or bool(self._waiters)

    def waiting(self) -> List[str]:
        """Owners in line, first to run next first."""
        return [w.owner for w in self._waiters]

    def position(self, owner: str) -> int:
        """1-based place in line, or 0 when *owner* is not waiting."""
        for i, w in enumerate(self._waiters, start=1):
            if w.owner == owner:
                return i
        return 0

    # ── Admission ─────────────────────────────────────────────────────
    async def acquire(
        self,
        owner: str,
        on_position: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Wait for the model, then hold it until :meth:`release`.

        ``on_position`` is called with the 1-based place in line whenever it
        changes while waiting; never when the slot is free on arrival.

        Raises:
            TurnCancelled: the wait was withdrawn with :meth:`cancel`.
        """
        if self._holder is None and not self._waiters:
            self._holder = owner
            return
        loop = asyncio.get_running_loop()
        waiter = _Waiter(owner, loop.create_future(), on_position)
        self._waiters.append(waiter)
        self._announce()
        try:
            await waiter.future
            if waiter.withdrawn:
                raise TurnCancelled(owner)
        except TurnCancelled:
            if self._handed is waiter:
                self._handed = None
                self.release()
            raise
        except BaseException:
            # Cancelled or withdrawn: leave the line, and never strand the slot
            # if it was handed over in the same tick.
            if waiter in self._waiters:
                self._waiters.remove(waiter)
                self._announce()
            elif (
                self._holder == owner
                and waiter.future.done()
                and (
                    not waiter.future.cancelled() and waiter.future.exception() is None
                )
            ):
                self._handed = None
                self.release()
            raise
        finally:
            if self._handed is waiter:
                self._handed = None

    def release(self) -> None:
        """Hand the model to the next turn in line."""
        if self._holder is None:
            raise RuntimeError("TurnQueue.release() called with no turn holding it")
        self._holder = None
        while self._waiters:
            nxt = self._waiters.pop(0)
            if nxt.future.done():
                continue
            self._holder = nxt.owner
            self._handed = nxt
            nxt.future.set_result(None)
            break
        self._announce()

    def cancel(self, owner: str) -> bool:
        """Withdraw *owner* from the line. False when it was not waiting."""
        handed = self._handed
        if handed is not None and handed.owner == owner and not handed.withdrawn:
            handed.withdrawn = True
            return True
        for w in self._waiters:
            if w.owner == owner and not w.future.done():
                self._waiters.remove(w)
                w.future.set_exception(TurnCancelled(owner))
                self._announce()
                return True
        return False

    def _announce(self) -> None:
        for i, w in enumerate(self._waiters, start=1):
            if w.position == i:
                continue
            w.position = i
            if w.on_position is None:
                continue
            try:
                w.on_position(i)
            except Exception:  # pylint: disable=broad-except
                # A broken listener must not wedge the line for everyone else.
                logger.exception("Turn-queue position callback failed for %s", w.owner)


def ordinal(n: int) -> str:
    """1 → '1st', 2 → '2nd', 11 → '11th'."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# Process-wide: one model backend per process, so one line.
turn_queue = TurnQueue()
