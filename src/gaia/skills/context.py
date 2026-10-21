# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a skill's own ``tools.py`` may do, checked against the running agent.

A skill tool is a plain ``@tool`` function with no ``self``, so it cannot see
the agent that called it. While the agent runs one, it binds the call here, and
these functions act for that agent and that skill::

    from gaia.skills.context import check_read, run_program, temp_dir

    @tool
    def convert(file_path: str) -> dict:
        source = check_read(file_path)          # agent's allowed folders
        out = temp_dir("out")                   # needs scratch:write
        proc = run_program(                     # needs program:run:soffice
            "soffice", ["--headless", "--convert-to", "pdf",
                        "--outdir", str(out), str(source)], timeout=120)
        ...

- **Paths.** :func:`check_read` / :func:`check_write` apply the agent's
  allowed-folders check — the same ``validate_read`` / ``validate_write`` that
  ``read_file`` and ``write_file`` use, approval prompt included. A tool
  argument whose name says it is a path (``path``, ``file_path``,
  ``output_dir`` …, see :func:`is_path_argument`) is read-checked before the
  tool body runs, so a skill tool cannot be pointed outside the scope by
  default. A tool that writes calls :func:`check_write` itself.
- **Files.** :func:`open_read`, :func:`list_readable` and :func:`open_write`
  open, list and create files behind those same checks, so a skill that
  reads the user's files (logs, exports) needs no ``open()`` of its own.
- **Programs.** :func:`run_program` starts one executable the manifest names
  (``program:run:<name>``): no shell, stdin closed, an environment holding
  only what a program needs to start (no API keys or tokens), and a timeout.
  It does not stop the program reaching the network.
- **Temporary files.** :func:`temp_dir`, :func:`write_temp_file` and
  :func:`copy_to_temp` work inside a fresh folder per tool call
  (``scratch:write``), deleted when the call returns.
- **Preferences.** :func:`preferences` reads and writes this skill's saved
  preferences (:mod:`gaia.skills.preferences`).

