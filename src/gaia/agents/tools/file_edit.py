#!/usr/bin/env python
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared rules for GAIA's file-changing tools.

Both rules live here so the separate implementations in ``file_io_tools`` and
``file_tools`` cannot drift apart.

**Read before edit.** :class:`FileReadRecord` is one agent's record of the
files it has read. A tool that would change an existing file refuses when the
file isn't in the record, or when its mtime or size moved since the read.
Creating a file needs no read, and what the agent wrote or edited itself stays
unlocked.

**Match and replace.** Every ``edit_*`` tool routes through
:func:`apply_unique_replacement`:

- ``old_content`` must match **exactly once**. Two matches is an error naming
  the count and the line of each, not a first-match replacement — the model
  cannot tell a wrong-region edit from the one it asked for, and neither can
  the human reading the diff.
- A rejected edit carries the file's current content around the region the
  caller was aiming at, so the retry lands without a separate re-read.
"""

import difflib
import os
import re
import stat
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.agents.base.verification import NOT_EXECUTED
from gaia.logger import get_logger

logger = get_logger(__name__)

# Lines of surrounding context returned with a rejected edit.
CONTEXT_RADIUS = 12

# Lines of context shown per location in an ambiguity report.
MATCH_CONTEXT_RADIUS = 2

# Locations described individually before an ambiguity report stops listing them.
MAX_REPORTED_MATCHES = 5

# Ceiling on a returned excerpt, so a rejection cannot flood the context window.
MAX_EXCERPT_CHARS = 4000

# Similarity a line needs against the probe line to anchor a not-found excerpt.
_ANCHOR_THRESHOLD = 0.6

#: A regular file's ``(st_mtime_ns, st_size)``.
Stamp = Tuple[int, int]


def _key(file_path: Any) -> str:
    """Record key: one entry per file however the caller spelled the path."""
    return os.path.normcase(os.path.realpath(os.fspath(file_path)))


def stamp_of(file_path: Any) -> Optional[Stamp]:
    """The file's ``(mtime_ns, size)``, or ``None`` when no regular file is there."""
    try:
        st = os.stat(os.fspath(file_path))
    except (FileNotFoundError, NotADirectoryError):
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return st.st_mtime_ns, st.st_size


def _read_required(message: str, file_path: Any, error_type: str) -> Dict[str, Any]:
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error": message,
        "error_type": error_type,
        "file_path": os.fspath(file_path),
    }


class FileReadRecord:
    """The files one agent has read, with each one's mtime and size at the time.

    A patch written from a grep snippet or a guess is made blind: the model
    never saw the code around the change. Holding the edit tools to this record
    grounds "have you looked?" in what the read tools actually returned.

    - A read records the file, a partial or line-range read included.
    - Creating a file records it, and so does every successful edit or overwrite.
    - Changing an existing file that isn't recorded, or whose mtime or size
      differs from the record, is refused. That includes a bare ``touch``: the
      stamp cannot tell it from a rewrite, and a fresh read is cheap.

    One per agent instance (see :func:`file_read_record`), for the agent's
    lifetime: another agent's reads say nothing about what this one has seen.
    """

    def __init__(self) -> None:
        self._seen: Dict[str, Stamp] = {}
        self._lock = threading.Lock()

    def note(self, file_path: Any, stamp: Optional[Stamp] = None) -> None:
        """Record ``file_path`` as seen in state ``stamp`` (default: as it is now).

        Read tools take the stamp *before* reading, so a write landing mid-read
        leaves a record that no longer matches instead of one that hides it.
        """
        if stamp is None:
            stamp = stamp_of(file_path)
            if stamp is None:
                return  # no file there, so nothing for a later change to clobber
        with self._lock:
            self._seen[_key(file_path)] = stamp

    def refusal(
        self, file_path: Any, verb: str = "editing"
    ) -> Optional[Dict[str, Any]]:
        """Why changing ``file_path`` must wait for a read, or ``None`` to go ahead.

        ``verb`` names the change in the message: "editing", "overwriting".
        """
        now = stamp_of(file_path)
        if now is None:
            return None
        with self._lock:
            seen = self._seen.get(_key(file_path))
        if seen is None:
            return _read_required(
                f"Read {file_path} with read_file before {verb} it; edits to a "
                "file you haven't read are made blind. Nothing was written.",
                file_path,
                "not_read",
            )
        if seen != now:
            return _read_required(
                f"{file_path} changed on disk since you read it; read it again "
                f"with read_file before {verb} it. Nothing was written.",
                file_path,
                "changed_since_read",
            )
        return None


