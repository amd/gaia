# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Marker package so the pre-built Agent UI bundle ships with the wheel.

The Python source under this package is intentionally empty — only the
``dist/`` directory matters at runtime, and it is located via package-relative
paths from ``gaia.ui.server.create_app``.
"""
