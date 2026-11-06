# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Leave evidence when the Agent UI backend dies hard.

An access violation or abort kills the process before logging runs, so
nothing reaches gaia.log. :func:`enable_fault_log` points Python's
``faulthandler`` at ``~/.gaia/logs/backend-faults.log``, which then gets the
stack of every thread at the moment of the crash.
"""

import faulthandler
import os
import sys
import time
from pathlib import Path
from typing import Optional, TextIO

from gaia.home import gaia_home

FAULT_LOG_NAME = "backend-faults.log"
_MAX_BYTES = 1024 * 1024

# faulthandler writes to this file's descriptor, so it stays open for the process.
_fault_file: Optional[TextIO] = None


def enable_fault_log(logs_dir: Optional[Path] = None) -> Path:
    """Send fatal-crash stacks to the fault log and return its path.

    Raises OSError when the log can't be opened; the caller decides whether
    the backend may start without it.
    """
    global _fault_file  # pylint: disable=global-statement
    logs_dir = logs_dir if logs_dir is not None else gaia_home() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / FAULT_LOG_NAME
    if path.exists() and path.stat().st_size > _MAX_BYTES:
        path.replace(path.with_suffix(".log.1"))
    # pylint: disable-next=consider-using-with
    fault_file = open(path, "a", encoding="utf-8")
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    fault_file.write(
        f"\n--- Agent UI backend pid={os.getpid()} parent={os.getppid()} "
        f"started {started} ({sys.executable}) ---\n"
    )
    fault_file.flush()
    faulthandler.enable(file=fault_file, all_threads=True)
    if _fault_file is not None:
        _fault_file.close()
    _fault_file = fault_file
    return path
