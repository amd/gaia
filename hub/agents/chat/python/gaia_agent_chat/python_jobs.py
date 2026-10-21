# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run a Python job for ``run_python`` / ``execute_python_file`` and stop it cleanly.

A job that runs past its timeout, or that the agent stops waiting for, is
killed with every process it started: a surviving child keeps writing, and the
user is left with a half-written file that looks finished. What the job wrote
before it was stopped is reported, never deleted — those paths may be files
the user already had.
"""

from __future__ import annotations

import ast
import ctypes
import functools
import logging
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from gaia.agents.tools.command_timeouts import terminate_process_tree

logger = logging.getLogger(__name__)

#: Long enough for a pandas pass over a few hundred MB, and under the agent's
#: 180 s default tool limit, so a job that forgets ``timeout`` fails on its own
#: clock with its own message.
DEFAULT_PYTHON_TIMEOUT = 120

#: One job may hold the turn for half an hour, the ceiling GAIA already uses
#: for its longest single tool call (a full-mailbox triage). Longer work should
#: be split into steps that each save their output.
MAX_PYTHON_TIMEOUT = 1800

#: How often a running job is checked against the agent's stop signals.
_POLL_SECONDS = 0.25

#: How long a process the script left running may hold its output open.
_LEFTOVER_GRACE_SECONDS = 2.0

#: How long a killed job's pipes may take to close.
_KILL_GRACE_SECONDS = 5.0

_SCAN_SKIP_DIRS = frozenset(
    {
        ".git",
        ".gaia",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
    }
)
_SCAN_MAX_ENTRIES = 20000
_SCAN_MAX_SECONDS = 3.0
_REPORT_MAX_FILES = 20


def resolve_python_timeout(requested: Optional[int]) -> int:
    """Seconds a job may run; *requested* None means the default.

    Raises:
        ValueError: not a positive whole number, or above ``MAX_PYTHON_TIMEOUT``.
            Refused rather than clamped, so a job is never killed at a limit
            the caller did not ask for.
    """
    if requested is None:
        return DEFAULT_PYTHON_TIMEOUT
    try:
        if isinstance(requested, bool):
            raise TypeError("a boolean is not a number of seconds")
        seconds = int(requested)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"timeout must be a whole number of seconds, got {requested!r}. "
            f"Omit it for the {DEFAULT_PYTHON_TIMEOUT}s default."
        ) from exc
    if seconds <= 0:
        raise ValueError(
            f"timeout must be a positive number of seconds, got {seconds}. "
            f"Omit it for the {DEFAULT_PYTHON_TIMEOUT}s default."
        )
    if seconds > MAX_PYTHON_TIMEOUT:
        raise ValueError(
            f"timeout of {seconds}s exceeds the {MAX_PYTHON_TIMEOUT}s ceiling for "
            "one Python run. Split the job into steps that each save their "
            "output, or process the data in chunks."
        )
    return seconds


class PythonJob:
    """The outcome of :func:`run_python_job`."""

    def __init__(
        self,
        run: subprocess.CompletedProcess,
        stopped: Optional[str],
        written: List[str],
        *,
        written_complete: bool = True,
        left_running: bool = False,
    ):
        #: ``returncode``, ``stdout`` and ``stderr`` of the job.
        self.run = run
        #: ``"timeout"`` or ``"cancelled"`` if the job was killed, else None.
        self.stopped = stopped
        #: Files created or changed while the job ran, if it was killed.
        self.written = written
        #: False when the search for *written* hit its size or time bound.
        self.written_complete = written_complete
        #: The script exited, but processes it started still held its output.
        self.left_running = left_running


class _ProcessTree:
    """Every process a job starts, killable even after the job itself exits.

    Windows has no process groups, and ``taskkill /T`` cannot reach a child
    whose parent already exited, so there the job runs in a Job Object.
    """

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc
        self._job = _assign_job_object(proc.pid) if os.name == "nt" else None

    def kill(self) -> Tuple[str, str]:
        """Kill every process in the tree; return what the job printed."""
        if os.name == "nt" and self._job is None:
            return terminate_process_tree(self._proc)
        if os.name != "nt":
            try:
                # start_new_session made the job's pid its process group id.
                os.killpg(self._proc.pid, signal.SIGKILL)  # pylint: disable=no-member
            except ProcessLookupError:
                pass  # every process in the group has already exited
        elif not _kernel32().TerminateJobObject(self._job, 1):
            logger.warning(
                "TerminateJobObject failed for pid %s: WinError %s",
                self._proc.pid,
                ctypes.get_last_error(),
            )
        if self._proc.poll() is None:
            self._proc.kill()
        return _drain(self._proc)

    def close(self) -> None:
        if self._job is not None:
            _kernel32().CloseHandle(self._job)
            self._job = None


@functools.lru_cache(maxsize=1)
def _kernel32():
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _assign_job_object(pid: int):
    """A Job Object holding *pid* and every process it starts, or None.

    None is logged: the kill then uses ``taskkill /T``
    (``terminate_process_tree``), which misses a grandchild whose parent has
    already exited.
    """
    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        logger.warning(
            "CreateJobObject failed (WinError %s); a stopped Python job's "
            "orphaned children may keep running.",
            ctypes.get_last_error(),
        )
        return None
    access = 0x0100 | 0x0001  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    handle = kernel32.OpenProcess(access, False, pid)
    assigned = bool(handle) and bool(kernel32.AssignProcessToJobObject(job, handle))
    error = ctypes.get_last_error()
    if handle:
        kernel32.CloseHandle(handle)
    if not assigned:
        kernel32.CloseHandle(job)
        logger.warning(
            "Could not put pid %s in a Job Object (WinError %s); a stopped "
            "Python job's orphaned children may keep running.",
            pid,
            error,
        )
        return None
    return job


def _drain(proc: subprocess.Popen) -> Tuple[str, str]:
    """What the killed job printed; empty if its pipes will not close."""
    try:
        return proc.communicate(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Output pipes of pid %s stayed open after the kill; its output is lost.",
            proc.pid,
        )
        return "", ""


def run_python_job(
    cmd: Sequence[str],
    *,
    cwd: str,
    env: Optional[Dict[str, str]],
    timeout: float,
    should_stop: Callable[[], bool],
    watch: Iterable[os.PathLike | str] = (),
    ignore: Iterable[os.PathLike | str] = (),
) -> PythonJob:
    """Run *cmd*; kill its whole process tree at *timeout* or on *should_stop*.

    A process the script leaves running and holding its output is killed once
    the script exits, so the run ends with the script. *watch* are folders to
    search for files the job wrote, reported only when it is killed; paths in
    *ignore* (e.g. the job's own script) are left out.

    Raises:
        OSError: *cmd* could not be started.
    """
    started_wall = time.time()
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(  # pylint: disable=consider-using-with
        list(cmd),
        cwd=cwd,
        env=env,
        # An inherited stdin leaves the child waiting on a pipe nobody writes
        # to, and the run only ends at the timeout.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # The locale codec (cp1252 on Windows) raises on one emoji in a print().
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
    )
    tree = _ProcessTree(proc)
    stopped: Optional[str] = None
    left_running = False
    exited_at: Optional[float] = None
    try:
        while True:
            remaining = deadline - time.monotonic()
            try:
                out, err = proc.communicate(
                    timeout=max(min(remaining, _POLL_SECONDS), 0)
                )
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if proc.poll() is not None:
                    # The script is done; a process it started holds the pipes.
                    exited_at = exited_at or now
                    if now - exited_at < _LEFTOVER_GRACE_SECONDS:
                        continue
                    left_running = True
                elif should_stop():
                    stopped = "cancelled"
                elif now >= deadline:
                    stopped = "timeout"
                else:
                    continue
                out, err = tree.kill()
                break
    finally:
        tree.close()
    run = subprocess.CompletedProcess(list(cmd), proc.returncode, out or "", err or "")
    written: List[str] = []
    complete = True
    if stopped:
        written, complete = files_written_since(watch, started_wall, ignore)
    return PythonJob(
        run,
        stopped,
        written,
        written_complete=complete,
        left_running=left_running,
    )


def files_written_since(
    roots: Iterable[os.PathLike | str],
    since: float,
    ignore: Iterable[os.PathLike | str] = (),
) -> Tuple[List[str], bool]:
    """Files under *roots* modified at or after *since* (epoch seconds).

    Returns ``(paths, complete)``; *complete* is False when the search hit its
    entry, time or result bound. Roots are searched in the order given.
    """
    skip = {os.path.normcase(os.path.abspath(p)) for p in ignore}
    # One second of slack for filesystems that round modification times.
    cutoff = since - 1.0
    budget_end = time.monotonic() + _SCAN_MAX_SECONDS
    seen = 0
    found: List[str] = []
    walked: set = set()
    for root in roots:
        root = os.path.abspath(root)
        if too_wide_to_scan(root) or not os.path.isdir(root):
            continue
        for current, dirs, files in os.walk(root):
            key = os.path.normcase(current)
            if key in walked:
                dirs[:] = []
                continue
            walked.add(key)
            dirs[:] = [d for d in dirs if d not in _SCAN_SKIP_DIRS]
            for name in files:
                seen += 1
                if seen > _SCAN_MAX_ENTRIES or time.monotonic() > budget_end:
                    return sorted(found), False
                path = os.path.join(current, name)
                if os.path.normcase(path) in skip:
                    continue
                try:
                    if os.stat(path).st_mtime >= cutoff:
                        found.append(path)
                except OSError:
                    continue  # deleted or locked mid-walk
                if len(found) >= _REPORT_MAX_FILES:
                    return sorted(found), False
    return sorted(found), True


def files_written_by(
    job: PythonJob,
    watch: Iterable[os.PathLike | str],
    started: float,
    ignore: Iterable[os.PathLike | str] = (),
) -> Tuple[List[str], bool]:
    """Files under *watch* changed since *started* while *job* ran.

    Returns ``(paths, complete)`` like :func:`files_written_since`. A killed
    job was already searched.
    """
    if job.stopped:
        return list(job.written), job.written_complete
    return files_written_since(watch, started, ignore)


# ── does a script write files? ──────────────────────────────────────────────
#
# Fails closed: a script counts as read-only only when every module it imports,
# every name it reaches and every call it makes is known not to write.

#: Modules that do not write files on their own; their functions and methods
#: that can are caught by name below. An entry covers its submodules.
_READ_SAFE_MODULES = frozenset(
    {
        "abc",
        "array",
        "base64",
        "binascii",
        "bisect",
        "bz2",
        "calendar",
        "cmath",
        "codecs",
        "collections",
        "contextlib",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "enum",
        "fnmatch",
        "fractions",
        "functools",
        "glob",
        "gzip",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "io",
        "ipaddress",
        "itertools",
        "json",
        "lzma",
        "math",
        "mimetypes",
        "numbers",
        "os",
        "pathlib",
        "pprint",
        "random",
        "re",
        "secrets",
        "shutil",
        "statistics",
        "string",
        "struct",
        "sys",
        "tarfile",
        "textwrap",
        "time",
        "tomllib",
        "typing",
        "unicodedata",
        "urllib.parse",
        "uuid",
        "xml.etree",
        "zipfile",
        "zoneinfo",
        "docx",
        "matplotlib",
        "numpy",
        "openpyxl",
        "pandas",
        "PIL",
        "pptx",
        "pdfplumber",
        "pypdf",
        "PyPDF2",
        "xlrd",
    }
)

#: Modules where only the names listed may be used.
_MODULE_READS = {
    "os": frozenset(
        {
            "path",
            "listdir",
            "scandir",
            "walk",
            "stat",
            "lstat",
            "fstat",
            "getcwd",
            "getcwdb",
            "environ",
            "getenv",
            "sep",
            "altsep",
            "extsep",
            "pathsep",
            "linesep",
            "devnull",
            "curdir",
            "pardir",
            "name",
            "cpu_count",
            "getpid",
            "getppid",
            "getlogin",
            "fspath",
            "fsencode",
            "fsdecode",
            "access",
            "get_terminal_size",
            "uname",
            "urandom",
            "readlink",
            "strerror",
            "stat_result",
            "DirEntry",
            "PathLike",
            "F_OK",
            "R_OK",
            "W_OK",
            "X_OK",
        }
    ),
    "shutil": frozenset(
        {
            "which",
            "disk_usage",
            "get_terminal_size",
            "get_archive_formats",
            "get_unpack_formats",
        }
    ),
    "sys": frozenset(
        {
            "argv",
            "stdout",
            "stderr",
            "stdin",
            "version",
            "version_info",
            "hexversion",
            "platform",
            "implementation",
            "executable",
            "exit",
            "maxsize",
            "maxunicode",
            "float_info",
            "int_info",
            "byteorder",
            "getsizeof",
            "getdefaultencoding",
            "getfilesystemencoding",
            "getrecursionlimit",
            "setrecursionlimit",
            "exc_info",
            "exception",
        }
    ),
}

#: Functions, methods and classes that write wherever they are reached from.
_WRITE_NAMES = frozenset(
    {
        "write",
        "writelines",
        "writestr",
        "write_text",
        "write_bytes",
        "save",
        "save_workbook",
        "ez_save",
        "savefig",
        "savez",
        "savez_compressed",
        "savetxt",
        "imsave",
        "tofile",
        "dump",
        "touch",
        "mkdir",
        "makedirs",
        "rmdir",
        "removedirs",
        "unlink",
        "rmtree",
        "symlink_to",
        "hardlink_to",
        "link_to",
        "chmod",
        "lchmod",
        "chown",
        "lchown",
        "utime",
        "truncate",
        "copyfile",
        "copy2",
        "copytree",
        "copymode",
        "copystat",
        "move",
        "move_into",
        "copy_into",
        "make_archive",
        "unpack_archive",
        "extractall",
        "urlretrieve",
        "memmap",
        "open_memmap",
        "read_pickle",
        "read_sql",
        "read_sql_query",
        "read_sql_table",
        "system",
        "popen",
        "exec",
        "eval",
        "DataSource",
        "ExcelWriter",
        "HDFStore",
        "PdfPages",
        "PdfWriter",
        "PdfMerger",
        "PdfFileWriter",
        "PdfFileMerger",
    }
)

#: ``to_*`` methods that only convert in memory.
_PURE_TO = frozenset(
    {
        "to_numpy",
        "to_dict",
        "to_list",
        "to_frame",
        "to_datetime",
        "to_numeric",
        "to_timedelta",
        "to_records",
        "to_period",
        "to_timestamp",
        "to_series",
        "to_pydatetime",
        "to_flat_index",
        "to_offset",
        "to_dense",
        "to_tuples",
        "to_julian_date",
        "to_bytes",
    }
)
#: ``to_*`` methods that write only when given a destination (``df.to_csv()`` returns text).
_WRITE_WITH_TARGET = frozenset(
    {"to_csv", "to_json", "to_html", "to_latex", "to_markdown", "to_xml", "to_string"}
)
_TARGET_KEYWORDS = frozenset(
    {
        "path_or_buf",
        "path_or_buffer",
        "buf",
        "path",
        "excel_writer",
        "fname",
        "file",
        "filename",
        "target",
        "dst",
        "dest",
        "destination",
    }
)

#: Calls that open a file, writing only in a mode that writes.
_OPENERS = frozenset(
    {
        "open",
        "taropen",
        "gzopen",
        "bz2open",
        "xzopen",
        "ZipFile",
        "PyZipFile",
        "TarFile",
        "GzipFile",
        "BZ2File",
        "LZMAFile",
        "FileIO",
    }
)

#: Methods that write only for some arguments: ``Path.rename(target)`` but not
#: ``df.rename(columns=...)``, ``Path.replace(target)`` but not ``str.replace(a, b)``.
_WRITE_BY_ARGS = frozenset({"rename", "replace", "copy", "extract"})
#: Modules whose ``rename``/``replace``/``copy`` work in memory.
_PURE_BY_ARGS_MODULES = frozenset({"copy", "numpy"})

#: Builtins that run other code or reach any object.
_UNSAFE_BUILTINS = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "__import__",
        "__builtins__",
        "__loader__",
        "__spec__",
        "globals",
        "locals",
        "vars",
        "setattr",
        "delattr",
        "breakpoint",
        "help",
    }
)
#: Attributes that hand back a module not imported by name (``pathlib.os``).
_MODULE_ATTRS = frozenset(
    {
        "os",
        "sys",
        "shutil",
        "subprocess",
        "builtins",
        "importlib",
        "ctypes",
        "tempfile",
        "pickle",
        "sqlite3",
        "socket",
        "runpy",
        "multiprocessing",
        "pty",
        "shelve",
        "dbm",
        "marshal",
    }
)
_SAFE_DUNDERS = frozenset(
    {"__name__", "__doc__", "__version__", "__file__", "__qualname__", "__module__"}
)

#: A string that is an ``open()`` mode, e.g. ``"rb"``, ``"w:gz"``, ``"r|*"``.
_MODE = re.compile(r"^[rwaxbtU+]{1,4}(?:[:|][\w*]*)?$")


def _read_mode(node: ast.AST) -> bool:
    """A literal mode that only reads."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    head = re.split(r"[:|]", node.value, maxsplit=1)[0]
    return bool(head) and set(head) <= set("rbtU")


def _dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and name not in _SAFE_DUNDERS


def _unpacks(call: ast.Call) -> bool:
    """Arguments that cannot be read off the call: ``*args`` or ``**kwargs``."""
    return any(isinstance(a, ast.Starred) for a in call.args) or any(
        kw.arg is None for kw in call.keywords
    )


def _has_target(call: ast.Call) -> bool:
    return (
        _unpacks(call)
        or bool(call.args)
        or any(
            kw.arg in _TARGET_KEYWORDS
            and not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
            for kw in call.keywords
        )
    )


def _name_may_write(name: str) -> bool:
    """An attribute or imported name that writes whatever it is called with."""
    return (
        _dunder(name)
        or name in _WRITE_NAMES
        or name in _MODULE_ATTRS
        or name.startswith("print_")  # matplotlib's canvas.print_png(path)
        or (name.startswith("to_") and name not in _PURE_TO)
    )


def _read_safe(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in _READ_SAFE_MODULES)


def _opens_for_writing(call: ast.Call, module: bool) -> bool:
    """An ``open``-style call that is not plainly read-only.

    *module* is an ``open(path, mode)`` function or class; otherwise a method
    such as ``Path.open(mode)`` or ``ZipFile.open(name, mode)``.
    """
    if _unpacks(call):
        return True
    for kw in call.keywords:
        if kw.arg == "mode" and not _read_mode(kw.value):
            return True
    args = call.args
    if not module and args:
        first = args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return True  # a mode or a name we cannot read
        if _MODE.match(first.value):
            return not _read_mode(first)
    return len(args) > 1 and not _read_mode(args[1])


def _writes_by_args(name: str, call: ast.Call, receiver: ast.AST) -> bool:
    if _unpacks(call) or any(kw.arg in _TARGET_KEYWORDS for kw in call.keywords):
        return True
    if name == "extract":  # ZipFile.extract, not Series.str.extract
        return not (isinstance(receiver, ast.Attribute) and receiver.attr == "str")
    if name == "replace":
        return len(call.args) == 1
    return bool(call.args)


def _numpy_load_may_write(call: ast.Call) -> bool:
    """``np.load`` unpickles, or maps a file writable, unless told not to."""
    if _unpacks(call) or len(call.args) > 1:
        return True
    for kw in call.keywords:
        if not isinstance(kw.value, ast.Constant):
            if kw.arg in ("allow_pickle", "mmap_mode"):
                return True
            continue
        if kw.arg == "allow_pickle" and kw.value.value is not False:
            return True
        if kw.arg == "mmap_mode" and kw.value.value not in (None, "r", "c"):
            return True
    return False


class _WriteScan:
    """One script's syntax tree, checked for anything that may write."""

    def __init__(self, tree: ast.AST):
        self.nodes = list(ast.walk(tree))
        #: Local name -> dotted module path it was imported as.
        self.imports: Dict[str, str] = {}
        #: Names also bound some other way, so not trusted to be the module.
        self.rebound: set = set()
        self.calls: Dict[int, ast.Call] = {}
        self.owners: set = set()
        for n in self.nodes:
            if isinstance(n, ast.Call):
                self.calls[id(n.func)] = n
            elif isinstance(n, ast.Attribute):
                self.owners.add(id(n.value))
            elif isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
                self.rebound.add(n.id)
            elif isinstance(n, ast.arg):
                self.rebound.add(n.arg)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.rebound.add(n.name)
            elif isinstance(n, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
                if n.name:
                    self.rebound.add(n.name)
            elif isinstance(n, ast.MatchMapping) and n.rest:
                self.rebound.add(n.rest)

    def may_write(self) -> bool:
        for n in self.nodes:
            if isinstance(n, (ast.Import, ast.ImportFrom)) and self._import_may_write(
                n
            ):
                return True
        for n in self.nodes:
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                if self._load_may_write(n):
                    return True
            elif isinstance(n, ast.Attribute):
                if self._attribute_may_write(n):
                    return True
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                if self._name_call_may_write(n):
                    return True
        return False

    def _bind(self, local: str, module: str) -> None:
        if self.imports.get(local, module) != module:
            self.rebound.add(local)
        self.imports[local] = module

    def _import_may_write(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _read_safe(alias.name):
                    return True
                if alias.asname:
                    self._bind(alias.asname, alias.name)
                else:
                    top = alias.name.split(".")[0]
                    self._bind(top, top)
            return False
        if node.level or not node.module or not _read_safe(node.module):
            return True
        reads = _MODULE_READS.get(node.module)
        for alias in node.names:
            if alias.name == "*" or _name_may_write(alias.name):
                return True
            if reads is not None and alias.name not in reads:
                return True
            self._bind(alias.asname or alias.name, f"{node.module}.{alias.name}")
        return False

    def _opener(self, name: str) -> bool:
        """``open`` or the like, under its own name or one it was imported as."""
        imported = self.imports.get(name)
        return (
            name in _OPENERS or bool(imported) and imported.split(".")[-1] in _OPENERS
        )

    def _resolve(self, node: ast.AST) -> Optional[str]:
        """The dotted module path *node* names, if it starts at an import."""
        if isinstance(node, ast.Name):
            return self.imports.get(node.id)
        if isinstance(node, ast.Attribute):
            inner = self._resolve(node.value)
            return f"{inner}.{node.attr}" if inner else None
        return None

    def _trusted(self, node: ast.AST) -> Optional[str]:
        """Like :meth:`_resolve`, but only when the name is bound by imports alone."""
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in self.rebound:
            return None
        return self._resolve(node)

    def _load_may_write(self, node: ast.Name) -> bool:
        name = node.id
        if name in _UNSAFE_BUILTINS:
            return True
        if name == "getattr" or self._opener(name):
            # Handed on uncalled, there is no telling what it is used for.
            return id(node) not in self.calls
        # A module limited to some of its names, itself handed on.
        return self.imports.get(name) in _MODULE_READS and id(node) not in self.owners

    def _attribute_may_write(self, node: ast.Attribute) -> bool:
        name = node.attr
        if _dunder(name) or name in _MODULE_ATTRS or name.startswith("print_"):
            return True
        resolved = self._resolve(node)
        if resolved:
            parts = resolved.split(".")
            reads = _MODULE_READS.get(parts[0])
            if reads is not None and parts[1] not in reads:
                return True
            if parts[0] == "sys" and parts[1] in ("stdout", "stderr"):
                # sys.stdout.write prints, unless "sys" was rebound to something else.
                return not self._trusted(node)
        call = self.calls.get(id(node))
        if name in _WRITE_NAMES:
            # json.dump needs a file object, and opening one to write is caught.
            return not (name == "dump" and self._trusted(node) == "json.dump")
        if name.startswith("to_") and name not in _PURE_TO:
            return name not in _WRITE_WITH_TARGET or call is None or _has_target(call)
        if name in _OPENERS:
            if call is None:
                return True
            owner = self._trusted(node.value)
            return _opens_for_writing(
                call, module=bool(owner) and not owner.startswith("pathlib")
            )
        if name in _WRITE_BY_ARGS:
            if call is None:
                return True
            owner = self._trusted(node.value)
            if owner and owner.split(".")[0] in _PURE_BY_ARGS_MODULES:
                return False
            return _writes_by_args(name, call, node.value)
        if name == "load" and (resolved or "").startswith("numpy"):
            return call is None or _numpy_load_may_write(call)
        return False

    def _name_call_may_write(self, call: ast.Call) -> bool:
        name = call.func.id
        if name == "getattr":
            return self._getattr_may_write(call)
        if self._opener(name):
            return _opens_for_writing(call, module=True)
        if self.imports.get(name) == "numpy.load":
            return _numpy_load_may_write(call)
        return False

    def _getattr_may_write(self, call: ast.Call) -> bool:
        if _unpacks(call) or len(call.args) < 2:
            return True
        owner, attr = call.args[0], call.args[1]
        if not (isinstance(attr, ast.Constant) and isinstance(attr.value, str)):
            return True
        if self._resolve(owner):
            return True
        name = attr.value
        return _name_may_write(name) or name in _OPENERS or name in _WRITE_BY_ARGS


def may_write_files(source: str) -> bool:
    """Whether a script could create, change or delete files.

    A static check that fails closed: only code whose every import, name and
    call is known not to write counts as read-only. Code that does not parse,
    runs other code, starts a process or imports a module not known to be
    read-only counts as writing.
    """
    try:
        return _WriteScan(ast.parse(source)).may_write()
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return True


_DESCRIBE_MAX = 120
#: A shebang, an encoding line, or a bare ``#``.
_PREAMBLE = re.compile(r"^#(!|.*coding[:=]|\s*$)")


def describe_code(source: str) -> str:
    """A one-line description of a snippet for the turn's change record.

    Its docstring or leading comment if it has one, else its first line of
    code that is not an import.
    """
    text = (source or "").strip()
    if not text:
        return ""
    line = ""
    try:
        doc = ast.get_docstring(ast.parse(text))
    except (SyntaxError, ValueError):
        doc = None
    if doc and doc.strip():
        line = doc.strip().splitlines()[0]
    if not line:
        lines = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not _PREAMBLE.match(ln.strip())
        ]
        if lines and lines[0].startswith("#"):
            line = lines[0].lstrip("#").strip()
        if not line:
            line = next(
                (ln for ln in lines if not ln.startswith(("#", "import ", "from "))),
                lines[0] if lines else "",
            )
    line = " ".join(line.split())
    if len(line) > _DESCRIBE_MAX:
        line = line[: _DESCRIBE_MAX - 1].rstrip() + "…"
    return line


def too_wide_to_scan(folder: os.PathLike | str) -> bool:
    """A drive root or the home folder: too wide to say anything useful."""
    folder = os.path.normcase(os.path.abspath(folder))
    home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    return os.path.dirname(folder) == folder or folder == home


def folders_named_in(source: str) -> List[str]:
    """Existing folders a script names by absolute path, for the written-files scan.

    A script run from a scratch folder usually saves its deliverable through an
    absolute path in a string literal; those folders are where to look.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    folders: List[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value.strip()
        if not text or len(text) > 400 or "\n" in text:
            continue
        path = os.path.normpath(os.path.expanduser(text))
        if not os.path.isabs(path):
            continue
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if too_wide_to_scan(folder) or not os.path.isdir(folder):
            continue
        if folder not in folders:
            folders.append(folder)
    return folders


def watch_folders(*candidates: Optional[os.PathLike | str]) -> List[Path]:
    """Existing folders among *candidates*, de-duplicated, in order."""
    folders: List[Path] = []
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).resolve()
        if path.is_dir() and not too_wide_to_scan(path) and path not in folders:
            folders.append(path)
    return folders


def stopped_job_result(job: PythonJob, timeout: int) -> Dict[str, object]:
    """The tool result for a job that was killed before it finished."""
    if job.stopped == "timeout":
        error = (
            f"Timed out after {timeout}s; the script and every process it "
            "started were stopped. If the job legitimately needs longer, pass "
            f"a larger timeout (max {MAX_PYTHON_TIMEOUT}), or make it do less "
            "per run."
        )
    else:
        error = (
            "Stopped before it finished; the script and every process it "
            "started were stopped."
        )
    if job.written:
        error += (
            " These files changed while it ran, so they may be incomplete — "
            "check or regenerate them before calling them done: "
            + ", ".join(job.written)
        )
    if not job.written_complete:
        error += " (Not every folder was searched; others may have changed too.)"
    return {
        "status": "error",
        "error": error,
        "has_errors": True,
        "timed_out": job.stopped == "timeout",
        "files_modified": job.written,
        "files_modified_complete": job.written_complete,
        "stdout": job.run.stdout[-4000:],
        "stderr": job.run.stderr[-2000:],
    }


def finished_job_note(job: PythonJob) -> Optional[str]:
    """A note for a run that ended normally but left processes behind."""
    if not job.left_running:
        return None
    return (
        "The script exited but left processes running that held its output; "
        "they were stopped."
    )


def stop_requested(agent: object) -> bool:
    """True once the turn was stopped or the agent gave up on this tool call."""
    from gaia.tool_cancellation import tool_cancelled

    if tool_cancelled():
        return True
    event = getattr(agent, "_cancel_event", None)
    if event is not None and event.is_set():
        return True
    console_cancelled = getattr(agent, "_console_cancelled", None)
    return bool(console_cancelled and console_cancelled())
