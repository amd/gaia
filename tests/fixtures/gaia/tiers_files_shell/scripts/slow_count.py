# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Eval fixture: a deliberately slow script (about 5 seconds)."""

import time

for tick in range(1, 6):
    print(f"tick {tick}", flush=True)
    time.sleep(1)
print("DONE after 5 ticks")
