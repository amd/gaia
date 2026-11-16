# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Names for the private copies of files attached to a chat.

An attachment is stored as ``<name><ext>``, so the agent reads and quotes the
name the user knows it by. Only when that name is already taken does the copy
become ``<id>_<name><ext>``, and answers cite ``<name><ext>`` either way.
"""

import os
import re
import uuid
from pathlib import Path
from typing import Callable, Optional

_ID_CHARS = 12
_NAME_CHARS = 80
_FALLBACK_NAME = "attachment"
_COPY_NAME_RE = re.compile(rf"[0-9a-f]{{{_ID_CHARS}}}_(?P<name>.+)")
_SHORT_NAME_RE = re.compile(r"~\d")


def upload_copy_stem(original_name: str) -> str:
    """Return the name, without extension, to store an attachment under.

    Keeps only characters an attachment link can carry: the chat's Markdown
    links end at a space or ``)``, and the web UI's ``UPLOAD_URL_RE`` serves
    only ASCII letters, digits, ``.``, ``_`` and ``-``. A name with nothing
    usable left, such as one in another script, becomes ``attachment``.
    """
    base = re.split(r"[\\/]", original_name)[-1]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(base).stem)
    stem = stem.strip("._-")[:_NAME_CHARS].rstrip("._-")
    return stem or _FALLBACK_NAME


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
        or name in (".", "..")
        or any(c in "/\\:" or ord(c) < 32 for c in name)
        or name != name.rstrip(" .")
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
