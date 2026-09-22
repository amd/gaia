# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Select a real interpreter for tools when the agent itself is frozen."""

import os
import sys
from pathlib import Path


def python_tool_runtime():
    """Return an explicit interpreter and environment for external scripts."""
    executable = os.environ.get("GAIA_PYTHON_EXECUTABLE")
    frozen = getattr(sys, "frozen", False)
    if not executable:
        if frozen:
            raise ValueError(
                "Python tools require GAIA_PYTHON_EXECUTABLE to name an installed "
                "Python interpreter; the frozen GAIA binary cannot execute scripts."
            )
        executable = sys.executable
    path = Path(executable)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("GAIA_PYTHON_EXECUTABLE must be an absolute executable path.")
    if frozen and path.resolve() == Path(sys.executable).resolve():
        raise ValueError(
            "GAIA_PYTHON_EXECUTABLE cannot point to the frozen GAIA binary."
        )
    env = dict(os.environ)
    if frozen:
        # PyInstaller prepends its private libraries. External Python must use
        # its own shared libraries, not the agent bundle's libpython/libssl.
        for key in ("LD_LIBRARY_PATH", "LIBPATH"):
            original = env.pop(key + "_ORIG", None)
            if original is None:
                env.pop(key, None)
            else:
                env[key] = original
    return str(path), env
