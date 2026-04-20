# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""FileToolsMixin — local file read/write/edit/search/glob/diff.

Implements the six tools from §15.2 of docs/plans/coder-agent.mdx. All tools
are pure-Python (no external binaries) so the mixin works on any supported
platform with only the stdlib.

Design notes
------------
* ``read_file`` raises ``FileNotFoundError`` on a missing path rather than
  returning a "status=error" dict — §2 principle 3 (fail loudly). Structured
  error envelopes belong at agent boundaries, not inside the tool body.
* ``edit_file`` mirrors Claude Code's Edit semantics — exact-match, uniqueness
  enforcement, opt-in ``replace_all``.
* ``search_code`` uses Python ``re`` rather than shelling out to ripgrep so the
  mixin stays portable and deterministic in tests.
"""

from __future__ import annotations

import difflib
import fnmatch
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, TypedDict

from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


class WriteResult(TypedDict):
    """Result of a :meth:`write_file` call."""

    written_bytes: int
    path: str


class EditResult(TypedDict):
    """Result of an :meth:`edit_file` call."""

    path: str
    replacements: int


class SearchHit(TypedDict):
    """One match from :meth:`search_code` / :meth:`grep`."""

    path: str
    line_number: int
    line_text: str


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class FileToolsMixin:
    """Mixin providing the six file tools in §15.2 of the coder plan."""

    def register_file_tools(self) -> None:
        """Register ``read_file`` / ``write_file`` / ``edit_file`` / ``search_code``
        / ``glob`` / ``generate_diff`` in the agent tool registry."""

        @tool
        def read_file(
            path: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
        ) -> str:
            """Read a text file, optionally slicing an inclusive 1-indexed line range.

            Raises:
                FileNotFoundError: if ``path`` does not exist.
            """
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"read_file: {path!r} does not exist")
            text = p.read_text(encoding="utf-8")
            if start_line is None and end_line is None:
                return text
            lines = text.splitlines(keepends=True)
            start = max(1, start_line or 1)
            end = end_line if end_line is not None else len(lines)
            # Convert 1-indexed inclusive → 0-indexed slice
            return "".join(lines[start - 1 : end])

        @tool
        def write_file(path: str, content: str) -> WriteResult:
            """Write ``content`` to ``path``, creating parent directories.

            Returns the byte count written and the absolute path, so the caller
            has something meaningful to log without a second stat.
            """
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            p.write_bytes(data)
            return {"written_bytes": len(data), "path": str(p)}

        @tool
        def edit_file(
            path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
        ) -> EditResult:
            """Replace ``old_string`` with ``new_string`` in ``path``.

            Mirrors Claude Code's Edit semantics:
            * ``old_string`` must match exactly at least once.
            * If ``replace_all=False`` the match must be unique.

            Raises:
                FileNotFoundError: if ``path`` does not exist.
                ValueError: ``"old_string not found"`` when absent,
                    ``"old_string not unique"`` when present more than once and
                    ``replace_all`` is False.
            """
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"edit_file: {path!r} does not exist")
            text = p.read_text(encoding="utf-8")
            count = text.count(old_string)
            if count == 0:
                raise ValueError(f"old_string not found in {path!r}")
            if count > 1 and not replace_all:
                raise ValueError(
                    f"old_string not unique in {path!r} (matched {count} times)"
                )
            n = -1 if replace_all else 1
            updated = text.replace(old_string, new_string, n)
            p.write_text(updated, encoding="utf-8")
            replacements = count if replace_all else 1
            return {"path": str(p), "replacements": replacements}

        @tool
        def search_code(
            pattern: str,
            path: str = ".",
            glob: Optional[str] = None,
            case_sensitive: bool = True,
            max_matches: int = 100,
        ) -> List[SearchHit]:
            """Regex search file contents under ``path``.

            Pure-Python (``re`` + ``pathlib``) so behaviour is deterministic on
            every platform — no dependency on ripgrep/grep. Binary/unreadable
            files are skipped silently (log at DEBUG).
            """
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
            base = Path(path)
            hits: List[SearchHit] = []
            for file_path in _iter_text_files(base, glob):
                try:
                    for lineno, line in enumerate(
                        file_path.read_text(encoding="utf-8").splitlines(), start=1
                    ):
                        if regex.search(line):
                            hits.append(
                                {
                                    "path": file_path.as_posix(),
                                    "line_number": lineno,
                                    "line_text": line,
                                }
                            )
                            if len(hits) >= max_matches:
                                return hits
                except (UnicodeDecodeError, OSError) as e:
                    logger.debug("search_code: skipping %s (%s)", file_path, e)
            return hits

        @tool
        def glob(pattern: str, path: str = ".") -> List[str]:
            """List files matching ``pattern`` under ``path`` (POSIX-style output).

            Uses ``pathlib.Path.glob``; ``**`` is supported for recursive
            searches just as in the underlying stdlib call.
            """
            base = Path(path)
            return sorted(p.as_posix() for p in base.glob(pattern))

        @tool
        def generate_diff(a_path: str, b_path: str, unified: int = 3) -> str:
            """Unified diff between ``a_path`` and ``b_path`` (``difflib``).

            Returns an empty string when the files are identical.
            """
            a_lines = Path(a_path).read_text(encoding="utf-8").splitlines(keepends=True)
            b_lines = Path(b_path).read_text(encoding="utf-8").splitlines(keepends=True)
            diff = difflib.unified_diff(
                a_lines,
                b_lines,
                fromfile=a_path,
                tofile=b_path,
                n=unified,
            )
            return "".join(diff)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_text_files(base: Path, glob_pattern: Optional[str]):
    """Yield files under ``base`` that match ``glob_pattern`` (or everything)."""
    if base.is_file():
        if glob_pattern is None or fnmatch.fnmatch(base.name, glob_pattern):
            yield base
        return
    for root, _dirs, files in os.walk(base):
        for name in files:
            if glob_pattern is not None and not fnmatch.fnmatch(name, glob_pattern):
                continue
            yield Path(root) / name
