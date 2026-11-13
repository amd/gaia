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
from typing import Optional

_ID_CHARS = 12
_NAME_CHARS = 80
_FALLBACK_NAME = "attachment"
_COPY_NAME_RE = re.compile(rf"[0-9a-f]{{{_ID_CHARS}}}_(?P<name>.+)")


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


def reserve_copy_path(directory: Path, stem: str, ext: str) -> Path:
    """Create an empty file for a new copy and return its path.

    The file is ``<stem><ext>`` when that name is free, else ``<id>_<stem><ext>``.
    Creating it exclusively means two uploads never share a name, even at once.
    The caller writes to the path, and removes it if the write fails.
    """
    candidates = [f"{stem}{ext}"] + [
        f"{uuid.uuid4().hex[:_ID_CHARS]}_{stem}{ext}" for _ in range(3)
    ]
    for name in candidates:
        path = directory / name
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"No free name for {stem}{ext} in {directory}")


def place_copy(directory: Path, stem: str, ext: str, content: bytes) -> Path:
    """Store *content* as a copy and return its path.

    A file already stored under the plain name with the same bytes is reused,
    so attaching one file to many chats keeps one copy with the plain name.
    """
    plain = directory / f"{stem}{ext}"
    if plain.is_file() and plain.read_bytes() == content:
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
