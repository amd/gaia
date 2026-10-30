# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Session ids that are safe to use as file names."""

import re
from typing import Any

_SESSION_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


def validate_session_id(session_id: Any) -> None:
    """Raise ValueError unless ``session_id`` is safe to use as a file name."""
    if (
        not isinstance(session_id, str)
        or not _SESSION_ID_RE.fullmatch(session_id)
        or not session_id.strip(".")
    ):
        raise ValueError(
            f"Invalid session_id {session_id!r}: use 1-128 characters from "
            "A-Z, a-z, 0-9, '.', '_' and '-' (not only dots)."
        )
