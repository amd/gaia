# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Eval fixture: needs interactive input, so a non-interactive run fails."""

name = input("What is your name? ")
print(f"Hello, {name}! Your ticket number is 4471.")
