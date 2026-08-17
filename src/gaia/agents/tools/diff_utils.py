#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared unified-diff helper for file-editing tools.

Every ``file_io_tools`` write/edit tool that mutates a text file on disk wants
the same three things: a unified diff of what changed, a one-line summary for
the activity log, and honest behavior when the "before" content turns out to
be binary. Centralizing it here means the diff a user sees in the TUI is
computed identically whether the agent called ``write_file``, ``edit_file``,
or ``write_python_file`` — a format that drifted between tools is a card the
renderer would have to special-case.

The unified diff this module returns is transport data, not prose — the TUI's
``diff`` render card parses it structurally (``tui/internal/ui/cards/diff.go``,
contract §4.3 in ``docs/spec/agent-ui-query-sse-contract.md``). Nothing here
formats it for direct display.
"""

from __future__ import annotations

import difflib
import os
from typing import Any, Dict, Optional, Tuple

#: Hard cap on unified-diff lines carried over the stdout/JSONL pipe to the
#: TUI (stdio.py) or the SSE payload. The TUI's own ``diff`` card applies a
#: much tighter DISPLAY cap on top of this (maxDiffCardRows in
#: tui/internal/ui/cards/diff.go) — this ceiling exists purely so one
#: pathological tool_result (a multi-thousand-line rewrite) can't blow up the
#: transport itself.
DIFF_MAX_LINES = 4000

#: Hard cap on the diff's SIZE, applied after the line cap. Lines can be
#: arbitrarily long (minified JS, generated code), and the TUI's JSONL reader
#: caps one line at 1MB — 4000 long lines exceeds that after JSON escaping
#: and kills the whole turn. The diff also rides back into the model's
#: context as part of the tool result, where the NPU profile truncates any
#: result over ~20K chars; staying well under that keeps the rest of the
#: result intact.
DIFF_MAX_BYTES = 8_000

#: A brand-new file's "diff" is just its entire content as additions — the
#: model already has that content (it supplied it), so echoing more than a
#: short preview back through the tool result costs context for nothing.
#: Matches the display card's own row cap (maxDiffCardRows).
NEW_FILE_PREVIEW_LINES = 40


def read_text_or_binary(path: str) -> Tuple[Optional[str], Optional[int]]:
    """Read *path* as UTF-8 text.

    Returns ``(text, None)`` when *path* exists and decodes as UTF-8,
    ``(None, size_bytes)`` when it exists but is not valid UTF-8 (a binary
    file — image, archive, compiled artifact), or ``(None, None)`` when it
    does not exist at all.

    Every write/edit tool that needs "the file's current content, if any"
    goes through this rather than a bare ``open(...).read()``: the bare
    form's ``UnicodeDecodeError`` on a binary file used to surface as an
    opaque ``str(e)`` tool error instead of the honest "this is binary, diff
    skipped" outcome the diff-display feature requires.
    """
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), None
    except UnicodeDecodeError:
        return None, os.path.getsize(path)


def format_size(size_bytes: int) -> str:
    """``2_400_000`` -> ``"2.3 MB"``."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def build_diff(
    file_path: str,
    before: Optional[str],
    after: str,
    *,
    context_lines: int = 3,
    max_lines: int = DIFF_MAX_LINES,
) -> Dict[str, Any]:
    """Compute the unified diff and card-ready summary for one text edit.

    ``before`` is ``None`` for a file that did not exist before this call (a
    new file — the whole ``after`` renders as additions). Returns the fields
    both the tool_result payload and the TUI's ``diff`` render card want:
    ``diff`` (unified text, possibly truncated), ``additions``,
    ``deletions``, ``has_changes``, ``is_new_file``, ``diff_truncated``, and
    ``summary`` (the one-line activity-log outcome).
    """
    before_text = before if before is not None else ""
    is_new_file = before is None

    diff_lines = list(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="/dev/null" if is_new_file else file_path,
            tofile=file_path,
            n=context_lines,
        )
    )
    # difflib emits the final line WITHOUT a newline when the source lacks
    # one, so "".join would glue the next diff line onto it ("-beta+beta"),
    # corrupting the parse and shifting every later line number in the card.
    diff_lines = [l if l.endswith("\n") else l + "\n" for l in diff_lines]

    additions = sum(
        1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
    )
    has_changes = before_text != after

    truncated = False
    if is_new_file:
        max_lines = min(max_lines, NEW_FILE_PREVIEW_LINES)
    if len(diff_lines) > max_lines:
        hidden = len(diff_lines) - max_lines
        diff_lines = diff_lines[:max_lines]
        diff_lines.append(
            f"... ({hidden} more diff line{'s' if hidden != 1 else ''} not "
            "shown — truncated for transport)\n"
        )
        truncated = True

    # Byte ceiling on top of the line ceiling — truncate at a line boundary.
    total = 0
    for i, line in enumerate(diff_lines):
        total += len(line.encode("utf-8"))
        if total > DIFF_MAX_BYTES:
            hidden = len(diff_lines) - i
            diff_lines = diff_lines[:i]
            diff_lines.append(
                f"... ({hidden} more diff line{'s' if hidden != 1 else ''} not "
                "shown — truncated for transport)\n"
            )
            truncated = True
            break

    if is_new_file:
        line_count = len(after.splitlines())
        summary = f"new file, {line_count} line{'s' if line_count != 1 else ''}"
    elif not has_changes:
        summary = "no changes"
    else:
        summary = f"+{additions} -{deletions}"

    return {
        "diff": "".join(diff_lines),
        "additions": additions,
        "deletions": deletions,
        "has_changes": has_changes,
        "is_new_file": is_new_file,
        "diff_truncated": truncated,
        "summary": summary,
    }


