# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How much of a repository's own guidance each model is shown.

``full`` puts the project's AGENTS.md / CLAUDE.md in the system prompt, lists
its ``.claude/skills`` in the skill catalogue, and adds the flagship's
repository-work section. ``off`` adds none of it, so the prompt is exactly the
one GAIA sends with no project guidance at all.

The level follows the model, because the payoff does. On 30 repo-work tasks
Claude Opus gained about five points from the full context, while DeepSeek V4.1
Flash gained nothing and paid 55% more input tokens. A large context window is
not the signal: DeepSeek has one. ``auto`` therefore gives ``full`` to the
families in :data:`FULL_CONTEXT_FAMILIES` and ``off`` to every other model,
unknown ones included.
"""

from __future__ import annotations

import os
from typing import Optional

PROJECT_CONTEXT_ENV = "GAIA_PROJECT_CONTEXT"

AUTO = "auto"
FULL = "full"
OFF = "off"
PROJECT_CONTEXT_CHOICES = (AUTO, FULL, OFF)

#: Model families measured to gain from the full project context. Add one only
#: with an A/B that shows the gain is worth its tokens; unlisted models get ``off``.
FULL_CONTEXT_FAMILIES = ("claude",)


class ProjectContextError(ValueError):
    """A ``project_context`` setting that is not one of the choices."""


def normalize_project_context(value: object, where: str, remedy: str) -> str:
    """*value* as one of :data:`PROJECT_CONTEXT_CHOICES`; empty means ``auto``.

    *remedy* says how to fix it where the value came from.
    """
    word = str(value if value is not None else "").strip().lower()
    if not word:
        return AUTO
    if word not in PROJECT_CONTEXT_CHOICES:
        raise ProjectContextError(
            f"{where}: project_context must be one of "
            f"{', '.join(PROJECT_CONTEXT_CHOICES)}, but it is {value!r}. "
            "'auto' picks per model, 'full' always shows the repository's "
            f"CLAUDE.md, AGENTS.md and skills, 'off' never does. {remedy}"
        )
    return word


def project_context_setting(explicit: Optional[str] = None) -> str:
    """The user's choice: *explicit*, else ``GAIA_PROJECT_CONTEXT``, else config.json."""
    if explicit is not None:
        return normalize_project_context(
            explicit, "project_context", "Pass one of those."
        )
    raw = os.environ.get(PROJECT_CONTEXT_ENV)
    if raw is not None:
        return normalize_project_context(
            raw,
            PROJECT_CONTEXT_ENV,
            f"Set {PROJECT_CONTEXT_ENV} to one of those, or unset it.",
        )
    from gaia.config import GaiaConfig

    # GaiaConfig.load has already validated the value.
    return GaiaConfig.load().project_context or AUTO


def model_family(model_id: Optional[str]) -> str:
    """*model_id*'s first name segment, lowercased, without a provider prefix.

    ``claude-opus-5``, ``amd.Claude-Opus-5-5``, ``anthropic/claude-sonnet-5`` and
    ``us.anthropic.claude-sonnet-5-v1:0`` are all ``claude``;
    ``Qwen3.6-35B-A3B-GGUF`` is ``qwen3``.
    """
    name = (model_id or "").strip().lower()
    name = name.rsplit("/", 1)[-1]
    while True:
        head, dot, rest = name.partition(".")
        if not (dot and rest and head.isalpha()):
            break
        name = rest
    family = ""
    for char in name:
        if not char.isalnum():
            break
        family += char
    return family


def wants_full_project_context(
    setting: str, model_id: Optional[str], use_claude: bool = False
) -> bool:
    """Whether a model under *setting* gets the full project context."""
    if setting == FULL:
        return True
    if setting == OFF:
        return False
    if use_claude:
        return True
    return model_family(model_id) in FULL_CONTEXT_FAMILIES


__all__ = [
    "AUTO",
    "FULL",
    "FULL_CONTEXT_FAMILIES",
    "OFF",
    "PROJECT_CONTEXT_CHOICES",
    "PROJECT_CONTEXT_ENV",
    "ProjectContextError",
    "model_family",
    "normalize_project_context",
    "project_context_setting",
    "wants_full_project_context",
]
