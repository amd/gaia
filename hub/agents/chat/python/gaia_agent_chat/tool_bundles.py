# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deprecated alias of :mod:`gaia_agent.tool_bundles`; removed in the next release."""

import sys

import gaia_agent.tool_bundles as _target

sys.modules[__name__] = _target
