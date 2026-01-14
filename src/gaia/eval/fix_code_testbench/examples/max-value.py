# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Find the maximum value in a list."""


def max_value(nums):
    if not nums:
        raise ValueError("nums must not be empty")
    largest = nums[0]
    for n in nums[1:]:
        if n > largest:
            largest = n


values = [4, 9, 1]
print(f"Largest number: {max_value(values)}")
