# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A simulated wall clock for replaying months of chats in minutes.

Memory reads the time only through ``datetime.now()`` in ``memory.py`` and
``memory_store.py``: timestamps, consolidation age, forget-vs-said ordering,
reminder windows. Swapping that name in those two modules for a subclass whose
``now()`` is shifted lets a scenario put each session on its own date, while
time still moves forward inside a session (so ordering within it is real).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime as _RealDatetime
from datetime import timedelta
from typing import Iterator

import gaia.agents.base.memory as _memory
import gaia.agents.base.memory_store as _memory_store

_PATCHED_MODULES = (_memory, _memory_store)


class _SimDatetime(_RealDatetime):
    """``datetime`` whose ``now()`` is the real time plus a settable offset."""

    _offset = timedelta(0)
    _lock = threading.Lock()

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        with cls._lock:
            offset = cls._offset
        # Shift an aware instant, then convert: shifting naive local time across
        # a daylight-saving change would land an hour off.
        instant = _RealDatetime.now().astimezone() + offset
        shifted = (
            instant.astimezone(tz)
            if tz is not None
            else instant.astimezone().replace(tzinfo=None)
        )
        return cls(
            shifted.year,
            shifted.month,
            shifted.day,
            shifted.hour,
            shifted.minute,
            shifted.second,
            shifted.microsecond,
            tzinfo=shifted.tzinfo,
        )


class SimClock:
    """Moves memory's notion of "now" to a chosen moment, then lets it run."""

    def start(self, moment: _RealDatetime) -> None:
        """Begin a scenario at *moment*, wherever the clock was before."""
        if moment.tzinfo is None:
            moment = moment.astimezone()
        with _SimDatetime._lock:
            _SimDatetime._offset = moment - _RealDatetime.now().astimezone()

    def set(self, moment: _RealDatetime) -> None:
        """Make ``datetime.now()`` read *moment* from this instant on.

        A naive *moment* is local time. Moving backwards is refused: memory
        compares timestamps, and a clock that runs back would make a later
        session look older than an earlier one.
        """
        if moment.tzinfo is None:
            moment = moment.astimezone()
        current = _SimDatetime.now().astimezone()
        if moment < current - timedelta(seconds=1):
            raise ValueError(
                f"simulated clock cannot run backwards: {moment.isoformat()} is "
                f"before {current.isoformat()}; order the scenario's sessions by date"
            )
        with _SimDatetime._lock:
            _SimDatetime._offset = moment - _RealDatetime.now().astimezone()

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward by *delta*."""
        if delta < timedelta(0):
            raise ValueError("simulated clock cannot run backwards")
        with _SimDatetime._lock:
            _SimDatetime._offset += delta

    @staticmethod
    def now() -> _RealDatetime:
        return _SimDatetime.now().astimezone()


@contextmanager
def simulated_time() -> Iterator[SimClock]:
    """Patch memory's clock for the duration of a replay, then restore it."""
    saved = [module.datetime for module in _PATCHED_MODULES]
    with _SimDatetime._lock:
        _SimDatetime._offset = timedelta(0)
    for module in _PATCHED_MODULES:
        module.datetime = _SimDatetime
    try:
        yield SimClock()
    finally:
        for module, original in zip(_PATCHED_MODULES, saved):
            module.datetime = original
        with _SimDatetime._lock:
            _SimDatetime._offset = timedelta(0)
