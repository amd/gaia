# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The tool-registry isolation the flagship's tests share."""

import contextlib

from gaia.agents.base.tools import _TOOL_REGISTRY


@contextlib.contextmanager
def isolated_registry():
    """@tool writes into a process-global dict shared with every other agent."""
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