Code that calls ``subprocess`` or writes files directly instead is flagged by
``gaia skill audit``; calls through this module are matched against the
declared permissions instead. Every function here raises
:class:`SkillContextError` when no skill tool call is running, and
:class:`~gaia.skills.errors.SkillPermissionError` for a capability the skill
did not declare.
"""

from __future__ import annotations

import contextvars
import os
import platform
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Dict, Iterator, List, Optional, Sequence

from gaia.logger import get_logger
from gaia.skills.errors import SkillError, SkillPermissionError
from gaia.skills.preferences import (
    PreferenceWrite,
    SkillPreferenceStore,
    load_schema,
)

log = get_logger(__name__)

#: Longest a skill may let one program run.
MAX_PROGRAM_TIMEOUT_S = 900

#: Environment variables passed to a program, when set. Everything else —
#: API keys, tokens, proxies with credentials — stays in the agent.
PROGRAM_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "DISPLAY",
    "XDG_RUNTIME_DIR",
)

#: Where an installer puts a program that it does not add to PATH. Checked
#: after PATH, in order. Keys are the names a manifest declares.
KNOWN_INSTALL_PATHS: Dict[str, Dict[str, Sequence[str]]] = {
    "soffice": {
        "Windows": (
            "{ProgramFiles}/LibreOffice/program/soffice.exe",
            "{ProgramFiles(x86)}/LibreOffice/program/soffice.exe",
        ),
        "Darwin": ("/Applications/LibreOffice.app/Contents/MacOS/soffice",),
        "Linux": (
            "/usr/bin/soffice",
            "/usr/lib/libreoffice/program/soffice",
            "/opt/libreoffice/program/soffice",
        ),
    },
}

_PATH_ARG_RE = re.compile(
    r"^(path|paths|file|filename|filepath|file_path|dir|directory|folder)$"
    r"|_(path|paths|file|dir|directory|folder)$"
)


class SkillContextError(SkillError):
    """A skill-context function was called outside a running skill tool."""


@dataclass
class SkillCall:
    """One running skill tool: the agent it runs for and the skill it belongs to."""

    agent: Any
    skill: Any
    _temp_root: Optional[Path] = field(default=None, repr=False)

    @property
    def skill_name(self) -> str:
        return str(self.skill.name)

    def declares(self, domain: str, level: str, scope: Optional[str] = None) -> bool:
        for permission in self.skill.parsed_permissions():
            if (permission.domain, permission.level) != (domain, level):
                continue
            if scope is None or permission.scope == scope:
                return True
        return False

    def require(self, permission: str, what: str) -> None:
        domain, level, *rest = permission.split(":", 2)
        if not self.declares(domain, level, rest[0] if rest else None):
            raise SkillPermissionError(
                f"Skill '{self.skill_name}' tried to {what} but does not declare "
                f"'{permission}'. Add it to metadata.gaia.permissions in its "
                "SKILL.md, then reload the skill."
            )


_ACTIVE: contextvars.ContextVar[Optional[SkillCall]] = contextvars.ContextVar(
    "gaia_skill_call", default=None
)


@contextmanager
def bind(agent: Any, skill: Any) -> Iterator[SkillCall]:
    """Make *agent* and *skill* the context of the tool call inside the block.

    Used by ``Agent._execute_tool``. The call's temporary folder is deleted on
    the way out, unless the call timed out: its worker may still be using it.
    """
    call = SkillCall(agent=agent, skill=skill)
    token = _ACTIVE.set(call)
    timed_out = False
    try:
        yield call
    except BaseException as exc:
        from gaia.agents.base.agent import ToolExecutionTimeout

        timed_out = isinstance(exc, ToolExecutionTimeout)
        raise
    finally:
        _ACTIVE.reset(token)
        if call._temp_root is not None:
            if timed_out:
                log.warning(
                    "Skill '%s' timed out; leaving its temporary folder %s for "
                    "the still-running call.",
                    call.skill_name,
                    call._temp_root,
                )
            else:
                try:
                    shutil.rmtree(call._temp_root)
                except OSError as e:
                    log.warning(
                        "Could not delete skill '%s' temporary folder %s: %s",
                        call.skill_name,
                        call._temp_root,
                        e,
                    )


def current() -> SkillCall:
    """The running skill tool call.

    Raises:
        SkillContextError: called outside a skill tool, e.g. at import time.
    """
    call = _ACTIVE.get()
    if call is None:
        raise SkillContextError(
            "gaia.skills.context works only inside a skill tool while an agent "
            "runs it. Call it from the @tool function body, not at import time."
        )
    return call


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------


def is_path_argument(name: str) -> bool:
    """True when a tool parameter's name says it holds a path."""
    return bool(_PATH_ARG_RE.search(name))


def path_argument_refusal(agent: Any, tool_args: Dict[str, Any]) -> Optional[dict]:
    """The read refusal for the first out-of-scope path argument, or ``None``."""
    from gaia.agents.tools.path_access import read_access_error

    for name, value in tool_args.items():
        if not is_path_argument(name):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item.strip():
                refusal = read_access_error(agent, os.path.expanduser(item))
                if refusal is not None:
                    return refusal
    return None


def check_read(path: str | os.PathLike) -> Path:
    """*path*, resolved, if the agent may read it.

    Raises:
        SkillPermissionError: outside the agent's allowed folders, or a secret.
    """
    from gaia.agents.tools.path_access import read_access_error

    call = current()
    resolved = Path(os.path.expanduser(str(path))).resolve()
    refusal = read_access_error(call.agent, str(resolved))
    if refusal is not None:
        raise SkillPermissionError(refusal["error"])
    return resolved


def check_write(path: str | os.PathLike, *, content_size: int = 0) -> Path:
    """*path*, resolved, if the agent may write it (asks before overwriting).

    Raises:
        SkillPermissionError: outside the agent's allowed folders, a protected
            location, or the user declined.
    """
    from gaia.agents.tools.path_access import write_access_error

    call = current()
    resolved = Path(os.path.expanduser(str(path))).resolve()
    refusal = write_access_error(call.agent, str(resolved), content_size=content_size)
    if refusal is not None:
        raise SkillPermissionError(refusal["error"])
    return resolved


