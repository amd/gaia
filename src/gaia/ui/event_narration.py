# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Turn machine-shaped agent events into the one line a human wants to read.

The canonical ``/query`` stream (see :mod:`gaia.ui.sse_translation`) carries
``tool_call {tool, args}`` and ``tool_result {tool, data}`` — accurate, and
unreadable. A user watching a two-minute turn should see *what the agent is
doing*, not the harness driving it:

    Checking your installed skills
    Loading skill: github-triage
    Running command: git log --oneline -5

This module derives those phrases. It is stdlib-only and free of ``gaia``
imports, matching ``sse_translation``'s dependency-light contract, so it
unit-tests without Lemonade, a registry, or a running agent.

Two entry points:

- :func:`derive_narration` — ``tool_call.narration``, a present-tense phrase
  naming the action in the user's terms.
- :func:`derive_preview` — ``tool_result.preview``, a hard-truncated single
  line carrying the outcome plus size/latency.

Design commitments
------------------
- **Never blocks a new tool.** Narration is derived from the tool *name* first
  (``load_skill`` → "Loading skill"), so a tool nobody taught this module still
  gets a user-facing phrase. Curated overrides exist only where the derived
  form reads badly.
- **Never silently empty** (CLAUDE.md, no-silent-fallbacks). Every path returns
  a non-empty, honest phrase. The last-resort form names the tool rather than
  inventing an action or emitting ``""``.
- **Never a raw payload.** Both functions collapse whitespace and hard-truncate,
  so a 40 KB stdout or a nested JSON blob can't blow up a single-line UI.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

#: ``channel`` value marking an event as harness bookkeeping rather than a
#: description of the user's work. Lives here because the layer that *tags*
#: (``gaia.ui.sse_handler``) and the layer that *filters* (
#: ``gaia.ui.sse_translation``) must agree on it, and a magic string in two
#: files drifts the moment one of them is edited alone.
DEBUG_CHANNEL = "debug"

#: Longest argument fragment embedded in a narration phrase.
_ARG_MAX_CHARS = 80

#: Longest ``tool_result.preview`` line. The front-end renders it on one row.
_PREVIEW_MAX_CHARS = 120

#: ``verb token -> present participle``. Tool names are overwhelmingly
#: ``verb_object`` (``list_skills``, ``read_file``), so conjugating the first
#: token yields a natural phrase for tools this module has never seen.
_VERB_FORMS = {
    "add": "Adding",
    "analyze": "Analyzing",
    "append": "Appending",
    "apply": "Applying",
    "build": "Building",
    "call": "Calling",
    "cancel": "Cancelling",
    "check": "Checking",
    "clear": "Clearing",
    "close": "Closing",
    "compare": "Comparing",
    "convert": "Converting",
    "copy": "Copying",
    "count": "Counting",
    "create": "Creating",
    "delete": "Deleting",
    "describe": "Describing",
    "download": "Downloading",
    "edit": "Editing",
    "execute": "Running",
    "export": "Exporting",
    "extract": "Extracting",
    "fetch": "Fetching",
    "find": "Finding",
    "generate": "Generating",
    "get": "Getting",
    "import": "Importing",
    "index": "Indexing",
    "insert": "Inserting",
    "inspect": "Inspecting",
    "install": "Installing",
    "list": "Listing",
    "load": "Loading",
    "make": "Making",
    "move": "Moving",
    "open": "Opening",
    "parse": "Parsing",
    "pull": "Pulling",
    "push": "Pushing",
    "query": "Querying",
    "read": "Reading",
    "remove": "Removing",
    "rename": "Renaming",
    "render": "Rendering",
    "reply": "Replying",
    "resolve": "Resolving",
    "run": "Running",
    "save": "Saving",
    "scan": "Scanning",
    "search": "Searching",
    "send": "Sending",
    "set": "Setting",
    "show": "Showing",
    "start": "Starting",
    "stop": "Stopping",
    "summarize": "Summarizing",
    "sync": "Syncing",
    "translate": "Translating",
    "update": "Updating",
    "upload": "Uploading",
    "validate": "Validating",
    "verify": "Verifying",
    "view": "Viewing",
    "write": "Writing",
}

#: Argument names that carry the point of the call, most-specific first. The
#: first one present becomes the narration's detail fragment.
_SALIENT_ARG_KEYS = (
    "command",
    "cmd",
    "script",
    "shell_command",
    "query",
    "question",
    "search_query",
    "prompt",
    "url",
    "file_path",
    "filepath",
    "filename",
    "path",
    "file",
    "directory",
    "pattern",
    "skill_name",
    "skill",
    "name",
    "topic",
    "issue_number",
    "issue",
    "number",
    "table",
    "sql",
    "subject",
    "to",
    "text",
    "content",
    "id",
)

