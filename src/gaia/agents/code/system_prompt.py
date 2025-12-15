# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
System prompt configuration for the Code Agent.

This module orchestrates loading the appropriate language-specific prompts
based on the language and project_type provided by RoutingAgent.
"""

from typing import Optional

from .prompts.base_prompt import get_base_prompt
from .prompts.nextjs_prompt import NEXTJS_PROMPT
from .prompts.python_prompt import get_python_prompt


def get_system_prompt(
    language: str = "python",
    project_type: str = "script",  # pylint: disable=unused-argument
    gaia_md_path: Optional[str] = None,
) -> str:
    """Get the appropriate system prompt based on language and project type.

    Language and project type are determined by RoutingAgent before CodeAgent
    is instantiated, so this function no longer performs detection.

    Args:
        language: Programming language ('python' or 'typescript')
        project_type: Project type ('frontend', 'backend', 'fullstack', or 'script')
        gaia_md_path: Optional path to GAIA.md file for project context

    Returns:
        Complete system prompt string (base + language-specific instructions)
    """
    if language == "typescript":
        # All TypeScript projects use Next.js unified approach
        base = get_base_prompt(gaia_md_path)
        return base + NEXTJS_PROMPT
    else:
        # Python prompt (default)
        return get_python_prompt(gaia_md_path)
