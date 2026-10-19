# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A clock for silence limits that does not count time the PC was asleep.

On Windows ``time.monotonic()`` keeps counting while the machine sleeps, so a
limit like "120 s without a byte from the model" fires the instant the PC
wakes from a long sleep, even though the model server (asleep too) is fine and
about to carry on. On Linux and macOS the monotonic clock already stops during
suspend; this clock behaves the same everywhere.

It works by noticing gaps: a clock read every second or so that jumps by more
than :data:`SUSPEND_GAP` means the process was not running for that long, and
the jump is not counted. :func:`shared` is read every second by its own
thread, so it notices a sleep whatever its callers are doing; a private
:class:`AwakeClock` must be read that often by its caller.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

#: A jump between two reads longer than this means the process was suspended.
SUSPEND_GAP = 5.0
#: How often the shared clock's thread reads it.
TICK_SECONDS = 1.0


class AwakeClock:
    """Seconds this process has been running, without the time it slept.

    Callable like ``time.monotonic`` and safe to share between threads. Every
    read, including of :attr:`slept`, also checks for a sleep, so the first
    reader after waking sees it.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        gap: Optional[float] = None,
    ):
        self._clock = clock
        self._gap = SUSPEND_GAP if gap is None else gap
        self._lock = threading.Lock()
        self._last = clock()
        self._slept = 0.0

    def _tick(self) -> float:
        with self._lock:
            now = self._clock()
            step = now - self._last
            if step > self._gap:
                self._slept += step
            self._last = now
            return now - self._slept

    def __call__(self) -> float:
        return self._tick()

    @property
    def slept(self) -> float:
        """Total seconds not counted because the process was asleep."""
        self._tick()
        return self._slept


_shared: Optional[AwakeClock] = None
_shared_lock = threading.Lock()


def shared() -> AwakeClock:
    """The process-wide awake clock over ``time.monotonic``."""
    global _shared  # pylint: disable=global-statement
    with _shared_lock:
        if _shared is None:
            clock = AwakeClock()

            def tick() -> None:
                while True:
                    time.sleep(TICK_SECONDS)
                    clock()

            threading.Thread(target=tick, name="gaia-awake-clock", daemon=True).start()
            _shared = clock
        return _shared


def monotonic() -> float:
    """``time.monotonic()`` without the time the PC slept (the shared clock)."""
    return shared()()
