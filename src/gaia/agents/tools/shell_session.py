# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A shell whose working directory and environment survive between commands.

One-shot execution is materially worse than a human terminal for the
build/test loop an agent spends most of its time in: ``cd build`` in one call
is invisible to the next, and so is ``export CC=clang`` or a virtualenv
activation. No system prompt can fix that — the state simply is not there to
observe. A :class:`ShellSession` keeps it.

This is a port of the C++ toolbelt's ``gaia::ShellSession``
(``cpp/include/gaia/process.h``, #2810). What persists is the *session state*,
not a resident child process: each call runs a generated script that restores
the session's cwd and variables, sources the command from its own file, then
writes the resulting ``pwd`` and environment to a side file that the session
absorbs. A resident shell driven over pipes would have to survive a timeout to
be worth anything, and a timed-out command that is still inside a shared shell
is exactly the corruption this is meant to avoid — so there is no long-lived
child to orphan, on any platform.

The security model is unchanged. This class only preserves state; it does not
decide what may run. Command validation stays with the caller and applies per
command exactly as before.

Two further properties of the generated script are worth knowing:

- stdin is ``/dev/null``. An interactive command (``git commit`` opening an
  editor, ``sudo``, ``npm login``) returns immediately instead of sitting until
  the timeout, because a tool call has no way to answer it.
- On timeout the whole process *group* is killed, not just the shell — a build
  or test command spawns children, and leaving them running is what makes a
  timed-out build worse than useless.

Windows without a POSIX shell: commands run as a ``cmd.exe`` batch script,
because keeping ``cd`` and ``set`` requires running in the same interpreter and
cmd only offers that to a script. ``%VAR%`` expansion is identical to a prompt,
but a ``for`` loop variable is written ``%%i`` rather than ``%i``. Point
``GAIA_SHELL`` at a POSIX shell (Git Bash, MSYS, WSL) and none of this applies.
"""

import locale
import logging
import os
import re
import shutil
import signal
import subprocess  # nosec B404 - running shell commands is this module's purpose
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Separates the reported cwd from the environment dump in the state file.
_ENV_MARKER = "---GAIA-ENV---"

#: Variables every shell rewrites on its own. Replaying them would make the
#: session drift a little further from the parent on every command.
_VOLATILE_ENV_NAMES = frozenset(
    {
        "_",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "PS1",
        "PS2",
        "RANDOM",
        "SECONDS",
        "LINENO",
        "PROMPT",
        "CD",
        "ERRORLEVEL",
        "CMDCMDLINE",
        "CMDEXTVERSION",
        "__GAIA_RC",
        # A POSIX shell exports these itself on startup, so they show up as a
        # divergence the agent never asked for. Reporting them would tell the
        # model it had changed the environment when it had only run a command.
        "COLUMNS",
        "LINES",
        "AWKPATH",
        "IFS",
        "OPTIND",
        "OPTARG",
        "PPID",
        "BASHOPTS",
        "SHELLOPTS",
        "BASH_VERSION",
        "BASH_SUBSHELL",
    }
)

_VALID_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env_key(name: str) -> str:
    """The name to compare environment variables under.

    Windows variable names are case-insensitive and ``os.environ`` upper-cases
    them, while ``set`` reports the original casing. Comparing raw would make
    every inherited variable look changed *and* look removed, so the session
    would replay the whole environment and unset it at the same time.
    """
    return name.upper() if os.name == "nt" else name


#: How long to wait for a killed process tree to release its pipes.
_DRAIN_TIMEOUT_SECONDS = 5.0

#: How long to give the platform's tree-killer before giving up on it.
_TREE_KILL_TIMEOUT_SECONDS = 15.0


class ShellSessionError(RuntimeError):
    """Base for the errors a session raises instead of degrading quietly."""


class ShellSessionBusy(ShellSessionError):
    """Another command still holds the session.

    A tool call that exceeds its timeout leaves its worker thread running
    (#2600), so a previous command can still be inside the subprocess when the
    next one arrives. Blocking forever would wedge the agent; this says so.
    """


class ShellSessionClosed(ShellSessionError):
    """The session was torn down and will not run anything else."""


@dataclass
class ShellResult:
    """What one command in a session produced."""

    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    timed_out: bool = False
    duration_seconds: float = 0.0
    cwd: str = ""
    #: Set when the command's ``cd`` was refused by the session's cwd guard.
    cwd_change_rejected: Optional[str] = None


@dataclass
class _State:
    """The cwd and environment divergence the session carries forward."""

    cwd: str
    overrides: Dict[str, str] = field(default_factory=dict)
    unset: set = field(default_factory=set)


def _is_valid_env_name(name: str) -> bool:
    return bool(_VALID_ENV_NAME.match(name))


def _posix_quote(value: str) -> str:
    """Wrap *value* in single quotes for POSIX sh."""
    return "'" + value.replace("'", "'\\''") + "'"


def _batch_quote(value: str) -> str:
    """Escape *value* for use inside a batch file.

    Only ``%`` needs escaping. ``"`` must be left alone: ``set "K=V"`` takes
    everything up to the *last* quote on the line, so doubling a quote is not
    undone — and because the session re-captures and re-emits the value, every
    command would double it again until the line broke.
    """
    return value.replace("%", "%%")


def _generic_path(path: str) -> str:
    """Forward-slash form, which every POSIX shell accepts — Git Bash included."""
    return Path(path).as_posix()


def _split_env_record(record: str, out: Dict[str, str]) -> None:
    """Split one ``NAME=VALUE`` record.

    Names are not validated here: ``ProgramFiles(x86)`` is a real Windows
    variable, and mis-parsing it would corrupt the neighbour it sorts next to.
    """
    name, sep, value = record.partition("=")
    if not sep or not name:
        return
    out[name] = value


def _parse_env_records_nul(text: str) -> Dict[str, str]:
    """Parse the NUL-delimited environment dump the POSIX script emits.

    NUL is the one byte an environment value cannot contain, so the record
    boundary is unambiguous. Line-delimited ``env`` output is not: a value
    containing a newline followed by ``SOMETHING=x`` is indistinguishable from
    a second variable, which would let a command's *data* become the session's
    *configuration*.
    """
    out: Dict[str, str] = {}
    for record in text.split("\0"):
        if record:
            _split_env_record(record, out)
    return out


def _parse_env_lines(text: str) -> Dict[str, str]:
    """Parse ``cmd.exe`` ``set`` output.

    Windows environment values cannot contain a newline, so one line is exactly
    one variable and an unparseable line is dropped rather than glued onto its
    predecessor.
    """
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line or line.startswith("="):
            # cmd.exe lists internal "=C:" / "=ExitCode" entries; not variables.
            continue
        _split_env_record(line, out)
    return out


def _terminate_tree(proc: "subprocess.Popen") -> None:
    """Kill *proc* and everything it spawned.

    Killing only the shell leaves the build it started running, which is what
    makes a timed-out command worse than useless.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # No process groups: taskkill /T walks the parent-child tree instead.
        # It takes seconds on a busy box, which is the price of not orphaning
        # the build a timed-out command started.
        try:
            subprocess.run(  # nosec B603 B607 - fixed argv, pid from our own child
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=_TREE_KILL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.warning("taskkill did not finish for pid %s", proc.pid)
        proc.kill()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError) as exc:
            logger.warning("Could not kill process group for pid %s: %s", proc.pid, exc)
            proc.kill()


class ShellSession:
    """A shell session whose cwd and exported environment persist across calls.

    Calls on the same session are serialised by an internal lock, because they
    share one logical shell state. Distinct sessions never collide: the cwd and
    the variables are applied *inside the child*, so the calling process is
    never mutated.
    """

    def __init__(
        self,
        start_cwd: Optional[str] = None,
        shell: Optional[str] = None,
        cwd_guard: Optional[Callable[[str], bool]] = None,
    ):
        """
        Args:
            start_cwd: Initial working directory. Defaults to the process cwd.
            shell: Shell to run commands with. ``None`` means ``/bin/sh`` on
                POSIX and ``cmd.exe`` on Windows; ``GAIA_SHELL`` overrides both.
                Naming a POSIX shell on Windows makes the session generate a
                shell script for it instead of a batch file.
            cwd_guard: Consulted before absorbing a directory a command changed
                into. Returning False keeps the session where it was — without
                it, ``cd`` would be a way to reach paths the caller's path
                policy refuses.
        """
        resolved = Path(start_cwd).resolve() if start_cwd else Path.cwd()
        self._state = _State(cwd=str(resolved))
        self._start_cwd = str(resolved)
        self._baseline_env = {_env_key(k): v for k, v in os.environ.items()}
        self._cwd_guard = cwd_guard
        self._lock = threading.Lock()
        self._closed = False
        self._temp_dir: Optional[str] = None

        configured = shell if shell is not None else os.environ.get("GAIA_SHELL", "")
        configured = configured.strip()
        if os.name == "nt":
            self._shell = configured
            self._posix_script = bool(configured)
        else:
            self._shell = configured or "/bin/sh"
            self._posix_script = True

    # -- state ------------------------------------------------------------

    @property
    def cwd(self) -> str:
        """Current working directory of the session."""
        return self._state.cwd

    @property
    def posix_script(self) -> bool:
        """True when commands run as a POSIX shell script rather than a batch file."""
        return self._posix_script

    def environment(self) -> Dict[str, str]:
        """Variables the session has diverged from the parent environment."""
        return dict(self._state.overrides)

    def removed_environment(self) -> List[str]:
        """Inherited variables the session's commands have unset."""
        return sorted(self._state.unset)

    def effective_env(self) -> Dict[str, str]:
        """The environment a command would see, for callers that bypass the script."""
        env = os.environ.copy()
        for name in self._state.unset:
            env.pop(name, None)
        env.update(self._state.overrides)
        return env

    def set_cwd(self, directory: str) -> bool:
        """Set the working directory. False (and unchanged) if it is not a directory."""
        resolved = Path(directory).resolve()
        if not resolved.is_dir():
            return False
        self._state.cwd = str(resolved)
        return True

    def set_env(self, name: str, value: str) -> None:
        """Set a variable for subsequent commands in this session."""
        self._state.overrides[name] = value
        self._state.unset.discard(name)

    def reset(self) -> None:
        """Forget every environment change and return to the starting directory."""
        self._state = _State(cwd=self._start_cwd)

    def close(self) -> None:
        """Tear the session down. Later calls raise :class:`ShellSessionClosed`.

        There is no resident child to kill — every command is waited on or
        killed (with its process group) inside :meth:`run`. What is left is the
        session's temp directory, which this removes.

        A command still running past its tool timeout (#2600) is holding files
        in that directory, so the removal is handed to it rather than pulled out
        from under it.
        """
        self._closed = True
        if self._lock.acquire(blocking=False):
            try:
                self._discard_temp_dir()
            finally:
                self._lock.release()
        else:
            logger.info(
                "Shell session closed while a command was still running; its temp "
                "directory is removed when that command finishes."
            )

    def _discard_temp_dir(self) -> None:
        temp_dir, self._temp_dir = self._temp_dir, None
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @property
    def closed(self) -> bool:
        return self._closed

    # -- execution --------------------------------------------------------

    def run(
        self,
        command: str,
        timeout: float = 30.0,
        working_directory: Optional[str] = None,
    ) -> ShellResult:
        """Run *command* in the session, then absorb the state it left behind.

        Args:
            command: The shell command to execute.
            timeout: Seconds before the command's process group is killed.
            working_directory: Run this one command elsewhere. A one-shot
                override: the session's own directory is untouched, and a
                ``cd`` inside such a command is not absorbed.

        Raises:
            ShellSessionClosed: the session was torn down.
            ShellSessionBusy: another command still holds the session.
        """
        if self._closed:
            raise ShellSessionClosed(
                "This shell session was closed. Start a new task, or reset the "
                "session, to run commands again."
            )
        if not command.strip():
            raise ValueError("Empty command")

        # Bounded, so a command still running past its tool timeout (#2600)
        # surfaces as an actionable error instead of wedging the agent.
        if not self._lock.acquire(timeout=max(1.0, float(timeout))):
            raise ShellSessionBusy(
                "Another command is still running in this shell session. Wait for "
                "it to finish, or reset the session to abandon it and start clean."
            )
        try:
            return self._run_locked(command, timeout, working_directory)
        finally:
            self._lock.release()

    def run_argv(
        self,
        argv: List[str],
        timeout: float = 30.0,
        working_directory: Optional[str] = None,
    ) -> ShellResult:
        """Run *argv* directly, with the session's cwd and environment applied.

        For callers that must not hand a command string to a shell — a binary
        invoked with arguments built from untrusted text. The session's state is
        applied, but nothing is absorbed: an argv call cannot ``cd`` or
        ``export`` in the first place.
        """
        if self._closed:
            raise ShellSessionClosed(
                "This shell session was closed. Start a new task, or reset the "
                "session, to run commands again."
            )
        if not self._lock.acquire(timeout=max(1.0, float(timeout))):
            raise ShellSessionBusy(
                "Another command is still running in this shell session. Wait for "
                "it to finish, or reset the session to abandon it and start clean."
            )
        try:
            cwd = working_directory or self._state.cwd
            result = self._spawn(argv, cwd, self.effective_env(), timeout, shell=False)
            result.cwd = self._state.cwd
            return result
        finally:
            self._lock.release()

    # -- internals --------------------------------------------------------

    def _run_locked(
        self,
        command: str,
        timeout: float,
        working_directory: Optional[str],
    ) -> ShellResult:
        temp_dir = self._ensure_temp_dir()
        script_ext = ".sh" if self._posix_script else ".cmd"
        start_cwd = working_directory or self._state.cwd

        state_file = self._write_temp(temp_dir, ".state", "")
        command_file = self._write_temp(temp_dir, script_ext, command + "\n")
        script_file = self._write_temp(
            temp_dir,
            script_ext,
            self._build_script(start_cwd, command_file, state_file),
        )

        try:
            if self._posix_script:
                argv = [self._shell, _generic_path(script_file)]
            else:
                # /d skips AutoRun, which would otherwise prepend a user's
                # profile commands to every agent command.
                argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", script_file]

            # No cwd/env arguments: the script applies both inside the child, so
            # the calling process is never mutated and sessions cannot collide.
            result = self._spawn(argv, None, None, timeout, shell=False)
            rejected = self._absorb_state(
                self._read_state(state_file), absorb_cwd=working_directory is None
            )
            result.cwd = start_cwd if working_directory else self._state.cwd
            result.cwd_change_rejected = rejected
            return result
        finally:
            for path in (state_file, command_file, script_file):
                try:
                    os.remove(path)
                except OSError as exc:
                    logger.warning("Could not remove temp file %s: %s", path, exc)
            if self._closed:
                # Closed while this command was running; it is ours to clean up.
                self._discard_temp_dir()

    def _ensure_temp_dir(self) -> str:
        if self._temp_dir is None or not os.path.isdir(self._temp_dir):
            self._temp_dir = tempfile.mkdtemp(prefix="gaia_shell_")
        return self._temp_dir

    @staticmethod
    def _write_temp(temp_dir: str, extension: str, contents: str) -> str:
        """Create a uniquely-named file in *temp_dir* and write *contents* to it.

        Exclusive creation, so a pre-planted symlink cannot be written through.
        """
        fd, path = tempfile.mkstemp(suffix=extension, dir=temp_dir)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(contents)
        return path

    def _build_script(self, cwd: str, command_file: str, state_file: str) -> str:
        """The per-command script: restore state, run the command, report state.

        The command lives in its own file and is sourced (``.`` / ``call``)
        rather than pasted in here. Pasting lets a command ending in a line
        continuation, or an unterminated heredoc, swallow the framework's own
        bookkeeping lines — which corrupts the reported exit code and leaks
        internals into what the model reads back.
        """
        state = self._state
        lines: List[str] = []
        if self._posix_script:
            lines.append(f"cd {_posix_quote(_generic_path(cwd))} || exit 127")
            for name in sorted(state.unset):
                if _is_valid_env_name(name):
                    lines.append(f"unset {name}")
            for name, value in sorted(state.overrides.items()):
                if _is_valid_env_name(name):
                    lines.append(f"{name}={_posix_quote(value)}; export {name}")
            lines.append(f". {_posix_quote(_generic_path(command_file))} < /dev/null")
            lines.append("__gaia_rc=$?")
            # awk's ENVIRON gives NUL-delimited records; `env` output cannot be
            # parsed unambiguously (see _parse_env_records_nul).
            lines.append(
                "{ pwd; printf '%s\\n' "
                + _posix_quote(_ENV_MARKER)
                + '; awk \'BEGIN { for (k in ENVIRON) printf "%s=%s%c", k, '
                "ENVIRON[k], 0 }'; } > "
                + _posix_quote(_generic_path(state_file))
                + " 2>/dev/null"
            )
            lines.append("exit $__gaia_rc")
            return "\n".join(lines) + "\n"

        lines.append("@echo off")
        lines.append(f'cd /d "{_batch_quote(cwd)}"')
        lines.append("if errorlevel 1 exit /b 127")
        for name in sorted(state.unset):
            if _is_valid_env_name(name):
                lines.append(f'set "{name}="')
        for name, value in sorted(state.overrides.items()):
            if _is_valid_env_name(name):
                lines.append(f'set "{name}={_batch_quote(value)}"')
        lines.append(f'call "{command_file}" <nul')
        lines.append("set __GAIA_RC=%ERRORLEVEL%")
        lines.append(f'> "{state_file}" (')
        lines.append("  cd")
        lines.append(f"  echo {_ENV_MARKER}")
        lines.append("  set")
        lines.append(")")
        lines.append("exit /b %__GAIA_RC%")
        return "\r\n".join(lines) + "\r\n"

    def _read_state(self, state_file: str) -> str:
        try:
            raw = Path(state_file).read_bytes()
        except OSError:
            return ""
        encoding = "utf-8" if self._posix_script else locale.getpreferredencoding(False)
        return raw.decode(encoding, errors="replace")

    def _absorb_state(self, state_text: str, absorb_cwd: bool) -> Optional[str]:
        """Take on the cwd and environment the command left behind.

        A command that calls ``exit`` terminates the script before the
        bookkeeping runs, so its changes are not captured; the session keeps its
        previous state rather than guessing.

        Returns a message when a directory change was refused by the cwd guard.
        """
        marker = state_text.find(_ENV_MARKER)
        if marker == -1:
            return None

        rejected: Optional[str] = None
        reported_cwd = state_text[:marker].strip()
        if reported_cwd and absorb_cwd:
            # The shell is the authority on where it ended up. With a Git Bash
            # shell on Windows that is an MSYS path (`/c/...`) the Win32 API
            # does not recognise but the next script — run by the same shell —
            # does, so it is taken as reported rather than validated away.
            if self._cwd_guard is not None and not self._cwd_guard(reported_cwd):
                rejected = (
                    f"Directory change to '{reported_cwd}' was not applied: that "
                    "path is outside the directories this agent may access. The "
                    f"session stayed in '{self._state.cwd}'."
                )
                logger.info("Refused session cwd change to %r", reported_cwd)
            else:
                self._state.cwd = reported_cwd

        env_text = state_text[marker + len(_ENV_MARKER) :].lstrip("\r\n")
        captured = (
            _parse_env_records_nul(env_text)
            if self._posix_script
            else _parse_env_lines(env_text)
        )
        if not captured:
            return rejected

        overrides: Dict[str, str] = {}
        unset = set()
        # Only replayable names participate: `ProgramFiles(x86)` is a real
        # Windows variable but no `set NAME=` / `export NAME` can name it.
        captured_keys = {
            _env_key(name) for name in captured if _is_valid_env_name(name)
        }
        for name, value in captured.items():
            key = _env_key(name)
            if key in _VOLATILE_ENV_NAMES or not _is_valid_env_name(name):
                continue
            if self._baseline_env.get(key) != value:
                overrides[name] = value
        for key in self._baseline_env:
            if key in _VOLATILE_ENV_NAMES or not _is_valid_env_name(key):
                continue
            if key not in captured_keys:
                unset.add(key)
        self._state.overrides = overrides
        self._state.unset = unset
        return rejected

    @staticmethod
    def _spawn(
        argv,
        cwd: Optional[str],
        env: Optional[Dict[str, str]],
        timeout: float,
        shell: bool,
    ) -> ShellResult:
        """Run *argv*, capping the wait and killing the whole tree on expiry.

        encoding/errors are explicit, and load-bearing. Bare ``text=True``
        decodes with the locale codec — cp1252 on a default Windows box — inside
        subprocess's pipe reader THREAD. A byte that codec cannot map raises
        there, the thread dies, and the caller gets returncode 0 with EMPTY
        stdout: the command succeeded and its output was silently discarded.
        """
        start = time.monotonic()
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            # stdin is DEVNULL, never inherited. This process's stdin is the
            # agent transport's pipe — held open and never written to — so a
            # child that reads it blocks forever on input that cannot arrive.
            "stdin": subprocess.DEVNULL,
            "cwd": cwd,
            "env": env,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": shell,
        }
        if os.name != "nt":
            # Its own process group, so a timeout can kill the command's
            # children too rather than just the shell that started them.
            popen_kwargs["start_new_session"] = True

        with subprocess.Popen(argv, **popen_kwargs) as proc:  # nosec B603
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                _terminate_tree(proc)
                timed_out = True
                try:
                    stdout, stderr = proc.communicate(timeout=_DRAIN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    stdout, stderr = "", ""
            return_code = proc.returncode if proc.returncode is not None else -1

        return ShellResult(
            stdout=stdout or "",
            stderr=stderr or "",
            return_code=return_code,
            timed_out=timed_out,
            duration_seconds=time.monotonic() - start,
        )
