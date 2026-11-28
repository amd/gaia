# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run a skill by naming it as ``/<skill-name>`` in a message, as in Claude Code.

The agent calls :func:`parse_skill_command` on every message. A match names an
installed skill; the agent turns it on and sends the model
:func:`skill_command_message` in place of the typed text, so the turn follows
that skill's instructions instead of hoping the model picks it.

The Agent UI (``webui/src/utils/skillCommand.ts``) and the TUI
(``tui/internal/ui/chat/research.go``) read the same token rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Container, Dict, Optional

# Notes a host puts in front of what the user typed (the Agent UI's folder
# note), one line each and a blank line after. The command is the user's text.
_LEAD = re.compile(r"\A(?:\[[^\n]*\]\n\n)*")

# A whole whitespace-separated word: /name or /plugin:name, then any sentence
# punctuation. A second slash or a dot inside the word (/tmp/x.txt) is no match.
_TOKEN = re.compile(
    r"(?<!\S)/(?P<name>(?:[a-z0-9]+(?:-[a-z0-9]+)*:)?[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"[.,!?;:\u060c\u061f]*(?!\S)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SkillCommand:
    """The skill a message names, the request to send, and any host notes."""

    name: str
    request: str
    lead: str = ""


def names_a_skill(text: str) -> bool:
    """Whether *text* holds a word shaped like a skill command, installed or not."""
    return _TOKEN.search(text or "") is not None


def parse_skill_command(text: str, installed: Container[str]) -> Optional[SkillCommand]:
    """The first installed skill *text* names as ``/<name>``, or ``None``.

    The word may start the message or follow any whitespace, in any case;
    ``/etc/hosts`` and ``/tmp/x.txt`` never match. A Claude Code plugin's skill
    is typed as Claude Code types it: ``/<plugin>:<skill>``. Only the first
    installed one runs, so a turn carries one skill's instructions.

    A command that starts the message is cut from the request, as before. One
    later in a sentence stays in it, so "rewrite this with /humanizer" still
    reads as what the user asked.
    """
    text = text or ""
    lead = _LEAD.match(text).group(0)
    typed = text[len(lead) :]
    for match in _TOKEN.finditer(typed):
        name = match.group("name").lower()
        if name not in installed:
            continue
        leading = not typed[: match.start()].strip()
        request = typed[match.end() :] if leading else typed
        return SkillCommand(name=name, request=request.strip(), lead=lead)
    return None


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
