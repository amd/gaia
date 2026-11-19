# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guard for the exporters that write committed artifacts into a checkout."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

MODULE_CHECKOUT = Path(__file__).resolve().parents[3]


def _checkout_of(path: Path) -> Optional[Path]:
    for candidate in (path, *path.parents):
        if (candidate / "src" / "gaia" / "__init__.py").is_file():
            return candidate
    return None


def require_matching_checkout(module: str) -> Optional[int]:
    """Return None to proceed, or exit code 2 after printing why not.

    ``gaia`` is editable-installed from whichever worktree ran ``pip install -e``
    last, so an exporter run elsewhere would write another checkout's routes
    into that checkout.
    """
    imported = MODULE_CHECKOUT
    cwd = Path.cwd().resolve()
    here = _checkout_of(cwd)
    if here is None:
        print(
            f"gaia is imported from {imported}, but {cwd} is not inside a GAIA "
            f"checkout.\nRun it from the repo root: cd {imported} && "
            f"python -m {module}",
            file=sys.stderr,
        )
        return 2
    if here != imported:
        print(
            f"gaia is imported from {imported}, but you are in the checkout "
            f"{here}.\nRun it from {imported}, or from {here} with "
            f"PYTHONPATH set to {here / 'src'}: python -m {module}",
            file=sys.stderr,
        )
        return 2
    return None
