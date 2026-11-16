# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cap OpenBLAS's thread pool before numpy or faiss loads it.

numpy and faiss each bundle OpenBLAS, which commits a buffer for every thread
in its pool when it loads, and sizes that pool to the hardware: on a 32-thread
PC that is about 5 GB of committed memory per process for 0.3 GB in use, and a
dozen GAIA processes exhaust the commit limit (#7291). The pool size is read
once, at load, so the cap must be in the environment before the first import.

GAIA's searches are single queries that faiss runs on its own OpenMP pool, which
commits almost nothing, so that pool is left at full width.

Stdlib only. ``gaia/__init__.py`` calls :func:`cap_blas_threads` first thing,
and the freeze scripts run this file as a PyInstaller runtime hook, so it also
runs before a frozen entry script imports anything.
"""

import os

#: OpenBLAS threads per GAIA process; see docs/reference/troubleshooting.mdx.
BLAS_THREADS = 2

# OpenBLAS takes its pool size from the first of these that is set, so a user's
# value under any of them must stand.
OPENBLAS_THREAD_VARS = ("OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS", "OMP_NUM_THREADS")


def cap_blas_threads(environ=None) -> bool:
    """Set ``OPENBLAS_NUM_THREADS`` unless the user already chose a pool size.

    Returns True when the cap was applied.
    """
    env = os.environ if environ is None else environ
    if any(env.get(name) for name in OPENBLAS_THREAD_VARS):
        return False
    env["OPENBLAS_NUM_THREADS"] = str(BLAS_THREADS)
    return True


if __name__ == "__main__":
    cap_blas_threads()
