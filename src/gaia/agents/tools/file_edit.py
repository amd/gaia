#!/usr/bin/env python
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared rules for GAIA's file-changing tools.

The rules live here so the separate implementations in ``file_io_tools`` and
``file_tools`` cannot drift apart.

**Read before edit.** :class:`FileReadRecord` is one agent's record of the
files it has read. A tool that would change an existing file refuses when the
file isn't in the record, or when its mtime or size moved since the read.
Creating a file needs no read, and what the agent wrote or edited itself stays
unlocked.

**No repeat within a turn.** An edit identical to one already applied to the
same file this turn — same ``old_content``, same ``new_content`` — is refused.
When ``old_content`` sits inside ``new_content`` a repeat still matches, and
"milk" -> "oat milk" twice leaves "oat oat milk".

**Match and replace.** Every ``edit_*`` tool routes through
:func:`apply_unique_replacement`:

- ``old_content`` must match **exactly once**. Two matches is an error naming
  the count and the line of each, not a first-match replacement — the model
  cannot tell a wrong-region edit from the one it asked for, and neither can
  the human reading the diff.
- ``new_content`` must not repeat the lines just outside ``old_content``,
  above, below, or one from each side at its edges. That is a whole-file
  rewrite squeezed into one span, and splicing it in would leave those lines
  twice.
- A rejected edit carries the file's current content around the region the
  caller was aiming at, so the retry lands without a separate re-read.
"""

import difflib
import os
import re
import stat
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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

# Neighbouring lines on each side checked for a repeat inside new_content.
NEIGHBOUR_LINES = 50

# The repeats from both sides together must reach this many lines and
# characters, so a single echoed line or a run of braces can recur legitimately.
MIN_REPEATED_LINES = 2
MIN_REPEATED_CHARS = 8

# Lines of context around the change in a successful edit's ``after`` excerpt.
AFTER_RADIUS = 2

# Similarity a line needs against the probe line to anchor a not-found excerpt.
_ANCHOR_THRESHOLD = 0.6

# Longest before/after text quoted in a repeated-edit refusal.
MAX_REPEAT_EXAMPLE_CHARS = 200

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


def turn_of(host: Any) -> Any:
    """``host``'s turn counter; a host with no turns is one long turn."""
    return getattr(host, "_turn_seq", None)


