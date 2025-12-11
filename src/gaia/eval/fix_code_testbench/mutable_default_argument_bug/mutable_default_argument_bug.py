# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Append values to a list accumulator."""


def append_value(value, bucket=[]):
    bucket.append(value)
    return bucket


print(append_value("first"))
print(append_value("second"))
