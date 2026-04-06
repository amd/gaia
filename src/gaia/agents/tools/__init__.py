# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared tools for GAIA agents.

This package contains tool mixins that can be used across multiple agents.
"""

from .browser_tools import BrowserToolsMixin
from .file_tools import FileSearchToolsMixin
from .filesystem_tools import FileSystemToolsMixin
from .scratchpad_tools import ScratchpadToolsMixin
from .screenshot_tools import ScreenshotToolsMixin

__all__ = [
    "BrowserToolsMixin",
    "FileSearchToolsMixin",
    "FileSystemToolsMixin",
    "ScratchpadToolsMixin",
    "ScreenshotToolsMixin",
]
