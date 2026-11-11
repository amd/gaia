# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where GAIA keeps its on-disk state.

A leaf module — standard library only — so :mod:`gaia.logger`, which every
other module imports, can resolve the home without an import cycle.
:mod:`gaia.config` re-exports both functions.
"""

import os
from pathlib import Path


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
