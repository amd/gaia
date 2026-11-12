# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Join the threads a test started, or name the ones that won't stop (#2786).

A daemon worker that outlives its test can still be writing to stderr when the
interpreter shuts down, which crashes CI with ``_enter_buffered_busy`` after
every test has passed. Uniquely named for the reason given in
``onboarding_fakes.py``.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable, List

#: Long enough for a worker finishing its last event; short enough to stay a test failure.
THREAD_JOIN_SECONDS = 10.0


def threads_left_running(
    before: Iterable[threading.Thread], timeout: float = THREAD_JOIN_SECONDS
) -> List[str]:
    """Join every thread started since *before* within one shared *timeout*;
    return the names still alive."""
    known = set(before)
    started = [t for t in threading.enumerate() if t not in known]
    deadline = time.monotonic() + timeout
    for thread in started:
        if thread is not threading.current_thread():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return [t.name for t in started if t.is_alive()]
