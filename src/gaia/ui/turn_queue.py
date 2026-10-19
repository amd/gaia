# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The one line every chat turn waits in for the model (#5226, #5040).

One local GPU runs one turn well; two at once slow each other down and
evict each other's prompt cache. ``TurnQueue`` admits one turn at a time,
first come first served, and — unlike the bare semaphore it replaces —
knows who is waiting and in what order, so the UI can show "2nd in line"
instead of two chats quietly competing.

The line is for the model, not for the whole turn. A turn paused on the user
(a permission prompt, a question) gives its place up and comes back at the
front of the line once answered, so an unanswered prompt in a background chat
never holds every other chat. The other shared state has its own guard: the
process-global tool registry is written only under ``tool_registry_lock`` in
``_chat_helpers``, and another process's use of the model is arbitrated by the
daemon's broker lease.

``TurnQueue`` methods run on the server's event loop; no extra locking is
needed. ``TurnSlot`` is the one part a turn's worker thread may call.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
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
    # Resuming a turn that already started: served before new turns.
    front: bool = False


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
        *,
        front: bool = False,
    ) -> None:
        """Wait for the model, then hold it until :meth:`release`.

        ``on_position`` is called with the 1-based place in line whenever it
        changes while waiting; never when the slot is free on arrival.
        ``front`` is for a turn coming back from a pause on the user: it goes
        ahead of new turns, behind any turn that came back earlier.

        Raises:
            TurnCancelled: the wait was withdrawn with :meth:`cancel`.
        """
        if self._holder is None and not self._waiters:
            self._holder = owner
            return
        loop = asyncio.get_running_loop()
        waiter = _Waiter(owner, loop.create_future(), on_position, front=front)
        if front:
            at = next(
                (i for i, w in enumerate(self._waiters) if not w.front),
                len(self._waiters),
            )
            self._waiters.insert(at, waiter)
        else:
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


class TurnSlot:
    """One turn's place on the model, which its worker thread can hand back.

    The turn's coroutine takes the slot with :meth:`acquire` and gives it back
    with :meth:`release`, both on the event loop. While the agent's thread
    waits on the user it calls :meth:`step_away`, then :meth:`come_back` once
    answered; those two are the thread-safe half.
    """

    # How long the worker thread waits for the event loop to run a hand-back.
    _LOOP_CALL_TIMEOUT_S = 10.0

    def __init__(
        self,
        queue: TurnQueue,
        owner: str,
        loop: asyncio.AbstractEventLoop,
        on_position: Optional[Callable[[int], None]] = None,
        on_resume: Optional[Callable[[], None]] = None,
    ) -> None:
        self._queue = queue
        self._owner = owner
        self._loop = loop
        self._on_position = on_position
        self._on_resume = on_resume
        # Read and written on the event loop only.
        self.held = False
        # Set once the turn is over: a late come_back must not retake the model.
        self.closed = False

    async def acquire(self, *, front: bool = False) -> None:
        """Wait in line, then hold the model (event loop only)."""
        await self._queue.acquire(self._owner, self._on_position, front=front)
        self.held = True

    def release(self) -> bool:
        """Give the model to the next turn; False when not holding it (event loop only)."""
        if not self.held:
            return False
        self.held = False
        self._queue.release()
        return True

    def close(self) -> None:
        """End the turn's hold for good (event loop only)."""
        self.closed = True
        self.release()

    def step_away(self) -> bool:
        """Give the model up while the turn waits on the user (worker thread).

        Returns whether the slot was held, i.e. whether :meth:`come_back` has
        anything to take back.
        """
        return self._call_in_loop(self.release)

    def come_back(self, cancelled: threading.Event) -> bool:
        """Retake the model at the front of the line (worker thread).

        Blocks until admitted, or returns False soon after *cancelled* is set.
        """
        future = asyncio.run_coroutine_threadsafe(self._resume(), self._loop)
        while True:
            try:
                future.result(timeout=0.2)
                return True
            except concurrent.futures.TimeoutError:
                if cancelled.is_set():
                    future.cancel()
            except (concurrent.futures.CancelledError, TurnCancelled):
                return False

    async def _resume(self) -> None:
        if self.closed:
            raise TurnCancelled(self._owner)
        waited = self._queue.locked()
        await self.acquire(front=True)
        if self.closed:
            self.release()
            raise TurnCancelled(self._owner)
        if waited and self._on_resume is not None:
            self._on_resume()

    def _call_in_loop(self, fn: Callable[[], bool]) -> bool:
        done: concurrent.futures.Future = concurrent.futures.Future()

        def run() -> None:
            try:
                done.set_result(fn())
            except BaseException as exc:  # re-raised on the calling thread
                done.set_exception(exc)

        self._loop.call_soon_threadsafe(run)
        try:
            return done.result(timeout=self._LOOP_CALL_TIMEOUT_S)
        except concurrent.futures.TimeoutError as exc:
            # A separate class from the builtin before Python 3.11.
            raise TimeoutError(
                f"The chat server's event loop did not answer within "
                f"{self._LOOP_CALL_TIMEOUT_S:g} s while turn {self._owner} "
                "handed back the model"
            ) from exc


def ordinal(n: int) -> str:
    """1 → '1st', 2 → '2nd', 11 → '11th'."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# Process-wide: one model backend per process, so one line.
turn_queue = TurnQueue()
