# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A clock for time limits that does not count time the PC was asleep.

On Windows ``time.monotonic()`` keeps counting while the machine sleeps, so a
limit like "120 s without a byte from the model" fires the instant the PC
wakes from a long sleep, even though the model server (asleep too) is fine and
about to carry on. :func:`monotonic` stops while the PC sleeps:

* Windows: ``QueryUnbiasedInterruptTime``, which leaves out S3 sleep and
  hibernation, in 100 ns units at timer-tick resolution (about 16 ms). Modern
  Standby (S0 low-power idle) counts as running time: the PC never leaves S0,
  and on the PC this was measured on, processes kept running through it.
* Linux and macOS: ``time.monotonic()``, which already stops during suspend.

Use it for any limit on how long something may take, so a sleep never reads
as a hang. Callers that take a clock should look this function up at call
time (``awake_clock.monotonic()``), not bind it as a default argument.
"""

from __future__ import annotations

import sys
import time


class AwakeClockError(RuntimeError):
    """The clock that time limits run on could not be read."""


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _query = _kernel32.QueryUnbiasedInterruptTime
    _query.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    _query.restype = wintypes.BOOL

    def monotonic() -> float:
        """Seconds since boot, not counting S3 sleep or hibernation.

        Raises:
            AwakeClockError: Windows refused ``QueryUnbiasedInterruptTime``,
                so no time limit can be measured.
        """
        ticks = ctypes.c_ulonglong()
        if not _query(ctypes.byref(ticks)):
            err = ctypes.WinError(ctypes.get_last_error())
            raise AwakeClockError(
                "GAIA's time-limit clock stopped working: Windows refused "
                f"QueryUnbiasedInterruptTime ({err}). Restart GAIA. If it "
                "happens again, run `gaia diagnostics` and attach the bundle to "
                "an issue at https://github.com/amd/gaia/issues"
            ) from err
        return ticks.value / 10_000_000

else:

    def monotonic() -> float:
        """Seconds since an arbitrary point, not counting time the PC slept."""
        return time.monotonic()
