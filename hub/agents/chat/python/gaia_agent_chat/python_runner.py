# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run a Python file the way ``python <file> [args]`` does, frozen or not.

A PyInstaller build carries no interpreter: ``sys.executable`` is the agent
binary, so ``[sys.executable, script]`` hands the script path to the agent's
own argument parser and nothing runs. A frozen agent re-launches itself with
:data:`RUN_PYTHON_FILE_FLAG` instead; its entry point
(``hub/agents/gaia/python/packaging/server.py``) sends that argv to
:func:`run_python_file` before it parses or imports anything else, and the
script runs on the binary's own bundled libraries.

Stdlib only: the frozen child imports this module before the user's script,
so anything it imports lands in that script's ``sys.modules``.
"""

from __future__ import annotations

import os
import runpy
import sys
import traceback
from typing import List, Optional, Sequence

#: Internal argv selector; never a user-facing flag.
RUN_PYTHON_FILE_FLAG = "--gaia-run-python-file"


def is_frozen() -> bool:
    """True inside a PyInstaller (or similar) frozen build."""
    return bool(getattr(sys, "frozen", False))


def python_command(script: os.PathLike | str, args: Sequence[str] = ()) -> List[str]:
    """The argv that runs *script* with *args* as ``python script args`` would.

    Frozen, the child must inherit PyInstaller's ``_PYI_*`` environment: with
    it a one-file binary reuses this process's unpacked bundle (about 2 s per
    run) instead of unpacking itself again (about 25 s).
    """
    if is_frozen():
        return [sys.executable, RUN_PYTHON_FILE_FLAG, str(script), *args]
    return [sys.executable, str(script), *args]


def requests_run_python_file(argv: Sequence[str]) -> bool:
    """Whether *argv* (without the program name) selects :func:`run_python_file`."""
    return bool(argv) and argv[0] == RUN_PYTHON_FILE_FLAG


def _exit_code(code: object) -> int:
    """The process exit status ``SystemExit(code)`` produces under CPython."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def _script_traceback(exc: BaseException, script: str):
    """*exc*'s traceback from the script's first frame on, dropping the runner's."""
    target = os.path.normcase(os.path.abspath(script))
    tb = exc.__traceback__
    while tb is not None:
        if os.path.normcase(tb.tb_frame.f_code.co_filename) == target:
            return tb
        tb = tb.tb_next
    return None


def _prepend_import_paths(script: str) -> None:
    """``sys.path`` as CPython builds it: script folder, then ``PYTHONPATH``.

    A frozen interpreter ignores ``PYTHONPATH``, and the tools put the user's
    project there so a script can import it.
    """
    entries = [os.path.dirname(os.path.realpath(script))]
    entries += [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    for entry in reversed(entries):
        while entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)


def run_python_file(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``argv[0]`` as ``__main__`` with ``argv[1:]`` as its arguments.

    Returns the exit status CPython would: 0, the script's ``sys.exit`` code,
    1 after an uncaught exception (traceback on stderr), 2 when the file
    cannot be opened.
    """
    argv = list(sys.argv[2:] if argv is None else argv)
    if not argv:
        print(f"usage: {RUN_PYTHON_FILE_FLAG} <script.py> [args ...]", file=sys.stderr)
        return 2
    script, args = argv[0], argv[1:]
    path = os.path.abspath(script)
    if not os.path.isfile(path):
        print(
            f"{sys.executable}: can't open file {path!r}: "
            "[Errno 2] No such file or directory",
            file=sys.stderr,
        )
        return 2

    sys.argv = [path, *args]
    _prepend_import_paths(path)
    if is_frozen():
        # A frozen interpreter ignores PYTHONIOENCODING, and the tools read UTF-8.
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as exc:
        return _exit_code(exc.code)
    except BaseException as exc:  # noqa: BLE001 - reported like CPython does
        traceback.print_exception(type(exc), exc, _script_traceback(exc, path))
        return 1
    finally:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.flush()
    return 0
