# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Registry coverage for the directory-watching FileToolsMixin."""

import importlib

from gaia.agents.registry import KNOWN_TOOLS


class TestRegistryEntry:
    def test_file_monitor_resolves_to_file_tools_mixin(self):
        """The directory-watching mixin is reachable by name from KNOWN_TOOLS."""
        module_path, class_name = KNOWN_TOOLS["file_monitor"]

        assert module_path == "gaia.agents.tools.file_monitor_tools"
        assert class_name == "FileToolsMixin"

        module = importlib.import_module(module_path)
        mixin = getattr(module, class_name)
        assert hasattr(mixin, "register_file_tools")
