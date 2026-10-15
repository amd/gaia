# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Whether a turn with no step limit is still getting anywhere.

A step makes progress when it runs a call this turn has not made before and
that call succeeds. A step that only repeats earlier calls, only fails, or
calls nothing is idle. The agent loop stops a turn with no step limit once
too many idle steps run back to back, so a model that keeps trying variations
of a failing call cannot run forever. Pacing calls (``sleep``) never count as
idle: waiting between polls is what a long watch is supposed to look like.
"""

import json
from typing import Any, Iterable, Set, Tuple


def _call_key(tool_name: str, tool_args: Any) -> Tuple[str, str]:
    try:
        args = json.dumps(tool_args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args = repr(tool_args)
    return str(tool_name), args


class TurnProgress:
    """Counts back-to-back steps that made no progress in one turn."""

    def __init__(self, pacing_tools: Iterable[str] = ()):
        self._pacing_tools = frozenset(pacing_tools)
        self._seen: Set[Tuple[str, str]] = set()
        self._step_moved = False
        self.idle_steps = 0

    def record(self, tool_name: str, tool_args: Any, ok: bool) -> None:
        """Note one executed call of the current step."""
        if tool_name in self._pacing_tools:
            self._step_moved = True
            return
        key = _call_key(tool_name, tool_args)
        if ok and key not in self._seen:
            self._step_moved = True
        self._seen.add(key)

    def end_step(self) -> int:
        """Close the current step; return how many idle steps now run back to back."""
        self.idle_steps = 0 if self._step_moved else self.idle_steps + 1
        self._step_moved = False
        return self.idle_steps
