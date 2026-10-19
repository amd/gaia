# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A clock for time limits that does not count time the PC was asleep.

On Windows ``time.monotonic()`` keeps counting while the machine sleeps, so a
limit like "120 s without a byte from the model" fires the instant the PC
wakes from a long sleep, even though the model server (asleep too) is fine and
about to carry on. :func:`monotonic` stops while the PC sleeps:

* Windows: ``QueryUnbiasedInterruptTime``, which excludes sleep and
  hibernation (Windows 7 and later), in 100 ns units at timer-tick resolution
  (about 16 ms).
* Linux and macOS: ``time.monotonic()``, which already stops during suspend.

Use it for any limit on how long something may take, so a sleep never reads
as a hang. Callers that take a clock should look this function up at call
time (``awake_clock.monotonic()``), not bind it as a default argument.
"""

from __future__ import annotations

import sys
import time

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _query = _kernel32.QueryUnbiasedInterruptTime
    _query.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    _query.restype = wintypes.BOOL

    def monotonic() -> float:
        """Seconds since boot, not counting time the PC slept.

        Raises:
            OSError: Windows refused the call (it never does on Windows 7+).
        """
        ticks = ctypes.c_ulonglong()
        if not _query(ctypes.byref(ticks)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ticks.value / 10_000_000

else:

    def monotonic() -> float:
        """Seconds since an arbitrary point, not counting time the PC slept."""
        return time.monotonic()
