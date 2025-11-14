# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool mixins for Code Agent."""

from .code_formatting import CodeFormattingMixin

# Consolidated mixins (new architecture)
from .code_tools import CodeToolsMixin
from .error_fixing import ErrorFixingMixin

# Focused mixins (retained)
from .file_io import FileIOToolsMixin
from .project_management import ProjectManagementMixin
from .testing import TestingMixin
from .validation_parsing import ValidationAndParsingMixin

__all__ = [
    # New consolidated mixins
    "CodeToolsMixin",
    "ValidationAndParsingMixin",
    # Focused mixins
    "FileIOToolsMixin",
    "CodeFormattingMixin",
    "ProjectManagementMixin",
    "TestingMixin",
    "ErrorFixingMixin",
]
