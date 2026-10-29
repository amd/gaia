# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Make a real folder this user can read but not create files in."""

from __future__ import annotations

import getpass
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest


@contextmanager
def cannot_create_files_in(folder: Path) -> Iterator[Path]:
    """Deny creating files in *folder*: a Windows ACL deny, or chmod 0500 on POSIX.

    The Windows deny leaves the read-only attribute alone, so ``os.access``
    still calls the folder writable.
    """
    if os.name != "nt":
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        folder.chmod(0o500)
        try:
            yield folder
        finally:
            folder.chmod(0o700)
        return
    user = getpass.getuser()
    deny = subprocess.run(
        ["icacls", str(folder), "/deny", f"{user}:(WD,AD)"],
        capture_output=True,
        text=True,
    )
    assert deny.returncode == 0, deny.stdout + deny.stderr
    try:
        yield folder
    finally:
        restore = subprocess.run(
            ["icacls", str(folder), "/remove:d", user],
            capture_output=True,
            text=True,
        )
        assert restore.returncode == 0, restore.stdout + restore.stderr
