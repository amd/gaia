# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Names for the private copies of files attached to a chat.

An attachment is stored as ``<id>_<name><ext>`` so the agent can recognize it,
and answers cite ``<name><ext>``, the name the user knows it by.
"""

import re
import uuid
from pathlib import Path
from typing import Optional

_ID_CHARS = 12
_NAME_CHARS = 80
_FALLBACK_NAME = "attachment"
_COPY_NAME_RE = re.compile(rf"[0-9a-f]{{{_ID_CHARS}}}_(?P<name>.+)")


def upload_copy_name(original_name: str, ext: str) -> str:
    """Return a unique name to store an attachment under.

    Keeps only characters an attachment link can carry: the chat's Markdown
    links end at a space or ``)``, and the web UI's ``UPLOAD_URL_RE`` serves
    only ASCII letters, digits, ``.``, ``_`` and ``-``. A name with nothing
    usable left, such as one in another script, becomes ``attachment``.
    """
    base = re.split(r"[\\/]", original_name)[-1]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(base).stem)
    stem = stem.strip("._-")[:_NAME_CHARS].rstrip("._-")
    return f"{uuid.uuid4().hex[:_ID_CHARS]}_{stem or _FALLBACK_NAME}{ext}"


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
