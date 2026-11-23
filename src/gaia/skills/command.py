# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run a skill by typing ``/<skill-name> [request]``, as in Claude Code.

The agent calls :func:`parse_skill_command` on every message. A match names an
installed skill; the agent turns it on and sends the model
:func:`skill_command_message` in place of the typed line, so the turn follows
that skill's instructions instead of hoping the model picks it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Notes a host puts in front of what the user typed (the Agent UI's folder
# note), one line each and a blank line after. The command is the user's text.
_COMMAND = re.compile(
    r"\A(?P<lead>(?:\[[^\n]*\]\n\n)*)"
    r"/(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"(?:[ \t]*\n|[ \t]+|\Z)(?P<request>.*)\Z",
    re.DOTALL,
)


@dataclass(frozen=True)
class SkillCommand:
    """``/name request`` as typed, plus any host notes that came before it."""

    name: str
    request: str
    lead: str = ""


def parse_skill_command(text: str) -> Optional[SkillCommand]:
    """The skill command *text* starts with, or ``None``.

    Only the shape is checked here; whether *name* is an installed skill is the
    caller's question. ``/etc/hosts`` and ``/tmp/x.txt`` never match, because a
    skill name cannot hold a second slash or a dot.
    """
    match = _COMMAND.match(text or "")
    if match is None:
        return None
    return SkillCommand(
        name=match.group("name"),
        request=match.group("request").strip(),
        lead=match.group("lead"),
    )


def skill_command_message(command: SkillCommand, activation: Dict[str, Any]) -> str:
    """What the model is sent for *command*, given the skill load's result.

    *activation* is what ``load_skill`` returns: ``status`` ``"success"`` with an
    optional ``warning``, or ``"error"`` with the reason. A refused load tells the
    model to report the reason, never to attempt the request without the skill.
    """
    name = command.name
    if activation.get("status") != "success":
        reason = activation.get("error") or "the skill did not load"
        note = (
            f"[GAIA: the user typed /{name} to run the {name} skill, but it "
            f"could not be turned on: {reason} Tell the user that, with what to "
            "do about it. Do not attempt their request without the skill.]"
        )
        body = f"Their request was: {command.request}" if command.request else ""
        return command.lead + (f"{note}\n\n{body}" if body else note)

    parts = [
        f"[GAIA: the user typed /{name} to run the {name} skill. It is loaded "
        "and its instructions are in your system prompt. Follow them for this "
        "request."
    ]
    directory = activation.get("directory")
    if directory:
        parts.append(f"Paths in those instructions are relative to {directory}.")
    warning = activation.get("warning")
    if warning:
        parts.append(warning)
    note = " ".join(parts) + "]"
    request = command.request or (
        "No request was typed after the command. Start the skill; if it needs "
        "something from the user first, ask for it."
    )
    return f"{command.lead}{note}\n\n{request}"
