# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Changing which skills are trusted is the user's step, never an agent's.

Every process an agent tool starts carries :data:`AGENT_CHILD_ENV`, and its own
children inherit it. Code an agent tool runs in-process runs inside
:func:`agent_tool_running`. The functions that promote, install, import or
trust a skill call :func:`require_user_terminal` first, so neither route can
reach them, however the command was spelled.
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from typing import Dict, Iterator, Mapping, Optional

from gaia.skills.errors import SkillError

#: Set on every process an agent tool starts; inherited by its children.
AGENT_CHILD_ENV = "GAIA_AGENT_CHILD"

HUMAN_ONLY_MESSAGE = (
    "This changes which skills are trusted, so only you can run it, from your "
    "own terminal."
)

_IN_AGENT_TOOL: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "gaia_in_agent_tool", default=False
)


class SkillHumanOnlyError(SkillError):
    """A trust-changing skill action was attempted from an agent's tool."""


def agent_child_env(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """*env* (default: this process's) marked as started by an agent tool."""
    marked = dict(os.environ if env is None else env)
    marked[AGENT_CHILD_ENV] = "1"
    return marked


@contextmanager
def agent_tool_running() -> Iterator[None]:
    """Mark the code run inside this block as an agent tool's."""
    token = _IN_AGENT_TOOL.set(True)
    try:
        yield
    finally:
        _IN_AGENT_TOOL.reset(token)


def started_by_agent() -> bool:
    """Whether this code runs in an agent tool or a process one started."""
    return _IN_AGENT_TOOL.get() or bool(os.environ.get(AGENT_CHILD_ENV))


def require_user_terminal(action: str) -> None:
    """Refuse *action* when an agent tool, not the user, is running it.

    Raises:
        SkillHumanOnlyError: inside an agent tool or a process one started.
    """
    if started_by_agent():
        raise SkillHumanOnlyError(f"Refused '{action}': {HUMAN_ONLY_MESSAGE}")
