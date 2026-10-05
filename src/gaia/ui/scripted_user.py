# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An eval-only stand-in for a user who says no, then stops answering.

The agent eval drives the Agent UI over HTTP with ``GAIA_AUTO_APPROVE_TOOLS=1``,
so nothing in it can ever decline a tool — and what the agent does *after* a
"no" is exactly what #4447 needs measured. While a script is set, a shell
command whose effect matches one of the declined commands is denied the same
way a user's No is, and every question goes unanswered, as in the run that
surfaced the bug (a prompt that timed out with nobody there).

Process-wide on purpose: the runner executes scenarios one at a time against
one backend, sets the script before a scenario and clears it after. The
endpoint that sets it refuses unless ``GAIA_EVAL_SCRIPTED_USER=1``.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from gaia.agents.base.denied_effects import DeniedEffects

ENV_VAR = "GAIA_EVAL_SCRIPTED_USER"

_lock = threading.Lock()
_ledger: Optional[DeniedEffects] = None


def set_declined_commands(commands: List[str]) -> List[str]:
    """Replace the script; an empty list turns the scripted user off."""
    global _ledger  # pylint: disable=global-statement
    cleaned = [c.strip() for c in commands if isinstance(c, str) and c.strip()]
    ledger = DeniedEffects()
    for command in cleaned:
        if ledger.record("run_shell_command", {"command": command}, "scripted") is None:
            raise ValueError(
                f"{command!r} names no command the scripted user could decline. "
                "Give a shell command such as 'pytest'."
            )
    with _lock:
        _ledger = ledger if cleaned else None
    return cleaned


def active() -> bool:
    """True while a script is set."""
    with _lock:
        return _ledger is not None


def declines(tool_name: str, tool_args: Optional[Dict[str, Any]]) -> bool:
    """Would the scripted user say no to this prompt?

    Only a prompt that shows a shell command, since that is what the user
    read and refused; a reroute through another tool is the agent's to stop.
    """
    if not isinstance(tool_args, dict) or not isinstance(tool_args.get("command"), str):
        return False
    with _lock:
        ledger = _ledger
    return ledger is not None and ledger.conflict(tool_name, tool_args) is not None
