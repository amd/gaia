# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where GAIA keeps its on-disk state.

A leaf module — standard library only — so :mod:`gaia.logger`, which every
other module imports, can resolve the home without an import cycle.
:mod:`gaia.config` re-exports :func:`gaia_home` and :func:`default_gaia_home`.
"""

import os
from pathlib import Path
from typing import List

#: The Agent UI's chat data below the GAIA home: every chat's database, the
#: files attached to messages, and the copy of each document dragged into a
#: chat. Never granted as a folder: a chat opens only what is attached to it
#: (see ``PathValidator.chat_data_root_of``).
CHAT_DATA_SUBDIR = Path("chat")

#: Where the Agent UI keeps its copy of each document dragged into a chat.
DOCUMENT_COPIES_SUBDIR = CHAT_DATA_SUBDIR / "documents"


def gaia_home() -> Path:
    """Return the directory that holds GAIA's on-disk state.

    Precedence:

    1. ``GAIA_HOME`` relocates the whole tree.
    2. ``GAIA_CONFIG_DIR`` keeps the existing config-directory override.
    3. ``~/.gaia``.
    """
    for env_var in ("GAIA_HOME", "GAIA_CONFIG_DIR"):
        raw = os.environ.get(env_var)
        if raw is None:
            continue
        raw = raw.strip()
        if not raw:
            continue
        return Path(os.path.expandvars(os.path.expanduser(raw)))
    return default_gaia_home()


def default_gaia_home() -> Path:
    """``~/.gaia``: the home of a process with no ``GAIA_HOME``, and so of the
    machine's default daemon and its Lemonade Server."""
    return Path.home() / ".gaia"


def chat_data_dirs() -> List[Path]:
    """Every folder the Agent UI may keep chat data in, read at call time.

    The Agent UI resolves its home from ``GAIA_HOME`` alone, so with only
    ``GAIA_CONFIG_DIR`` set its chat data sits under ``~/.gaia`` rather than
    :func:`gaia_home`; both are listed.
    """
    dirs = [gaia_home() / CHAT_DATA_SUBDIR]
    if not (os.environ.get("GAIA_HOME") or "").strip():
        default = default_gaia_home() / CHAT_DATA_SUBDIR
        if default not in dirs:
            dirs.append(default)
    return dirs
