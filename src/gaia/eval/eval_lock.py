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
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from gaia.daemon.lock import try_lock, unlock

LOCK_FILE = Path(tempfile.gettempdir()) / "gaia-eval.lock"
HOLDER_FILE = LOCK_FILE.with_suffix(".holder.json")
#: Escape hatch for runs that genuinely use separate backends (e.g. unit tests).
BYPASS_ENV = "GAIA_EVAL_NO_LOCK"


def _holder_description() -> str:
    try:
        holder = json.loads(HOLDER_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "another process"
    started = time.strftime("%H:%M", time.localtime(holder.get("started", 0)))
    return f"PID {holder.get('pid')} (`{holder.get('command')}`, started {started})"


@contextlib.contextmanager
def exclusive_eval(command: str):
    """Hold the machine-wide eval lock for the duration of *command*.

    Raises ``SystemExit(2)`` with the holder's details when another eval
    already holds it.
    """
    if os.environ.get(BYPASS_ENV) == "1":
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
        yield
    finally:
        HOLDER_FILE.unlink(missing_ok=True)
        unlock(fd)
        os.close(fd)