_ATTACH_LOCK = threading.Lock()


def file_read_record(host: Any) -> FileReadRecord:
    """``host``'s record, attached on first use.

    Lazy because a tool mixin cannot count on its ``__init__`` running:
    ``Agent.__init__`` does not chain to ``super().__init__()``.
    """
    record = getattr(host, "_file_read_record", None)
    if isinstance(record, FileReadRecord):
        return record
    with _ATTACH_LOCK:
        record = getattr(host, "_file_read_record", None)
        if not isinstance(record, FileReadRecord):
            record = FileReadRecord()
            host._file_read_record = record
    return record


def read_first_preflight(
    host: Any,
    target_of: Callable[[Dict[str, Any]], Any],
    verb: str = "editing",
) -> Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """A ``@tool(preflight=...)`` check: refuse a blind change before the prompt.

    ``target_of`` maps the call's arguments to the path the tool would change,
    resolved the way the tool resolves it. The tool body repeats the check,
    because the file can change while a confirmation prompt waits.
    """

    def preflight(tool_args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            target = target_of(tool_args)
            validator = getattr(host, "path_validator", None) or getattr(
                host, "_path_validator", None
            )
            # Out of scope or write-blocked, the tool's own refusal is the true
            # one, and it doesn't reveal whether a file exists there.
            if validator is not None and (
                not validator.is_path_allowed(str(target), prompt_user=False)
                or validator.is_write_blocked(str(target))[0]
            ):
                return None
            return file_read_record(host).refusal(target, verb)
        except (KeyError, TypeError, ValueError, OSError) as e:
            # Malformed arguments or an unstat-able path: the tool body reports it.
            logger.debug("read-first preflight deferred to the tool: %s", e)
            return None

    return preflight


def _line_of(content: str, offset: int) -> int:
    """1-based line number of a character offset."""
    return content.count("\n", 0, offset) + 1


def _match_offsets(content: str, needle: str) -> List[int]:
    offsets = []
    start = 0
    while True:
        found = content.find(needle, start)
        if found == -1:
            return offsets
        offsets.append(found)
        start = found + len(needle)


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clip(text: str) -> Tuple[str, bool]:
    if len(text) <= MAX_EXCERPT_CHARS:
        return text, False
    return text[:MAX_EXCERPT_CHARS], True


def _slice_lines(lines: List[str], center: int, radius: int) -> Tuple[str, int, int]:
    """Excerpt around a 0-based line index. Returns (text, start_1based, end_1based)."""
    start = max(0, center - radius)
    end = min(len(lines), center + radius + 1)
    return "\n".join(lines[start:end]), start + 1, end


def _anchor_line(lines: List[str], old_content: str) -> Optional[int]:
    """0-based index of the line most like the first real line of ``old_content``."""
    probe = next((ln.strip() for ln in old_content.splitlines() if ln.strip()), "")
    if not probe:
        return None

    best_index: Optional[int] = None
    best_score = _ANCHOR_THRESHOLD
    matcher = difflib.SequenceMatcher(b=probe, autojunk=False)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        matcher.set_seq1(stripped)
        # real_quick_ratio/quick_ratio are cheap upper bounds — skip the O(n*m)
        # ratio() for lines that cannot clear the threshold.
        if matcher.real_quick_ratio() <= best_score:
            continue
        if matcher.quick_ratio() <= best_score:
            continue
        score = matcher.ratio()
        if score > best_score:
            best_score = score
            best_index = index
    return best_index


def _excerpt(current_content: str, old_content: str) -> Dict[str, Any]:
    """Current content around the region the caller was most likely aiming at.

    Centres on ``old_content`` when it occurs, on the closest fuzzy line match
    otherwise, and falls back to the head of the file when nothing resembles it.
    """
    lines = current_content.splitlines()
    total = len(lines)

    offsets = _match_offsets(current_content, old_content) if old_content else []
    if offsets:
        center = _line_of(current_content, offsets[0]) - 1
        anchored_on = "match"
    else:
        anchor = _anchor_line(lines, old_content)
        if anchor is None:
            center, anchored_on = min(CONTEXT_RADIUS, total), "file_start"
        else:
            center, anchored_on = anchor, "closest_line"

    text, start, end = _slice_lines(lines, center, CONTEXT_RADIUS)
    text, truncated = _clip(text)
    return {
        "current_content": text,
        "current_content_start_line": start,
        "current_content_end_line": end,
        "current_content_total_lines": total,
        "current_content_truncated": truncated,
        "current_content_anchored_on": anchored_on,
    }


def _describe_matches(current_content: str, offsets: List[int]) -> List[Dict[str, Any]]:
    lines = current_content.splitlines()
    described = []
    for offset in offsets[:MAX_REPORTED_MATCHES]:
        line_no = _line_of(current_content, offset)
        context, start, end = _slice_lines(lines, line_no - 1, MATCH_CONTEXT_RADIUS)
        clipped, _ = _clip(context)
        described.append(
            {
                "line": line_no,
                "context": clipped,
                "context_start_line": start,
                "context_end_line": end,
            }
        )
    return described


def _error(
    message: str, file_path: str, match_count: int, extra: Dict[str, Any]
) -> Dict[str, Any]:
    payload = {
        "status": "error",
        "error": message,
        "file_path": str(file_path),
        "match_count": match_count,
    }
    payload.update(extra)
    return payload


def apply_unique_replacement(
    file_path: str,
    current_content: str,
    old_content: str,
    new_content: str,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Replace the single occurrence of ``old_content``, or explain why not.

    Returns ``(updated_content, None)`` on success and ``(None, error)`` on
    every rejection. The error dict always carries ``status``, ``error``,
    ``file_path`` and ``match_count``; rejections that a retry could fix also
    carry ``current_content`` and its line range, so the caller does not have
    to re-read the file to try again.

    Callers own their own checks (path allowlist, size limits, backups, and
    :class:`FileReadRecord`) — this function only decides *what* the new
    contents should be.
    """
    file_path = str(file_path)

    if not old_content:
        return None, _error(
            f"old_content is empty, so there is nothing to find in {file_path} — "
            "nothing was written. Pass the exact text to replace, or use "
            "write_file to replace the whole file.",
            file_path,
            0,
            {},
        )

    offsets = _match_offsets(current_content, old_content)

    if not offsets:
        hint = ""
        if _collapse_whitespace(old_content) in _collapse_whitespace(current_content):
            hint = (
                " A whitespace-insensitive match does exist, so the indentation, "
                "tabs-vs-spaces, or line endings in old_content differ from the file."
            )
        excerpt = _excerpt(current_content, old_content)
        return None, _error(
            f"Content to replace not found in {file_path} — nothing was written."
            f"{hint} The file's current content around the closest region is "
            f"included as `current_content` (lines "
            f"{excerpt['current_content_start_line']}-"
            f"{excerpt['current_content_end_line']} of "
            f"{excerpt['current_content_total_lines']}); copy old_content "
            "verbatim from it.",
            file_path,
            0,
            excerpt,
        )

    if len(offsets) > 1:
        line_numbers = [_line_of(current_content, offset) for offset in offsets]
        shown = ", ".join(str(n) for n in line_numbers[:MAX_REPORTED_MATCHES])
        if len(line_numbers) > MAX_REPORTED_MATCHES:
            shown += ", ..."
        return None, _error(
            f"Ambiguous edit: old_content matches {len(offsets)} locations in "
            f"{file_path} (lines {shown}) — nothing was written, because there "
            "is no way to tell which one you meant. Extend old_content with "
            "enough surrounding lines to match exactly one location, then "
            "reissue the edit. The candidate locations are listed in `matches`.",
            file_path,
            len(offsets),
            {
                "ambiguous": True,
                "match_lines": line_numbers,
                "matches": _describe_matches(current_content, offsets),
                **_excerpt(current_content, old_content),
            },
        )

    offset = offsets[0]
    updated = (
        current_content[:offset]
        + new_content
        + current_content[offset + len(old_content) :]
    )
    return updated, None
