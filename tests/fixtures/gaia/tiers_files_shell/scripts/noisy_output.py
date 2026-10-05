# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Eval fixture: floods stdout, with the one line that matters printed last."""

for n in range(1, 50001):
    print(f"line {n:05d} filler text that only exists to make the output large")
print("FINAL_CHECKSUM=8a1f-2291")
