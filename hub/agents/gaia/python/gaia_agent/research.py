# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/research``: one turn of deep research, the same from every app.

The Agent UI's Research switch and the TUI's ``/research`` both send the turn
as ``/research <topic>``. The agent, not each app, turns that into a research
turn: it loads the ``research-report`` skill, keeps its instructions in the
prompt, and adds a research brief to the turn's context so the model searches,
reads and cites rather than answering from memory. The user's message becomes
the topic itself, so memory and history keep what the user asked, not the brief.
One implementation means the Agent UI, the TUI and ``gaia -p`` cannot drift
apart.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

#: What the user types, and what the Agent UI's switch prepends.
RESEARCH_COMMAND = "/research"
#: The skill a research turn runs on.
RESEARCH_SKILL = "research-report"
#: Tools a research turn cannot do without.
RESEARCH_TOOLS = ("search_web", "fetch_page")

RESEARCH_BRIEF = (
    "Research request: the user's message is the topic. Follow the "
    "research-report skill: break the topic into the questions it really asks, "
    "search the web for each, read the most authoritative sources with "
    "fetch_page, check for developments newer than your sources, and write the "
    "report in your reply - the answer first, a section per question, every "
    "factual claim cited inline with a link to a page you fetched in this "
    "conversation, and a Sources list at the end. Say plainly what you could "
    "not confirm."
)

BARE_RESEARCH_BRIEF = (
    "The user switched on research mode without naming a topic. Ask them, in "
    "one short sentence, what they would like researched. Do not search yet."
)

#: Bracketed notes an app puts before the user's words, e.g. the Agent UI's
#: "[This chat is working in the folder ...]". A folder name may hold brackets.
_LEADING_NOTES = re.compile(r"\A(?:\s*\[[^\n]*\]\s*\n)+")


def _split_notes(text: str) -> Tuple[str, str]:
    """``(notes, rest)``: the leading bracketed notes, and what the user typed."""
    match = _LEADING_NOTES.match(text)
    if match is None:
        return "", text
    return match.group(0).strip(), text[match.end() :]


def split_research_command(text: object) -> Optional[Tuple[str, str]]:
    """``(notes, topic)`` for a ``/research`` message, ``None`` for any other.

    Only a leading ``/research`` followed by whitespace or the end counts, after
    any notes the app put first: ``/researcher`` and a ``/research``
    mid-sentence are ordinary text. A bare ``/research`` has topic ``""``.
    """
    if not isinstance(text, str):
        return None
    notes, rest = _split_notes(text)
    stripped = rest.lstrip()
    if not stripped.lower().startswith(RESEARCH_COMMAND):
        return None
    after = stripped[len(RESEARCH_COMMAND) :]
    if after and not after[0].isspace():
        return None
    return notes, after.strip()


def parse_research_command(text: object) -> Optional[str]:
    """The topic of a ``/research`` turn, ``""`` for a bare one, ``None`` otherwise."""
    split = split_research_command(text)
    return None if split is None else split[1]


def research_brief(topic: str) -> str:
    """The instruction a research turn adds to its context."""
    return RESEARCH_BRIEF if topic else BARE_RESEARCH_BRIEF


def research_message(notes: str, topic: str) -> str:
    """The user's message for the turn: the topic, behind any app notes."""
    return "\n\n".join(part for part in (notes, topic or RESEARCH_COMMAND) if part)
