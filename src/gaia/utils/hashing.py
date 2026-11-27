# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SHA-256 of a file, read in chunks so a large file never sits in memory."""

import hashlib
from pathlib import Path
from typing import Union

_CHUNK = 1 << 20


def sha256_file(path: Union[str, Path]) -> str:
    """Return the lowercase hex SHA-256 of the file at *path*.

    Raises OSError when the file cannot be read; callers decide what a
    missing file means.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()
