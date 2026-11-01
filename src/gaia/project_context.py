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
not the signal: DeepSeek has one. ``auto`` therefore gives ``full`` to Claude
served by Anthropic (see :func:`is_hosted_claude`) and ``off`` to every other
model, unknown ones included. A local model named after Claude is not Claude.
"""

from __future__ import annotations

import os
import re
from typing import Optional

PROJECT_CONTEXT_ENV = "GAIA_PROJECT_CONTEXT"

AUTO = "auto"
FULL = "full"
OFF = "off"
PROJECT_CONTEXT_CHOICES = (AUTO, FULL, OFF)

#: ``anthropic/claude-*`` and Bedrock's ``[region.]anthropic.claude-*``. Anchored and
#: with a closed region list, so ``user.`` or ``hf.co/...`` local ids never match.
_ANTHROPIC_HOSTED_ID = re.compile(
    r"^(?:anthropic/|(?:(?:us|eu|apac|global|jp|au|ca|us-gov)\.)?anthropic\.)claude-",
    re.IGNORECASE,
)


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


def is_hosted_claude(model_id: Optional[str]) -> bool:
    """Whether *model_id* names Claude on a provider that serves Anthropic's model.

    True for the AMD gateway's ``amd.Claude-*`` and for ``anthropic/claude-*`` and
    ``[region.]anthropic.claude-*`` ids. Keyed on the provider, never the name:
    ``user.Claude-3.7-Sonnet-Reasoning-Gemma3-12B-GGUF`` is a local model.
    """
    from gaia.llm.gateway import gateway_claude_upstream_id

    if not model_id:
        return False
    return bool(
        gateway_claude_upstream_id(model_id) or _ANTHROPIC_HOSTED_ID.match(model_id)
    )


def wants_full_project_context(
    setting: str, model_id: Optional[str], use_claude: bool = False
) -> bool:
    """Whether a model under *setting* gets the full project context.

    *use_claude* is whether the agent talks to Anthropic's client; a bare
    ``claude-*`` id without it is a local model of that name.
    """
    if setting == FULL:
        return True
    if setting == OFF:
        return False
    # The Anthropic client is Claude whatever the id says.
    return use_claude or is_hosted_claude(model_id)


__all__ = [
    "AUTO",
    "FULL",
    "OFF",
    "PROJECT_CONTEXT_CHOICES",
    "PROJECT_CONTEXT_ENV",
    "ProjectContextError",
    "is_hosted_claude",
    "normalize_project_context",
    "project_context_setting",
    "wants_full_project_context",
]
