# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The frozen ``gaia`` executable standing in for the interpreter GAIA relaunches.

GAIA starts parts of itself as ``[sys.executable, "-m", <module>, ...]`` (the
daemon, the Agent UI MCP server, ``gaia mcp start``) or ``-c <code>``
(``--attempts`` children). In the frozen app ``sys.executable`` is the ``gaia``
executable itself, so its entry hands those two forms to :func:`dispatch` before
the CLI parses argv. Only GAIA's own modules, and uvicorn, which ``gaia api
start --background`` runs, start this way: the frozen app has no packages to
install and no other Python to offer.
"""

from __future__ import annotations

import runpy
import sys
from typing import Optional, Sequence


def is_frozen() -> bool:
    """Whether this is the PyInstaller build, where ``sys.executable`` is the app."""
    return bool(getattr(sys, "frozen", False))


def _runs_here(name: str) -> bool:
    return name in ("gaia", "uvicorn") or name.startswith("gaia.")


def dispatch(argv: Sequence[str]) -> Optional[int]:
    """Exit status when *argv* is a ``-m``/``-c`` relaunch, else None for the CLI."""
    if len(argv) < 2 or argv[0] not in ("-m", "-c"):
        return None
    flag, target, rest = argv[0], argv[1], list(argv[2:])
    if flag == "-m":
        if not _runs_here(target):
            print(
                f"gaia: `-m {target}` can't run here. This GAIA build runs only "
                "its own modules and can't install packages.",
                file=sys.stderr,
            )
            return 2
        sys.argv = [target, *rest]
        runpy.run_module(target, run_name="__main__", alter_sys=True)
        return 0
    sys.argv = ["-c", *rest]
    # As CPython's -c: an empty first entry, which the attempts child removes.
    sys.path.insert(0, "")
    code = compile(target, "<string>", "exec")
    # GAIA's own -c relaunch, run as `python -c` would run it.
    # pylint: disable-next=exec-used
    exec(code, {"__name__": "__main__"})  # nosec B102
    return 0
