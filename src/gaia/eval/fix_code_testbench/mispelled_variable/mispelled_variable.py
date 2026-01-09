# flake8: noqa
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Simple average calculator script."""


def average(nums):
    total = 0
    for n in nums:
        total += n
    return total / len(nums)


numbers = [10, 20, 30]
print(f"Average: {average(number)}")
