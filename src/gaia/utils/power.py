# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep the machine awake while GAIA is doing work.

On a Modern Standby PC an idle screen timeout puts the system into standby,
where a local model's GPU work stalls: a long agent turn left unattended
freezes until someone touches the machine. ``stay_awake()`` holds the same
request a video player does for as long as the work runs, and releases it
after, so the PC's own power settings apply again once GAIA is idle.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading

log = logging.getLogger(__name__)

#: SetThreadExecutionState flags: keep the system and display on until cleared.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002

# The request is per thread, and clearing it clears every holder on that
# thread, so nested holders are counted and only the outermost clears it.
_depth = threading.local()


@contextlib.contextmanager
def stay_awake(required: bool = False):
    """Hold off idle standby on this thread while the block runs.

    ``required=True`` raises when Windows refuses the request — for a
    measurement that is meaningless if the machine sleeps. Otherwise a refusal
    is logged and the work goes ahead, since a chat turn is still worth
    answering on a PC that may later sleep. A no-op off Windows.
    """
    if sys.platform != "win32":
        yield
        return
    depth = getattr(_depth, "n", 0)
    if depth == 0:
        import ctypes  # pylint: disable=import-outside-toplevel

        set_state = ctypes.windll.kernel32.SetThreadExecutionState
        if not set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED):
            message = (
                "Could not ask Windows to stay awake (SetThreadExecutionState "
                "failed); the PC may go into standby and stall local-model work. "
                "Keep it awake with `powercfg /change monitor-timeout-ac 0` and "
                "`powercfg /change standby-timeout-ac 0`."
            )
            if required:
                raise OSError(message)
            log.warning(message)
            yield
            return
    _depth.n = depth + 1
    try:
        yield
    finally:
        _depth.n -= 1
        if _depth.n == 0:
            import ctypes  # pylint: disable=import-outside-toplevel

            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
