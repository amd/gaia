# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One model-driving eval per machine at a time.

Two evals against one Lemonade evict each other's models (every task pays a
reload and loses its prompt cache) and slow each other's GPU work, so both
runs' scores and timings are wrong. CLAUDE.md makes this a rule; this lock
enforces it for ``gaia eval agent`` and ``gaia eval tasks run``.

The OS releases the lock when its holder dies, so a crashed run never leaves
a stale lock. The holder's details live in a separate file because Windows
refuses reads of a locked byte range.

While the lock is held the machine is kept awake. On a Modern Standby PC an
idle screen timeout puts the system into standby, where a local model's GPU
work stalls: a run left unattended scores a machine that was asleep.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from gaia.daemon.lock import try_lock, unlock

LOCK_FILE = Path(tempfile.gettempdir()) / "gaia-eval.lock"
HOLDER_FILE = LOCK_FILE.with_suffix(".holder.json")
#: Escape hatch for runs that genuinely use separate backends (e.g. unit tests).
BYPASS_ENV = "GAIA_EVAL_NO_LOCK"
#: A heartbeat late by more than this means the machine was suspended.
SLEEP_GAP_S = 60.0
_HEARTBEAT_S = 5.0


def _holder_description() -> str:
    try:
        holder = json.loads(HOLDER_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "another process"
    started = time.strftime("%H:%M", time.localtime(holder.get("started", 0)))
    return f"PID {holder.get('pid')} (`{holder.get('command')}`, started {started})"


#: SetThreadExecutionState flags: keep the system and display on until cleared.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


@contextlib.contextmanager
def _stay_awake():
    """Hold off idle standby for this thread while a run is in progress."""
    if sys.platform != "win32":
        yield
        return
    import ctypes

    set_state = ctypes.windll.kernel32.SetThreadExecutionState
    if not set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED):
        raise OSError(
            "Could not ask Windows to stay awake for the eval (SetThreadExecutionState "
            "failed). An unattended run would stall when the PC goes into standby; "
            "keep it awake another way (e.g. powercfg) and retry."
        )
    try:
        yield
    finally:
        set_state(_ES_CONTINUOUS)


@contextlib.contextmanager
def _sleep_watch(command: str):
    """Fail the run if the machine was suspended while it ran.

    A suspended process sees wall-clock time jump between two ticks. Timings
    and timeouts inside such a run are meaningless, so its results must not
    be recorded as a measurement.
    """
    gaps: list[tuple[float, float]] = []
    stop = threading.Event()

    def beat():
        last = time.time()
        while not stop.wait(_HEARTBEAT_S):
            now = time.time()
            if now - last > SLEEP_GAP_S:
                gaps.append((last, now))
            last = now

    thread = threading.Thread(target=beat, name="eval-sleep-watch", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
    if gaps:
        spans = ", ".join(
            f"{time.strftime('%H:%M:%S', time.localtime(a))}-"
            f"{time.strftime('%H:%M:%S', time.localtime(b))}"
            for a, b in gaps
        )
        print(
            f"[ERROR] The machine was suspended during `{command}` ({spans}). "
            "Local-model work stalls while suspended, so this run's scores and "
            "timings are not a valid measurement. Keep the PC awake (on Windows: "
            "`powercfg /change monitor-timeout-ac 0` and "
            "`powercfg /change standby-timeout-ac 0`) and rerun.",
            file=sys.stderr,
        )
        raise SystemExit(3)


@contextlib.contextmanager
def exclusive_eval(command: str):
    """Hold the machine-wide eval lock for the duration of *command*.

    Raises ``SystemExit(2)`` with the holder's details when another eval
    already holds it, and ``SystemExit(3)`` when the machine was suspended
    while *command* ran.
    """
    if os.environ.get(BYPASS_ENV) == "1":
        with _stay_awake(), _sleep_watch(command):
            yield
        return

    fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    if not try_lock(fd):
        os.close(fd)
        print(
            f"[ERROR] Another eval is already running on this machine: "
            f"{_holder_description()}.\n"
            "        Two evals share one Lemonade: they evict each other's models "
            "and slow each other down, so both runs' scores and timings are wrong.\n"
            "        Wait for it to finish or stop it, then retry. To run anyway "
            f"against a separate backend, set {BYPASS_ENV}=1.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    HOLDER_FILE.write_text(
        json.dumps({"pid": os.getpid(), "command": command, "started": time.time()}),
        encoding="utf-8",
    )
    try:
        with _stay_awake(), _sleep_watch(command):
            yield
    finally:
        HOLDER_FILE.unlink(missing_ok=True)
        unlock(fd)
        os.close(fd)