def binary_skip_result(size_bytes: int) -> Dict[str, Any]:
    """Fields to merge into a *write* tool's result when the prior file was binary.

    Diffing raw bytes as if they were text produces garbage a terminal cannot
    usefully show, so the diff is skipped outright and the card degrades to a
    size summary instead — the write itself still proceeds, since a full
    overwrite never needs to read the old content as text.
    """
    return {
        "diff": "",
        "is_binary": True,
        "size_bytes": size_bytes,
        "has_changes": True,
        "is_new_file": False,
        "diff_truncated": False,
        "summary": f"binary file ({format_size(size_bytes)}) — diff skipped",
    }


def diff_fields_for_overwrite(file_path: str, new_content: str) -> Dict[str, Any]:
    """Diff fields for a tool that fully overwrites *file_path* with *new_content*.

    Reads whatever is currently on disk (if anything) and dispatches to
    :func:`build_diff` or :func:`binary_skip_result` — the one call a
    ``write_*`` tool needs before it writes the new content. Read the old
    content BEFORE writing; this must be called prior to the overwrite.
    """
    before_text, binary_size = read_text_or_binary(file_path)
    if binary_size is not None:
        return binary_skip_result(binary_size)
    return build_diff(file_path, before_text, new_content)


def read_text_for_edit(
    file_path: str,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Read *file_path* as text for an in-place (search/replace) edit.

    Returns ``(text, None)`` on success, or ``(None, error_result)`` when the
    file is binary — *error_result* is ready to return directly from the
    calling tool (``status="error"``, ``is_binary=True``, ``size_bytes`` set,
    and an actionable message pointing at ``write_file`` as the fix, since a
    content-replacement edit has no way to locate ``old_content`` inside
    bytes that aren't text).
    """
    text, size = read_text_or_binary(file_path)
    if size is not None:
        return None, {
            "status": "error",
            "error": (
                f"Cannot edit {file_path}: binary content "
                f"({format_size(size)}) — content-replacement edits require "
                "text. Use write_file to replace it entirely."
            ),
            "is_binary": True,
            "size_bytes": size,
        }
    return text, None


__all__ = [
    "DIFF_MAX_LINES",
    "binary_skip_result",
    "build_diff",
    "diff_fields_for_overwrite",
    "format_size",
    "read_text_for_edit",
    "read_text_or_binary",
]
