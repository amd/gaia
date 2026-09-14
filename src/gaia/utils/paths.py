# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One safe way to turn an untrusted string into a single path segment.

Anywhere GAIA joins a name it did not choose — a hub manifest's
``artifact.filename``, an archive member, a caller-supplied basename — onto a
directory it did choose, ``Path(dir) / name`` is a write primitive, not a join.
``pathlib`` collapses ``.`` back to the directory, leaves ``..`` at its parent,
and lets an absolute name (or a bare Windows drive prefix such as ``C:evil.zip``)
replace the directory outright.

:func:`safe_path_segment` is the one check those call sites should share. It is
deliberately stricter than "no slashes": it also refuses drive-relative and UNC
shapes, NT device names, control characters, and the trailing dots/spaces
Windows silently strips — all of which survive a naive separator scan.
"""

from __future__ import annotations

from pathlib import PureWindowsPath

__all__ = ["UnsafePathSegment", "safe_path_segment"]

#: Longest segment most filesystems accept.
MAX_SEGMENT_LENGTH = 255

#: NT device names. ``open("NUL")`` succeeds on Windows in any directory.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


class UnsafePathSegment(ValueError):
    """A caller-supplied name is not usable as a single path segment.

    Callers translate this into their own boundary error (``SkillValidationError``,
    ``InstallError``, an HTTP 400) rather than letting it escape as a bare
    ``ValueError``.
    """


def safe_path_segment(value: str, *, what: str = "filename", origin: str = "") -> str:
    """Return *value* unchanged, or raise if it is not one safe path segment.

    Args:
        value: The untrusted name.
        what: What the name is, quoted back in the error (e.g. ``"artifact filename"``).
        origin: Optional "who supplied it" clause for the error (e.g. a hub URL).

    Returns:
        The name, safe to join onto a directory GAIA chose.

    Raises:
        UnsafePathSegment: the name is empty, over-long, carries a path
            separator, drive or root, is ``.``/``..``, names an NT device, or
            contains a character the filesystem would rewrite.
    """
    source = f" (from {origin})" if origin else ""

    def refuse(why: str) -> "UnsafePathSegment":
        return UnsafePathSegment(
            f"Refusing to use {what} {value!r}{source}: {why}. It must be a single "
            "file name — no directory separators, no '.', no '..', no drive letter "
            "and no leading '/' or ''. Nothing was written. If this came from a "
            "hub or catalog, report the manifest as malformed."
        )

    if not isinstance(value, str) or not value:
        raise refuse("it is empty")
    if len(value) > MAX_SEGMENT_LENGTH:
        raise refuse(
            f"it is {len(value)} characters and the limit is {MAX_SEGMENT_LENGTH}"
        )
    if value in (".", ".."):
        raise refuse("it names a directory rather than a file")
    if "/" in value or "\\" in value:
        raise refuse("it contains a directory separator")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise refuse("it contains a control character")
    if ":" in value:
        # 'C:evil.zip' has no separator but still re-roots the join on Windows.
        raise refuse("it contains ':', which names a drive or an NTFS data stream")
    if value != value.strip() or value.endswith("."):
        raise refuse(
            "it has leading/trailing whitespace or a trailing dot, which Windows strips"
        )
    if value.split(".", 1)[0].lower() in _WINDOWS_DEVICE_NAMES:
        raise refuse("it names a Windows device")

    # Belt and braces: whatever the character-level rules missed, pathlib must
    # still read this as one relative component.
    pure = PureWindowsPath(value)
    if pure.drive or pure.root or len(pure.parts) != 1:
        raise refuse("it does not resolve to a single relative path component")

    return value
