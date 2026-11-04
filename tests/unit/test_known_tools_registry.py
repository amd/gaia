# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Registry coverage for composable tool mixins."""

import importlib

from gaia.agents.registry import KNOWN_TOOLS


def test_file_monitor_is_a_composable_tool_mixin():
    """The directory-watching mixin is reachable by name from KNOWN_TOOLS."""
    module_path, class_name = KNOWN_TOOLS["file_monitor"]

    assert module_path == "gaia.agents.tools.file_monitor_tools"
    assert class_name == "FileToolsMixin"

    module = importlib.import_module(module_path)
    mixin = getattr(module, class_name)
    assert hasattr(mixin, "register_file_tools")
