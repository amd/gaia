# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Compatibility shim — the mixin moved to the framework.

:class:`SkillLibraryToolsMixin` is general capability ("let the model manage
its own skill library"), not gaia-agent-specific code, so it lives in
``gaia.agents.tools.skill_library_tools`` and is composable by name
(``KNOWN_TOOLS["skills"]``) like every other tool mixin. This module keeps the
old import path working.
"""

from gaia.agents.tools.skill_library_tools import (  # noqa: F401
    _LIST_DESCRIPTION_CHARS,
    SKILL_LIBRARY_TOOL_NAMES,
    SkillLibraryToolsMixin,
    _summarize,
    estimate_prompt_tokens,
)
