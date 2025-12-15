# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool mixins for Code Agent."""

from .code_formatting import CodeFormattingMixin

# Consolidated mixins (new architecture)
from .code_tools import CodeToolsMixin
from .error_fixing import ErrorFixingMixin

# External service tools
from .external_tools import ExternalToolsMixin

# Focused mixins (retained)
from .file_io import FileIOToolsMixin
from .project_management import ProjectManagementMixin
from .testing import TestingMixin
from .typescript_tools import TypeScriptToolsMixin
from .validation_parsing import ValidationAndParsingMixin

# Validation tools
from .validation_tools import ValidationToolsMixin
from .web_dev_tools import WebToolsMixin

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
    # TypeScript/Web mixins
    "TypeScriptToolsMixin",
    "WebToolsMixin",
    # External service mixins
    "ExternalToolsMixin",
    # Validation tools
    "ValidationToolsMixin",
]