def _repeat_outcome(old_content: str, new_content: str) -> str:
    """What reapplying the edit would do to the text it wrote, or ""."""
    if new_content.count(old_content) != 1:
        return ""
    again = new_content.replace(old_content, new_content, 1)
    if len(again) > MAX_REPEAT_EXAMPLE_CHARS:
        return ""
    return f"; applying it again would change {new_content!r} to {again!r}"


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

    It also holds the edits applied this turn, so an identical one can be
    refused (:meth:`repeat_refusal`). That part resets when the turn moves on.
    """

    def __init__(self) -> None:
        self._seen: Dict[str, Stamp] = {}
        self._edits: Set[Tuple[str, str, str]] = set()
        self._edits_turn: Any = None
        self._lock = threading.Lock()

    def note_edit(
        self, file_path: Any, old_content: str, new_content: str, turn: Any
    ) -> None:
        """Record an edit written to ``file_path`` in ``turn``."""
        with self._lock:
            if turn != self._edits_turn:
                self._edits = set()
                self._edits_turn = turn
            self._edits.add((_key(file_path), old_content, new_content))

    def repeat_refusal(
        self, file_path: Any, old_content: str, new_content: str, turn: Any
    ) -> Optional[Dict[str, Any]]:
        """Why this edit must not run again, or ``None`` when it wasn't applied yet."""
        with self._lock:
            applied = turn == self._edits_turn and (
                (_key(file_path), old_content, new_content) in self._edits
            )
        if not applied:
            return None
        return _read_required(
            f"This exact edit was already applied to {file_path} this turn"
            f"{_repeat_outcome(old_content, new_content)}. Nothing was written. "
            "Read the file with read_file to see its current content.",
            file_path,
            "already_applied",
        )

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
    """A ``@tool(preflight=...)`` check: refuse a blind or repeat edit before the prompt.

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
            if validator is not None and not validator.is_path_allowed(
                str(target), prompt_user=False
            ):
                # No answer could grant it, so refuse before the approval prompt.
                if validator.grant_refusal_reason(str(target)):
                    reason = validator.access_denied_message(str(target))
                    validator.audit_write("write", str(target), 0, "denied", reason)
                    return {"status": "error", "error": reason}
                # Askable: the tool's own path-access question comes next.
                return None
            # Write-blocked, the tool's own refusal is the true one, and it
            # doesn't reveal whether a file exists there.
            if validator is not None and validator.is_write_blocked(str(target))[0]:
                return None
            reads = file_read_record(host)
            refusal = reads.refusal(target, verb)
            old, new = tool_args.get("old_content"), tool_args.get("new_content")
            if refusal is None and isinstance(old, str) and isinstance(new, str):
                refusal = reads.repeat_refusal(target, old, new, turn_of(host))
            return refusal
        except (KeyError, TypeError, ValueError, OSError) as e:
            # Malformed arguments or an unstat-able path: the tool body reports it.
            logger.debug("read-first preflight deferred to the tool: %s", e)
            return None

    # The confirmation prompt names this path, so it shows where the write lands.
    preflight.target_of = target_of
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


def _retarget_newlines(block: str, reference: str) -> str:
    """*block* with the line endings *reference* uses.

    The caller composes ``old_content`` from text it was shown, which may have
    been normalised on the way, while the file on disk keeps whatever endings
    it was authored with. A CRLF-vs-LF difference is never a semantic one, so
    it must not be the reason an otherwise exact edit is refused.
    """
    flat = block.replace("\r\n", "\n")
    return flat.replace("\n", "\r\n") if "\r\n" in reference else flat


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


def _nonblank(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _contains_run(lines: List[str], run: List[str]) -> bool:
    width = len(run)
    return any(lines[i : i + width] == run for i in range(len(lines) - width + 1))


def _nearest_run(new_lines: List[str], neighbours: List[str], above: bool) -> List[str]:
    """The longest run of ``neighbours`` next to the match that ``new_lines`` has."""
    best: List[str] = []
    for width in range(1, min(len(neighbours), len(new_lines)) + 1):
        run = neighbours[-width:] if above else neighbours[:width]
        if not _contains_run(new_lines, run):
            break
        best = run
    # A lone echoed line counts only where a paste puts it: at the edge.
    if len(best) == 1 and new_lines[0 if above else -1] != best[0]:
        return []
    return best


def _repeated_neighbours(
    current_content: str, offset: int, old_content: str, new_content: str
) -> List[str]:
    """Lines just outside the matched span that ``new_content`` repeats.

    Each side counts its longest run that starts next to the match, so one
    line echoed from above plus one from below is caught as well as a run.
    """
    new_lines = _nonblank(new_content)
    old_lines = set(_nonblank(old_content))
    # Whole lines only: the rest of a line the match starts or ends inside is
    # part of the match's own line, not a neighbour.
    head, _, _ = current_content[:offset].rpartition("\n")
    rest = current_content[offset + len(old_content) :]
    tail = rest if old_content.endswith("\n") else rest.partition("\n")[2]
    runs = (
        _nearest_run(new_lines, _nonblank(head)[-NEIGHBOUR_LINES:], above=True),
        _nearest_run(new_lines, _nonblank(tail)[:NEIGHBOUR_LINES], above=False),
    )
    repeated = [
        line
        for run in runs
        for line in run
        if line not in old_lines and any(ch.isalnum() for ch in line)
    ]
    if len(repeated) < MIN_REPEATED_LINES:
        return []
    if sum(len(line) for line in repeated) < MIN_REPEATED_CHARS:
        return []
    return repeated


def after_excerpt(current_content: str, updated_content: str) -> Dict[str, Any]:
    """The edited lines as they now read, so the caller sees what it wrote."""
    prefix = len(os.path.commonprefix([current_content, updated_content]))
    room = min(len(current_content), len(updated_content)) - prefix
    suffix = len(os.path.commonprefix([current_content[::-1], updated_content[::-1]]))
    changed_end = len(updated_content) - min(suffix, room)
    first = updated_content.count("\n", 0, prefix)
    last = updated_content.count("\n", 0, max(prefix, changed_end - 1))
    lines = updated_content.splitlines()
    start = max(0, first - AFTER_RADIUS)
    end = min(len(lines), last + AFTER_RADIUS + 1)
    text, _ = _clip("\n".join(lines[start:end]))
    return {"after": text, "after_start_line": start + 1, "after_end_line": end}


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

    if len(offsets) == 1 and "\n" not in old_content and "\r\n" in current_content:
        # A one-line old_content carries no line ending of its own, so a
        # multi-line LF new_content would leave a CRLF line with mixed endings.
        # Use the ending of the line it matched on. A multi-line old_content
        # already says which endings it matched, so it is left alone: with no
        # CRLF it can only have matched an LF region, and with CRLF the caller
        # is choosing endings deliberately.
        eol = current_content.find("\n", offsets[0])
        if eol == -1 or current_content[eol - 1] == "\r":
            new_content = _retarget_newlines(new_content, current_content)

    if not offsets:
        # Retry once with old_content's line endings rewritten to the file's.
        # Only a line-ending difference can be rescued this way, and only when
        # it then matches exactly one place, so nothing ambiguous is guessed at.
        retargeted = _retarget_newlines(old_content, current_content)
        if retargeted != old_content:
            retargeted_offsets = _match_offsets(current_content, retargeted)
            if len(retargeted_offsets) == 1:
                old_content = retargeted
                new_content = _retarget_newlines(new_content, current_content)
                offsets = retargeted_offsets

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
    repeated = _repeated_neighbours(current_content, offset, old_content, new_content)
    if repeated:
        return None, _error(
            f"new_content repeats lines that sit outside old_content in "
            f"{file_path} (listed in `repeated_lines`) — nothing was written, "
            "because the file would end up with those lines twice. Pass only "
            "the text that replaces old_content. If those lines belong in "
            "new_content, add them to old_content too, so they are replaced "
            "instead of copied. To replace the whole file, use write_file.",
            file_path,
            1,
            {"repeated_lines": repeated, **_excerpt(current_content, old_content)},
        )

    updated = (
        current_content[:offset]
        + new_content
        + current_content[offset + len(old_content) :]
    )
    return updated, None
