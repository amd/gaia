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

import os
import re
from typing import Any, Iterable, List, Optional, Tuple

from gaia.web.citations import cited_urls, evidence_from_calls, uncited_reads

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


#: Tools whose calls a research report's citations are checked against.
WEB_RECORD_TOOLS = frozenset(RESEARCH_TOOLS) | {"download_file"}
#: Reads that open a downloaded file; one counts only for a downloaded path.
DOWNLOAD_READ_TOOLS = frozenset({"read_file", "index_document"})
#: ``download_file``'s receipt names where the file went.
_SAVED_TO = re.compile(r"^\s*Saved to:\s*(.+?)\s*$", re.MULTILINE)
#: Web calls kept per conversation: well past a deep report's few dozen.
WEB_CALLS_KEPT = 300
#: Links shown per problem: enough to act on, short enough to read.
_SHOWN = 5

CITATION_REPAIR = (
    "[check:citations] {problems} Open each page you have not read with "
    "fetch_page (download_file, then read_file, for a PDF) and keep only what "
    "it says, or drop that citation. Copy every "
    "link exactly as a tool result gave it; never retype or shorten one. Cite "
    "only pages you fetched. This check is internal: write to the user without "
    "mentioning it. Then give your complete report again: it replaces the one "
    "above, which the user will not see."
)


def _listed(urls: List[str]) -> str:
    shown = ", ".join(urls[:_SHOWN])
    more = len(urls) - _SHOWN
    return f"{shown} and {more} more" if more > 0 else shown


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path.strip())))


def downloaded_path(result: Any) -> Optional[str]:
    """Where a ``download_file`` call saved its file, or ``None`` if it did not."""
    match = _SAVED_TO.search(result) if isinstance(result, str) else None
    return _path_key(match.group(1)) if match else None


def file_read_path(args: Any) -> Optional[str]:
    """The file a ``read_file`` / ``index_document`` call opened."""
    path = args.get("file_path") if isinstance(args, dict) else None
    return _path_key(path) if isinstance(path, str) and path.strip() else None


def _as_page_reads(calls: Iterable[Tuple[str, Any, Any]]) -> List[Tuple[str, Any, Any]]:
    """Calls with each read of a downloaded file turned into a read of its URL.

    A download alone is a receipt, not a reading: the link it fetched counts
    as opened only once ``read_file`` or ``index_document`` opens the file.
    """
    sources: dict = {}
    out = []
    for name, args, result in calls:
        if name == "download_file":
            path = downloaded_path(result)
            url = args.get("url") if isinstance(args, dict) else None
            if path and isinstance(url, str):
                sources[path] = url
            out.append((name, args, None))
            continue
        if name in DOWNLOAD_READ_TOOLS:
            url = sources.get(file_read_path(args) or "")
            if url:
                out.append(("fetch_page", {"url": url}, result))
            continue
        out.append((name, args, result))
    return out


def citation_problems(report: str, calls: Iterable[Tuple[str, Any, Any]]) -> List[str]:
    """What is wrong with a research report's citations, as plain sentences.

    *calls* are the conversation's web tool calls as ``(name, args, result)``,
    including ``download_file`` and the reads of the files it saved.
    """
    evidence = evidence_from_calls(_as_page_reads(calls))
    unread, invented = uncited_reads(report, evidence)
    problems = []
    if invented:
        problems.append(
            "These cited links appear in no search result or page read in this "
            f"conversation, so they may be wrong: {_listed(invented)}."
        )
    if unread:
        problems.append(
            "These cited pages were never opened, so they may not say what they "
            f"are cited for: {_listed(unread)}."
        )
    if evidence.pages and not cited_urls(report):
        problems.append("The report cites none of the pages that were read.")
    return problems
