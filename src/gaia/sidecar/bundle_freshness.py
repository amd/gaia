# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep a one-file agent binary's unpacked files from aging out of the temp folder.

A PyInstaller one-file binary unpacks its data (the agent manifest, bundled
skills, compiled libraries) into the OS temp folder and reads it from there
for as long as it runs. OS temp cleaners delete files that have not been
touched for a while: macOS removes anything older than three days. An agent
left running past that loses files it still needs. The running agent marks
its own files as used on a schedule, so only a dead agent's leftovers age out.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

#: Well inside macOS's three-day cutoff, the shortest of the common cleaners.
REFRESH_INTERVAL_SECONDS = 6 * 3600


def _unpacked_bundle() -> Optional[Path]:
    """The one-file binary's unpack folder, or ``None`` for any other run."""
    bundle = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or bundle is None:
        return None
    bundle_path = Path(bundle).resolve()
    install_dir = Path(sys.executable).resolve().parent
    # A folder build unpacks beside its executable, where no cleaner looks.
    if bundle_path == install_dir or install_dir in bundle_path.parents:
        return None
    return bundle_path


def _touch_tree(root: Path) -> None:
    now = time.time()
    lstat_touch = os.utime in os.supports_follow_symlinks
    failed: list[str] = []
    paths = [root]
    for dirpath, dirnames, filenames in os.walk(root):
        paths.extend(Path(dirpath, name) for name in (*dirnames, *filenames))
    for path in paths:
        try:
            if lstat_touch:
                os.utime(path, (now, now), follow_symlinks=False)
            else:
                os.utime(path, (now, now))
        except OSError as exc:
            failed.append(f"{path}: {exc}")
    if failed:
        logger.warning(
            "Could not mark %d of the agent's unpacked files in %s as in use "
            "(first: %s). The OS temp cleaner may delete them; restart the "
            "agent if it starts failing to read its own files.",
            len(failed),
            root,
            failed[0],
        )


def _refresh_forever(root: Path) -> None:
    while True:
        time.sleep(REFRESH_INTERVAL_SECONDS)
        _touch_tree(root)


def keep_unpacked_bundle_fresh() -> Optional[threading.Thread]:
    """Refresh the unpacked files now and every few hours; ``None`` when not one-file."""
    root = _unpacked_bundle()
    if root is None:
        return None
    _touch_tree(root)
    keeper = threading.Thread(
        target=_refresh_forever,
        args=(root,),
        name="bundle-freshness",
        daemon=True,
    )
    keeper.start()
    return keeper
