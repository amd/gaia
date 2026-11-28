# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Names for the private copies of files attached to a chat.

An attachment is stored as ``<name><ext>``, so the agent reads and quotes the
name the user knows it by. Only when that name is already taken does the copy
become ``<id>_<name><ext>``, and answers cite ``<name><ext>`` either way.

The stored name keeps the user's spaces, accents and non-Latin scripts. The
browser link percent-encodes it, since a Markdown link ends at a space or ``)``
and only one plain URL path segment can be served from ``uploads_dir()``.
"""

import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

_ID_CHARS = 12
_NAME_CHARS = 80
# Leaves room under the 255-byte file name limit for the id prefix and extension.
_NAME_BYTES = 200
_FALLBACK_NAME = "attachment"
_COPY_NAME_RE = re.compile(rf"[0-9a-f]{{{_ID_CHARS}}}_(?P<name>.+)")
_SHORT_NAME_RE = re.compile(r"~\d")

# Same rules the document library uses: legal across platforms, and never a
# name that reads as a path, a shell fragment or a control character.
_ILLEGAL_NAME_CHARS = '<>:"/\\|?*'
_SHELL_QUOTING_CHARS = "'`"
_SLASH_LOOKALIKES = "\u2044\u2215\u29f5\u29f8\u29f9\ufe68\uff0f\uff3c"
_JOINERS = "\u200c\u200d"


def unsafe_name_char(c: str) -> bool:
    """Whether *c* must not appear in a stored attachment or document name."""
    return (
        c in _ILLEGAL_NAME_CHARS
        or c in _SHELL_QUOTING_CHARS
        or c in _SLASH_LOOKALIKES
        or (
            unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp")
            and c not in _JOINERS
        )
    )


def _clip_name(stem: str) -> str:
    """Truncate *stem* to the character and UTF-8 byte limits."""
    stem = stem[:_NAME_CHARS]
    encoded = stem.encode("utf-8")
    if len(encoded) > _NAME_BYTES:
        stem = encoded[:_NAME_BYTES].decode("utf-8", "ignore")
    return stem


def upload_copy_stem(original_name: str) -> str:
    """Return the name, without extension, to store an attachment under.

    Spaces, accents and non-Latin scripts stay, so the agent and the user see
    the name they picked. Characters that would read as a path, a shell
    fragment or a control character become ``_``, and a name with nothing
    usable left becomes ``attachment``.
    """
    base = re.split(r"[\\/]", original_name)[-1]
    stem = "".join(
        "_" if unsafe_name_char(c) else c
        for c in unicodedata.normalize("NFC", Path(base).stem)
    )
    stem = _clip_name(stem).strip(" ._-")
    return stem or _FALLBACK_NAME


def upload_url_name(file_name: str) -> str:
    """Percent-encode *file_name* as the one path segment an upload link carries."""
    return quote(file_name, safe="")


# Names Windows maps to a device whatever the extension, compared before the first dot.
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{dev}{n}" for dev in ("COM", "LPT") for n in "0123456789\u00b9\u00b2\u00b3"}
)


def _copy_name(stem: str, ext: str) -> str:
    """Return ``<stem><ext>``, refusing anything but one plain file name."""
    name = f"{stem}{ext}"
    if (
        not stem
        or name.startswith(".")
        or any(c in "/\\:" or ord(c) < 32 or ord(c) == 127 for c in name)
        or name != name.strip(" .")
    ):
        raise ValueError(f"{name!r} is not a plain file name for a copy")
    return name


def _plain_name_usable(name: str) -> bool:
    """Whether a copy can go by *name* alone, with no id in front.

    A device name would open the device on Windows, a name shaped like a
    Windows short name (``REPORT~1``) can open a different file, and a name
    that already looks like ``<id>_<name>`` would be cited without its first part.
    """
    device = name.split(".", 1)[0].rstrip(" ").upper()
    return (
        device not in _DEVICE_NAMES
        and not _SHORT_NAME_RE.search(name)
        and not _COPY_NAME_RE.fullmatch(name)
    )


def reserve_copy_path(
    directory: Path,
    stem: str,
    ext: str,
    taken: Optional[Callable[[Path], bool]] = None,
) -> Path:
    """Create an empty file for a new copy and return its path.

    The file is ``<stem><ext>`` when that name is free, else ``<id>_<stem><ext>``.
    Creating it exclusively means two uploads never share a name, even at once.
    *taken* marks names still claimed by something other than a file on disk.
    The caller writes to the path, and removes it if the write fails.
    """
    plain = _copy_name(stem, ext)
    candidates = [plain] if _plain_name_usable(plain) else []
    candidates += [f"{uuid.uuid4().hex[:_ID_CHARS]}_{plain}" for _ in range(3)]
    for name in candidates:
        path = directory / name
        if taken is not None and taken(path):
            continue
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"No free name for {plain} in {directory}")


def place_copy(directory: Path, stem: str, ext: str, content: bytes) -> Path:
    """Store *content* as a copy and return its path.

    A file already stored under the plain name with the same bytes is reused,
    so attaching one file to many chats keeps one copy with the plain name.
    """
    plain = directory / _copy_name(stem, ext)
    if (
        _plain_name_usable(plain.name)
        and plain.is_file()
        and not plain.is_symlink()
        and plain.read_bytes() == content
    ):
        os.utime(plain)  # keep the age-based media prune from taking it
        return plain
    path = reserve_copy_path(directory, stem, ext)
    try:
        path.write_bytes(content)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return path


def upload_original_name(file_path: str) -> Optional[str]:
    """Return the name a chat attachment's copy stands for, or None if it isn't one.

    Only files directly inside ``chat/uploads`` count, so a user's own
    ``0123456789ab_report.pdf`` elsewhere keeps its full name.
    """
    path = Path(file_path)
    if path.parent.name != "uploads" or path.parent.parent.name != "chat":
        return None
    match = _COPY_NAME_RE.fullmatch(path.name)
    return match.group("name") if match else None


def display_file_name(file_path: str) -> str:
    """Return the name to show or cite for *file_path*."""
    return upload_original_name(file_path) or Path(file_path).name
