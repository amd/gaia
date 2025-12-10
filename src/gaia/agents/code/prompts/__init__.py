# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Language-specific prompt modules for Code Agent."""

from .python_prompt import get_python_prompt
from .typescript_prompt import get_typescript_prompt

__all__ = [
    "get_python_prompt",
    "get_typescript_prompt",
]