def open_read(path: str | os.PathLike) -> IO[bytes]:
    """Open a file the agent may read, in binary mode.

    The same check as :func:`check_read`, then ``open(path, "rb")``. The
    caller decodes (and may wrap it in ``gzip`` or ``io.TextIOWrapper``). A
    skill that reads only through here needs no ``filesystem`` permission:
    the agent's allowed folders already bound what it can open.

    Raises:
        SkillPermissionError: outside the agent's allowed folders, or a secret.
        OSError: the file cannot be opened.
    """
    return open(check_read(path), "rb")


def open_write(
    path: str | os.PathLike,
    *,
    binary: bool = False,
    encoding: str = "utf-8",
    newline: Optional[str] = None,
) -> IO[Any]:
    """Create or replace a file the agent may write, after :func:`check_write`.

    Missing parent folders are created inside the checked path's scope. An
    existing file goes through the validator's overwrite confirmation.

    Raises:
        SkillPermissionError: outside the allowed folders, a protected
            location, or the user declined the overwrite.
        OSError: the file cannot be created.
    """
    target = check_write(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        return open(target, "wb")
    return open(target, "w", encoding=encoding, newline=newline)


def list_readable(root: str | os.PathLike, *, recursive: bool = True) -> List[Path]:
    """Files under *root* the agent may read, sorted, hidden entries skipped.

    *root* itself goes through :func:`check_read` (with its approval prompt);
    entries found inside never prompt, and secrets or links that leave the
    allowed folders are left out, as the agent's own folder walks do.

    Raises:
        SkillPermissionError: *root* is outside the agent's allowed folders.
    """
    from gaia.agents.tools.path_access import readable_entry

    call = current()
    base = check_read(root)
    found: List[Path] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if not name.startswith("."):
                    found.append(Path(dirpath) / name)
    else:
        found = sorted(
            p for p in base.iterdir() if p.is_file() and not p.name.startswith(".")
        )
    return [p for p in found if readable_entry(call.agent, p)]


# ----------------------------------------------------------------------
# Temporary files
# ----------------------------------------------------------------------


def _temp_root(call: SkillCall) -> Path:
    call.require("scratch:write", "use a temporary folder")
    if call._temp_root is None:
        from gaia.config import gaia_home

        base = gaia_home() / "skill-temp" / call.skill_name
        base.mkdir(parents=True, exist_ok=True)
        call._temp_root = Path(tempfile.mkdtemp(prefix="call-", dir=base))
    return call._temp_root


def _inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise SkillPermissionError(
            f"'{relative}' leaves the call's temporary folder. Use a relative "
            "name such as 'out/report.pdf'."
        )
    return target


def temp_dir(subdir: Optional[str] = None) -> Path:
    """This call's temporary folder (or a subfolder of it), created if needed.

    Requires ``scratch:write``. Deleted when the tool call returns.
    """
    root = _temp_root(current())
    target = _inside(root, subdir) if subdir else root
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_temp_file(name: str, data: str | bytes) -> Path:
    """Write *data* to *name* inside the call's temporary folder."""
    target = _inside(_temp_root(current()), name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        target.write_bytes(data)
    else:
        target.write_text(data, encoding="utf-8")
    return target


def copy_to_temp(source: str | os.PathLike, name: Optional[str] = None) -> Path:
    """Copy a file the agent may read into the call's temporary folder.

    For work on a copy, so the user's file is never changed.
    """
    readable = check_read(source)
    if not readable.is_file():
        raise FileNotFoundError(f"No file at {readable}.")
    target = _inside(_temp_root(current()), name or readable.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(readable, target)
    return target


# ----------------------------------------------------------------------
# Programs
# ----------------------------------------------------------------------


def find_program(name: str) -> Optional[str]:
    """Absolute path of *name* on PATH or in a known install folder, or None."""
    found = shutil.which(name)
    if found:
        return found
    for pattern in KNOWN_INSTALL_PATHS.get(name, {}).get(platform.system(), ()):
        try:
            candidate = Path(pattern.format_map(os.environ))
        except KeyError:
            continue  # that Program Files variable is not set on this machine
        if candidate.is_file():
            return str(candidate)
    return None


def run_program(
    name: str,
    args: Sequence[str],
    *,
    timeout: float,
    cwd: Optional[str | os.PathLike] = None,
) -> subprocess.CompletedProcess:
    """Run the program the manifest names, and return its finished process.

    Args:
        name: The executable as declared, e.g. ``"soffice"`` for
            ``program:run:soffice``. Found on PATH or in a known install folder.
        args: Arguments, one string each. There is no shell, so nothing is
            expanded or chained.
        timeout: Seconds before the program is killed (max
            :data:`MAX_PROGRAM_TIMEOUT_S`).
        cwd: Working folder. Defaults to the call's temporary folder when the
            skill declares ``scratch:write``; otherwise must be readable.

    Returns:
        The completed process, with ``stdout`` / ``stderr`` as text. A non-zero
        exit is returned, not raised: the skill decides what it means.

    Raises:
        SkillPermissionError: ``program:run:<name>`` is not declared.
        FileNotFoundError: the program is not installed.
        subprocess.TimeoutExpired: it ran past *timeout* and was killed.
    """
    call = current()
    call.require(f"program:run:{name}", f"run '{name}'")
    if isinstance(args, (str, bytes)) or not all(isinstance(a, str) for a in args):
        raise TypeError("run_program args must be a list of strings, one per argument.")
    if not 0 < timeout <= MAX_PROGRAM_TIMEOUT_S:
        raise ValueError(
            f"run_program timeout must be between 0 and {MAX_PROGRAM_TIMEOUT_S}s, "
            f"got {timeout}."
        )
    executable = find_program(name)
    if executable is None:
        raise FileNotFoundError(
            f"'{name}' is not installed (not on PATH or in its usual install "
            f"folder), so skill '{call.skill_name}' cannot run it. Tell the user "
            f"to install it."
        )
    if cwd is not None:
        workdir: Optional[Path] = check_read(cwd)
    elif call.declares("scratch", "write"):
        workdir = temp_dir()
    else:
        workdir = None
    env = {k: os.environ[k] for k in PROGRAM_ENV_KEYS if k in os.environ}
    if call._temp_root is not None:
        for key in ("TMP", "TEMP", "TMPDIR"):
            env[key] = str(call._temp_root)
    log.info(
        "Skill '%s' runs %s (%d arg(s), timeout %ss)",
        call.skill_name,
        executable,
        len(args),
        timeout,
    )
    # No shell: the executable is the declared program, resolved to a path.
    return subprocess.run(
        [executable, *args],
        cwd=str(workdir) if workdir is not None else None,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ----------------------------------------------------------------------
# Preferences
# ----------------------------------------------------------------------


class SkillPreferences:
    """This skill's saved preferences, as the running agent may use them."""

    def __init__(self, call: SkillCall, store: SkillPreferenceStore) -> None:
        self._call = call
        self._store = store

    def get(self) -> Dict[str, Any]:
        """The saved preferences (``{}`` when none). Works in private chats."""
        return self._store.get(self._call.skill_name)

    def _skipped(self) -> Optional[PreferenceWrite]:
        agent = self._call.agent
        if not getattr(agent, "_incognito", False):
            return None
        from gaia.agents.base.memory import memory_off_reason

        return PreferenceWrite(
            saved=False,
            preferences=self.get(),
            note=f"{memory_off_reason(agent)} The preference was not saved.",
        )

    def update(self, changes: Dict[str, Any]) -> PreferenceWrite:
        """Merge *changes* (``None`` removes a key) and save, unless private.

        Raises:
            SkillValidationError: the result breaks ``preferences.schema.json``.
        """
        skipped = self._skipped()
        if skipped is not None:
            return skipped
        schema = load_schema(getattr(self._call.skill, "directory", None))
        saved = self._store.update(self._call.skill_name, changes, schema=schema)
        return PreferenceWrite(saved=True, preferences=saved)

    def reset(self) -> PreferenceWrite:
        """Forget every saved preference, unless private."""
        skipped = self._skipped()
        if skipped is not None:
            return skipped
        self._store.reset(self._call.skill_name)
        return PreferenceWrite(saved=True, preferences={})


def preferences(store: Optional[SkillPreferenceStore] = None) -> SkillPreferences:
    """This skill's preferences. No permission needed: they are its own."""
    return SkillPreferences(current(), store or SkillPreferenceStore())


__all__: List[str] = [
    "SkillContextError",
    "check_read",
    "check_write",
    "copy_to_temp",
    "current",
    "find_program",
    "is_path_argument",
    "preferences",
    "run_program",
    "temp_dir",
    "write_temp_file",
]