#: Tools whose derived phrase reads badly or loses the point. Kept deliberately
#: small — the generic deriver is the contract, this is the exception list.
_NARRATION_OVERRIDES = {
    "list_skills": "Checking your installed skills",
    "list_directory": "Listing folder contents",
    "list_indexed_documents": "Checking which documents are indexed",
    "query_documents": "Searching your documents",
    "search_indexed_chunks": "Searching your documents",
    "web_search": "Searching the web",
    "get_file_preview": "Previewing file",
}

#: Substrings marking a tool that shells out. The narration for these MUST
#: carry the actual command — "Running a shell command" tells a user nothing
#: about whether the agent is about to ``ls`` or ``rm -rf``.
_SHELL_TOOL_MARKERS = ("shell", "bash", "terminal", "subprocess", "run_command")

_WHITESPACE_RUN = re.compile(r"\s+")


def _collapse(text: Any) -> str:
    """Flatten any value to a single line of normalized whitespace."""
    return _WHITESPACE_RUN.sub(" ", str(text)).strip()


def _clip(text: str, limit: int) -> str:
    """Hard-truncate to ``limit`` characters, marking the cut with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _is_scalar(value: Any) -> bool:
    # bool is an int subclass; a ``recursive=True`` flag is never the point of a
    # call, so it must not win the salient-argument race against a real value.
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _salient_arg(args: Mapping[str, Any]) -> Optional[str]:
    """Pick the one argument worth showing, as a clipped single line.

    Prefers :data:`_SALIENT_ARG_KEYS` by priority. Falls back to a lone scalar
    argument — with exactly one, it is unambiguously the point of the call. With
    several unrecognized arguments it returns ``None``: a ``k=v, k=v`` dump is
    the noise this module exists to remove.
    """
    for key in _SALIENT_ARG_KEYS:
        value = args.get(key)
        if _is_scalar(value) and _collapse(value):
            return _clip(_collapse(value), _ARG_MAX_CHARS)
    scalars = [v for v in args.values() if _is_scalar(v) and _collapse(v)]
    if len(scalars) == 1:
        return _clip(_collapse(scalars[0]), _ARG_MAX_CHARS)
    return None


def _humanize_tool(tool: str) -> str:
    """Conjugate ``verb_object`` into a present-tense phrase.

    ``list_skills`` → "Listing skills"; ``load_skill`` → "Loading skill". An
    unrecognized leading token is not forced into a verb — ``triage_inbox``
    becomes "Running triage inbox", which is honest about the fact that this
    module only knows the tool's name.
    """
    tokens = [t for t in re.split(r"[_\-.\s]+", tool.strip()) if t]
    if not tokens:
        return "Running a tool"
    verb = _VERB_FORMS.get(tokens[0].lower())
    if verb is None:
        return f"Running {' '.join(tokens).lower()}"
    rest = " ".join(tokens[1:]).lower()
    return f"{verb} {rest}".strip() if rest else verb


def _is_shell_tool(tool: str) -> bool:
    lowered = tool.lower()
    return any(marker in lowered for marker in _SHELL_TOOL_MARKERS)


def derive_narration(tool: str, args: Optional[Mapping[str, Any]] = None) -> str:
    """Return the present-tense phrase for a ``tool_call``.

    Args:
        tool: Registered tool name, e.g. ``load_skill``.
        args: Arguments the agent is calling it with. Only one is ever shown.

    Returns:
        A non-empty, single-line, user-facing phrase. Never a raw payload, never
        ``""`` — an unnamed tool narrates as "Running a tool" rather than
        blocking the event.
    """
    name = _collapse(tool) if tool is not None else ""
    if not name:
        return "Running a tool"
    if not isinstance(args, Mapping):
        args = {}

    detail = _salient_arg(args)

    # A shell tool without its command is worse than useless — it hides the one
    # thing the user needs to see. Say so rather than narrating a bare verb.
    if _is_shell_tool(name):
        return f"Running command: {detail}" if detail else "Running a shell command"

    base = _NARRATION_OVERRIDES.get(name) or _humanize_tool(name)
    return f"{base}: {detail}" if detail else base


# -- tool_result preview ----------------------------------------------------


def _singularize(word: str) -> str:
    """Best-effort English singular for a payload key.

    Covers the shapes that actually appear as result keys (``skills``,
    ``entries``, ``matches``) and deliberately leaves ``status`` / ``analysis``
    alone rather than mangling them into ``statu`` / ``analysi``.
    """
    lowered = word.lower()
    if lowered.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if lowered.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if lowered.endswith(("ss", "us", "is")):
        return word
    if lowered.endswith("s") and len(word) > 1:
        return word[:-1]
    return word


def format_count(count: int, noun: str) -> str:
    """``(1, "loaded_skills")`` -> ``"1 loaded skill"``.

    Shared with the SSE handler's summary builder so a count reads the same
    wherever it is produced. Only the final word is singularized, so a
    multi-word key keeps its qualifiers.
    """
    label = noun.replace("_", " ").strip()
    if count == 1 and label:
        head, _, last = label.rpartition(" ")
        label = f"{head} {_singularize(last)}".strip()
    return f"{count} {label}"


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _error_text(payload: Mapping[str, Any]) -> str:
    for key in ("error", "message", "detail", "display_message", "summary"):
        value = payload.get(key)
        if _is_scalar(value) and _collapse(value):
            return _collapse(value)
    return "tool call failed"


def _command_head(command_output: Mapping[str, Any]) -> str:
    """One line describing how a shell command ended."""
    return_code = command_output.get("return_code", 0)
    stdout = str(command_output.get("stdout") or "")
    stderr = str(command_output.get("stderr") or "")
    if return_code:
        tail = _collapse(stderr.strip().splitlines()[0]) if stderr.strip() else ""
        return f"exit {return_code}" + (f": {tail}" if tail else "")
    lines = stdout.strip().splitlines()
    if not lines:
        return "exit 0 · no output"
    return f"exit 0 · {len(lines)} line{'s' if len(lines) != 1 else ''}"


def _count_head(payload: Mapping[str, Any]) -> Optional[str]:
    """``{"skills": [...18]}`` → "18 skills"."""
    for key, value in payload.items():
        if isinstance(value, list) and value:
            return format_count(len(value), key)
    count = payload.get("count")
    if isinstance(count, int):
        return format_count(count, "results")
    return None


def _preview_head(payload: Mapping[str, Any]) -> str:
    """The outcome fragment, before size and latency are appended."""
    command_output = payload.get("command_output")
    if isinstance(command_output, Mapping):
        return _command_head(command_output)

    failed = payload.get("success") is False or payload.get("status") == "error"
    if failed:
        return f"error: {_error_text(payload)}"

    summary = payload.get("summary")
    if _is_scalar(summary) and _collapse(summary):
        text = _collapse(summary)
        # A bare "success" carries no information; prefer a real count if the
        # payload has one, and only fall back to the status word if it doesn't.
        if text.lower() in {"success", "ok", "done", "completed"}:
            return _count_head(payload) or text
        return text

    return _count_head(payload) or "done"


def derive_preview(
    tool: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    max_chars: int = _PREVIEW_MAX_CHARS,
) -> str:
    """Return the single-line outcome summary for a ``tool_result``.

    Args:
        tool: The tool that produced this result, used only for the no-payload
            phrasing.
        payload: The source result event — ``summary`` / ``success`` /
            ``latency_ms`` / ``command_output`` are read when present.
        max_chars: Hard ceiling on the returned line.

    Returns:
        A non-empty line such as ``18 skills · 21ms`` or
        ``error: GITHUB_TOKEN not set``. Long outcomes are clipped and annotated
        with the payload size so the user knows something was withheld.
    """
    if not isinstance(payload, Mapping) or not payload:
        return f"{_collapse(tool) or 'tool'} returned no result"

    head = _preview_head(payload)
    parts = [head]

    # Only claim "truncated" when the outcome text itself was cut — the size of
    # the raw payload is meaningless if the user is seeing all of it.
    budget = max_chars
    latency = payload.get("latency_ms")
    latency_note = ""
    if isinstance(latency, (int, float)) and latency >= 0:
        latency_note = f"{round(latency)}ms"
        budget -= len(latency_note) + 3

    if len(head) > budget:
        raw_size = len(str(payload.get("summary") or "")) or len(str(payload))
        size_note = f"{_format_bytes(raw_size)} truncated"
        parts = [_clip(head, max(8, budget - len(size_note) - 3)), size_note]

    if latency_note:
        parts.append(latency_note)
    return " · ".join(parts)


__all__ = ["DEBUG_CHANNEL", "derive_narration", "derive_preview", "format_count"]
