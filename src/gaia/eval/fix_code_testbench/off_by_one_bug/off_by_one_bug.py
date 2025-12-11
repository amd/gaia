# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Compute a numeric series."""


def sum(n):
    if n < 0:
        raise ValueError("n must be non-negative")
    total = 0
    for i in range(n):
        total += i
    return total


print(f"Sum through 5: {sum(5)}")
