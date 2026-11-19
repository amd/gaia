# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hooks that let low-level code report timings to the running turn's record.

The LLM client sits below the agent and has no handle on its recorder, yet it
is where the waits happen: the model-slot lease, the per-model lock, the
status probe, the backend's own prefill/decode split. These functions read the
recorder from a thread-local, so the client can report into it without an
import of the agent package — and when recording is off (the default) each
call costs one attribute lookup.

The recorder is thread-local on purpose: a tool body or background memory
extraction runs on another thread, and its requests must not be booked as the
turn's own model calls. The recorder is any object with ``note_client``,
``note_server`` and ``add_span`` — in practice
:class:`gaia.agents.base.turn_metrics.TurnRecorder`.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

_ACTIVE = threading.local()


def activate(recorder: Optional[Any]) -> None:
    """Make *recorder* this thread's turn recorder (``None`` clears it)."""
    _ACTIVE.recorder = recorder


def current() -> Optional[Any]:
    """This thread's turn recorder, or ``None`` when recording is off."""
    return getattr(_ACTIVE, "recorder", None)


def note_wait(key: str, seconds: float) -> None:
    """Add *seconds* under ``client.<key>`` on the open model call, if any."""
    recorder = current()
    if recorder is not None:
        recorder.note_client(key, seconds)


def note_server_timings(timings: Optional[Dict[str, Any]]) -> None:
    """Attach the backend's own per-request timings to the open model call."""
    recorder = current()
    if recorder is not None and isinstance(timings, dict):
        recorder.note_server(timings)


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    """Time a named stretch of the turn — a no-op when recording is off."""
    recorder = current()
    if recorder is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        recorder.add_span(name, started, time.perf_counter(), attrs)


__all__ = ["activate", "current", "note_server_timings", "note_wait", "span"]
