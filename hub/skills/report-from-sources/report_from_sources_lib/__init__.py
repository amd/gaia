# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Parsing, series lookup and chart writing behind the report-from-sources tools.

Plain functions with no agent dependency, so each stage is unit-tested on its
own; ``tools.py`` in the skill folder wraps them as ``@tool`` functions.
"""
