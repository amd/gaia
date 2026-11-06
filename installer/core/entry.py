# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Entry of the frozen ``gaia`` executable the desktop app ships (see freeze.py)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from gaia.frozen import dispatch

    status = dispatch(sys.argv[1:])
    if status is None:
        from gaia.cli import main

        status = main()
    sys.exit(status)
