# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run Python the way ``python`` does, frozen or not.

A PyInstaller build carries no interpreter: ``sys.executable`` is the agent
binary, so ``[sys.executable, script]`` hands the script path to the agent's
own argument parser and nothing runs. A frozen agent re-launches itself with
:data:`RUN_PYTHON_FILE_FLAG` instead; its entry point
(``hub/agents/gaia/python/packaging/server.py``) passes argv to
:func:`dispatch` before it parses or imports anything else, and the script
runs on the binary's own bundled libraries.

A script that itself starts ``sys.executable`` -- ``-m <module>``, ``-c
<code>``, ``<file.py>`` -- reaches the same binary, so :func:`dispatch` also
takes those interpreter forms. ``-m pip`` gets a plain "can't install here"
instead of the agent's argparse error.

Stdlib only: the frozen child imports this module before the user's script,
so anything it imports lands in that script's ``sys.modules``.
"""

from __future__ import annotations

import builtins
import importlib.util
import os
import runpy
import sys
import traceback
from typing import Callable, Dict, List, Optional, Sequence, Tuple

#: Internal argv selector; never a user-facing flag.
RUN_PYTHON_FILE_FLAG = "--gaia-run-python-file"

#: Install name -> import name of what the frozen binary bundles for scripts.
#: The run_python / execute_python_file descriptions name the same set.
BUNDLED_LIBRARIES: Dict[str, str] = {
    "pypdf": "pypdf",
    "pymupdf": "pymupdf",
    "python-docx": "docx",
    "python-pptx": "pptx",
    "openpyxl": "openpyxl",
    "xlsxwriter": "xlsxwriter",
    "reportlab": "reportlab",
    "pandas": "pandas",
    "numpy": "numpy",
    "matplotlib": "matplotlib",
}

#: ``-m`` targets that install packages; a frozen build has nowhere to put them.
_INSTALLERS = frozenset({"pip", "pip3", "ensurepip", "venv", "virtualenv", "uv"})

# Interpreter options that take no value, and those that take one. Accepted
# and ignored, so ``[sys.executable, "-u", "x.py"]`` still runs.
_FLAGS = frozenset({"-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO", "-q", "-P"})
_FLAGS_WITH_VALUE = frozenset({"-X", "-W"})


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


def interpreter_request(
    argv: Sequence[str],
) -> Optional[Tuple[str, str, List[str]]]:
    """``(kind, target, args)`` when *argv* is a ``python`` command line, else None.

    *kind* is ``"module"`` (``-m``), ``"code"`` (``-c``) or ``"file"``. The
    agent's own argv never has these shapes -- it takes no positional and no
    ``-m``/``-c`` -- so matching them cannot steal an agent launch.
    """
    i = 0
    while i < len(argv):
        if argv[i] in _FLAGS:
            i += 1
        elif argv[i] in _FLAGS_WITH_VALUE:
            i += 2
        else:
            break
    if i >= len(argv):
        return None
    head, rest = argv[i], list(argv[i + 1 :])
    for option, kind in (("-m", "module"), ("-c", "code")):
        if head == option and rest:
            return kind, rest[0], rest[1:]
        if head.startswith(option) and len(head) > 2:
            return kind, head[2:], rest
    if not head.startswith("-"):
        return "file", head, rest
    return None


def _exit_code(code: object) -> int:
    """The process exit status ``SystemExit(code)`` produces under CPython."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def _user_traceback(exc: BaseException):
    """*exc*'s traceback without the leading runner and ``runpy`` frames."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_frame.f_globals.get("__name__") in (
        __name__,
        "runpy",
    ):
        tb = tb.tb_next
    return tb


def _prepend_import_paths(first: str) -> None:
    """``sys.path`` as CPython builds it: *first*, then ``PYTHONPATH``.

    A frozen interpreter ignores ``PYTHONPATH``, and the tools put the user's
    project there so a script can import it.
    """
    entries = [first]
    entries += [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    for entry in reversed(entries):
        while entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)


def _set_output_encoding(encoding: Optional[str]) -> None:
    if not encoding:
        return
    name, _, errors = encoding.partition(":")
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding=name or None, errors=errors or None)


def _run(body: Callable[[], object]) -> int:
    """Run *body* as CPython runs ``__main__``: exit status, traceback, flush."""
    try:
        body()
    except SystemExit as exc:
        return _exit_code(exc.code)
    except BaseException as exc:  # noqa: BLE001 - reported like CPython does
        traceback.print_exception(type(exc), exc, _user_traceback(exc))
        return 1
    finally:
        # A script may close stdout itself (json.tool does); CPython allows it.
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and not stream.closed:
                stream.flush()
    return 0


def _run_file(script: str, args: Sequence[str]) -> int:
    path = os.path.abspath(script)
    if not os.path.isfile(path):
        print(
            f"{sys.executable}: can't open file {path!r}: "
            "[Errno 2] No such file or directory",
            file=sys.stderr,
        )
        return 2
    sys.argv = [path, *args]
    _prepend_import_paths(os.path.dirname(os.path.realpath(path)))
    return _run(lambda: runpy.run_path(path, run_name="__main__"))


def no_install_message(module: str) -> str:
    """Why ``-m <installer>`` does nothing here, and what to use instead."""
    return (
        f"{module} is not available: packages can't be installed in this GAIA "
        f"build. Bundled for scripts: {', '.join(BUNDLED_LIBRARIES)}, and the "
        "Python standard library."
    )


def _run_module(module: str, args: Sequence[str]) -> int:
    if module.split(".")[0] in _INSTALLERS:
        print(no_install_message(module), file=sys.stderr)
        return 1
    _prepend_import_paths(os.getcwd())
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        print(f"{sys.executable}: No module named {module}", file=sys.stderr)
        return 1
    sys.argv = [module, *args]
    return _run(lambda: runpy.run_module(module, run_name="__main__", alter_sys=True))


def _run_code(code: str, args: Sequence[str]) -> int:
    sys.argv = ["-c", *args]
    _prepend_import_paths("")
    namespace = {"__name__": "__main__", "__builtins__": builtins}
    # The interpreter's own `-c`: the code is what the caller asked to run.
    return _run(
        lambda: exec(compile(code, "<string>", "exec"), namespace)  # nosec B102
    )


def run_python_file(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``argv[0]`` as ``__main__`` with ``argv[1:]`` as its arguments.

    The tools' entry. Returns the exit status CPython would: 0, the script's
    ``sys.exit`` code, 1 after an uncaught exception (traceback on stderr), 2
    when the file cannot be opened.
    """
    argv = list(sys.argv[2:] if argv is None else argv)
    if not argv:
        print(f"usage: {RUN_PYTHON_FILE_FLAG} <script.py> [args ...]", file=sys.stderr)
        return 2
    if is_frozen():
        # A frozen interpreter ignores PYTHONIOENCODING, and the tools read UTF-8.
        _set_output_encoding("utf-8")
    return _run_file(argv[0], argv[1:])


def run_interpreter(kind: str, target: str, args: Sequence[str]) -> int:
    """Run one :func:`interpreter_request` the way ``python`` would."""
    if is_frozen():
        _set_output_encoding(os.environ.get("PYTHONIOENCODING"))
    if kind == "module":
        return _run_module(target, args)
    if kind == "code":
        return _run_code(target, args)
    return _run_file(target, args)


def dispatch(argv: Sequence[str]) -> Optional[int]:
    """Exit status when *argv* asks this binary to act as Python, else None."""
    if requests_run_python_file(argv):
        return run_python_file(argv[1:])
    request = interpreter_request(argv)
    if request is None:
        return None
    return run_interpreter(*request)
