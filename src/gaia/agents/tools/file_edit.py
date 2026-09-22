#!/usr/bin/env python
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared match-and-replace semantics for GAIA's file-editing tools.

Every ``edit_*`` tool routes its match-and-replace through
:func:`apply_unique_replacement`, so the separate implementations in
``file_io_tools`` and ``file_tools`` cannot drift apart.

The contract:

- ``old_content`` must match **exactly once**. Two matches is an error naming
  the count and the line of each, not a first-match replacement — the model
  cannot tell a wrong-region edit from the one it asked for, and neither can
  the human reading the diff.
- A rejected edit carries the file's current content around the region the
  caller was aiming at, so the retry lands without a separate re-read.
- An edit against a file that changed since the agent read it is rejected by
  :class:`FileStateTracker` rather than clobbering the newer contents.

``FileStateTracker`` is a port of the C++ tracker in
``cpp/include/gaia/file_tools.h``; the two trees keep the same ledger
semantics deliberately.
"""

import difflib
import hashlib
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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

_SHORT_HASH_CHARS = 12


def hash_content(content: str) -> str:
    """Lowercase hex SHA-256 of ``content``."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def short_hash(content_hash: str) -> str:
    """First ``_SHORT_HASH_CHARS`` of a hash, for human-readable messages."""
    return content_hash[:_SHORT_HASH_CHARS]


def _key(file_path: str) -> str:
    """Ledger key: one entry per file however the caller spelled the path."""
    return os.path.normcase(os.path.realpath(str(file_path)))


@dataclass
class Divergence:
    """Result of comparing a file's current contents to what was read."""

    diverged: bool = False
    hash_at_read: str = ""
    hash_now: str = ""
    size_at_read: int = 0
    size_now: int = 0
    reason: str = ""


@dataclass
class _Record:
    content_hash: str
    size: int


class FileStateTracker:
    """Content-hash ledger of every file an agent has read.

    A later write can then tell "the model is editing what it saw" from "the
    file moved under it". No system prompt can prevent a stale write: by the
    time the model emits an edit, the read that justified it may be many turns
    old and a build step, a formatter, another agent, or the user may have
    changed the file since.

    Semantics (matching the C++ tracker):

    - A read records the SHA-256 of the file's **full** contents, so a
      line-range read still anchors the whole file.
    - An edit is rejected when a record exists and the contents now hash
      differently.
    - A file with **no** record is not blocked. Requiring a prior read would
      make the tools unusable for creating files, and an agent that never read
      the file has nothing stale to be wrong about.
    - A successful edit re-records the new contents, so consecutive edits work
      without an intervening read.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._records: Dict[str, _Record] = {}
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> "FileStateTracker":
        """Process-wide tracker shared by every file tool."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def record_read(self, file_path: str, content: str) -> str:
        """Record the contents an agent has just seen. Returns the hash."""
        digest = hash_content(content)
        with self._lock:
            self._records[_key(file_path)] = _Record(
                digest, len(content.encode("utf-8"))
            )
        return digest

    def record_write(self, file_path: str, content: str) -> str:
        """Record contents an agent has just written.

        Identical to :meth:`record_read` but named for the call site so intent
        stays readable.
        """
        return self.record_read(file_path, content)

    def check(self, file_path: str, current_content: str) -> Divergence:
        """Compare ``current_content`` against the recorded read.

        Returns ``diverged=False`` when there is no record for the path.
        """
        with self._lock:
            record = self._records.get(_key(file_path))
        if record is None:
            return Divergence()

        digest = hash_content(current_content)
        size_now = len(current_content.encode("utf-8"))
        if digest == record.content_hash:
            return Divergence(
                hash_at_read=short_hash(record.content_hash),
                hash_now=short_hash(digest),
                size_at_read=record.size,
                size_now=size_now,
            )

        return Divergence(
            diverged=True,
            hash_at_read=short_hash(record.content_hash),
            hash_now=short_hash(digest),
            size_at_read=record.size,
            size_now=size_now,
            reason=(
                f"contents hashed {short_hash(record.content_hash)} when read "
                f"and {short_hash(digest)} now "
                f"({record.size} -> {size_now} bytes)"
            ),
        )

    def has_record(self, file_path: str) -> bool:
        with self._lock:
            return _key(file_path) in self._records

    def forget(self, file_path: str) -> None:
        """Drop the record for a path (e.g. the file was deleted or renamed)."""
        with self._lock:
            self._records.pop(_key(file_path), None)

    def clear(self) -> None:
        """Drop every record. Intended for tests and session resets."""
        with self._lock:
            self._records.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._records)


def record_read(file_path: str, content: str) -> str:
    """Record a read against the process-wide tracker."""
    return FileStateTracker.instance().record_read(file_path, content)


def record_write(file_path: str, content: str) -> str:
    """Record a write against the process-wide tracker."""
    return FileStateTracker.instance().record_write(file_path, content)


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

    Callers own their own security checks (path allowlist, size limits,
    backups) — this function only decides *what* the new contents should be.
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

    divergence = FileStateTracker.instance().check(file_path, current_content)
    if divergence.diverged:
        error = _error(
            f"Edit rejected: {file_path} changed on disk after it was read — "
            f"{divergence.reason}. Nothing was written. The file's current "
            "content is included as `current_content`; reissue the edit against "
            "that, not against what you read earlier.",
            file_path,
            len(_match_offsets(current_content, old_content)),
            {
                "stale": True,
                "hash_at_read": divergence.hash_at_read,
                "hash_now": divergence.hash_now,
                **_excerpt(current_content, old_content),
            },
        )
        # Returning the content *is* a read, so re-anchor. Without this the
        # ledger still holds the superseded hash and the corrected retry is
        # rejected as stale too — forever.
        FileStateTracker.instance().record_read(file_path, current_content)
        return None, error

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
