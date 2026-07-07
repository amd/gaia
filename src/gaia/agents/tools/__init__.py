# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared tools for GAIA agents.

This package contains tool mixins that can be used across multiple agents.
"""

from .browser_tools import BrowserToolsMixin
from .code_index_tools import CodeIndexToolsMixin
from .file_io_tools import FileIOToolsMixin
from .file_monitor_tools import FileToolsMixin
from .file_tools import FileSearchToolsMixin
from .filesystem_tools import FileSystemToolsMixin
from .rag_tools import RAGToolsMixin
from .scratchpad_tools import ScratchpadToolsMixin
from .screenshot_tools import ScreenshotToolsMixin
from .shell_tools import ShellToolsMixin

__all__ = [
    "BrowserToolsMixin",
    "CodeIndexToolsMixin",
    "FileIOToolsMixin",
    "FileSearchToolsMixin",
    "FileToolsMixin",
    "FileSystemToolsMixin",
    "RAGToolsMixin",
    "ScratchpadToolsMixin",
    "ScreenshotToolsMixin",
    "ShellToolsMixin",
]
