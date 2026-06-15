# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Language-specific prompt modules for Code Agent."""

from .nextjs_prompt import NEXTJS_PROMPT
from .python_prompt import get_python_prompt

__all__ = [
    "get_python_prompt",
    "NEXTJS_PROMPT",
]
