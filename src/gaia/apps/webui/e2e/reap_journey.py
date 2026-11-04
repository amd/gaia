# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Kill every process still working inside one journey's folder, then exit.

The GAIA daemon and its agent sidecars start in their own session so they
outlive whoever started them, so killing the backend's group leaves them
running, still writing into the journey's home while the harness deletes it.
Each of them runs with its cwd inside that folder; that is how they are found.

Usage: python reap_journey.py <journey root>. Exits 1 naming any survivor.
"""

import os
import sys
import time

import psutil

DEADLINE_S = 15


def _inside(proc: psutil.Process, root: str) -> bool:
    try:
        cwd = os.path.normcase(os.path.realpath(proc.cwd()))
    except (psutil.Error, OSError):
        return False
    return cwd == root or cwd.startswith(root + os.sep)


def _describe(proc: psutil.Process) -> str:
    try:
        return f"{proc.pid} ({' '.join(proc.cmdline())})"
    except psutil.Error:
        return str(proc.pid)


def main() -> int:
    root = os.path.normcase(os.path.realpath(sys.argv[1]))
    me = os.getpid()
    deadline = time.monotonic() + DEADLINE_S
    while True:
        procs = [p for p in psutil.process_iter() if p.pid != me and _inside(p, root)]
        if not procs:
            return 0
        if time.monotonic() > deadline:
            print(
                f"still running in {root}: {', '.join(_describe(p) for p in procs)}",
                file=sys.stderr,
            )
            return 1
        for p in procs:
            print(f"stopped {_describe(p)}")
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(procs, timeout=5)


if __name__ == "__main__":
    sys.exit(main())
