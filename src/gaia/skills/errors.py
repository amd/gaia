# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Error types for the skills runtime.

Every error names three things (GAIA's fail-loudly rule): *what failed*, *what
the caller should do*, and *where to look next*. Callers that catch these are
expected to surface the message verbatim — never to substitute a default.
"""

from __future__ import annotations

DOCS_URL = "https://amd-gaia.ai/docs/spec/agent-skills"
FORMAT_DOCS_URL = "https://amd-gaia.ai/docs/plans/skill-format"


class SkillError(Exception):
    """Base class for every skills-runtime failure."""


class SkillValidationError(SkillError):
    """A ``SKILL.md`` is malformed, incomplete, or contradicts its ``tools.py``.

    Raised before anything is registered so a rejected skill never leaves a
    partial load behind.
    """


class SkillNotFoundError(SkillError):
    """No skill of that name exists in any discovery root."""


class SkillPermissionError(SkillError):
    """The skill declares a permission this phase cannot honor.

    Phase 1 bridges only connector-backed domains (``network``, ``mcp``). A
    local-capability domain (``filesystem``, ``shell``, ``database``,
    ``desktop``, ``env``) needs the Phase 2 sandbox, so the skill is refused
    outright rather than loaded without enforcement.
    """
