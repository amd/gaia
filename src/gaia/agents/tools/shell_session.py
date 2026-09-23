# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A shell session's working directory, persisted across separate tool calls.

One-shot execution is materially worse than a human terminal for the
build/test loop an agent spends most of its time in: ``cd build`` in one call
is invisible to the next. No system prompt can fix that — the state simply is
not there to observe.

Scope, deliberately narrow: this persists ``cd`` only. ``run_shell_command``
never hands a command string to a real shell — each segment of a chain runs as
argv (``_run_step`` in ``shell_tools.py``), which is what keeps `&&`/`;`/`2>/dev/null`
meaning what they say instead of becoming quoted literal arguments, and is what
lets a skill-granted CLI run on untrusted text without a shell ever seeing it
(``lone_granted_segment``). A real ``export``/``source`` step needs a real shell
interpreting it to capture state *from* — there is no execution path here that
can observe one, so that capability is not something a future version of this
class can add without reopening the shell-string question this design
deliberately avoids.

Earlier iteration of this idea generated a per-command shell/batch script,
ran it, and parsed its ``pwd``/environment dump back out (port of the C++
toolbelt's ``ShellSession``, #2810) — that round-trip is what produced the
AWKPATH/AWKLIBPATH pollution and Windows env case-folding bugs flagged in the
original review. Tracking cwd in Python, the way `_resolve_cd_target`
(``shell_tools.py``) already does per chain, needs no round-trip and cannot
reintroduce them.
"""

from pathlib import Path
from typing import Callable, Optional


class ShellSessionError(RuntimeError):
    """Base for the errors a session raises instead of degrading quietly."""


class ShellSessionClosed(ShellSessionError):
    """The session was torn down and will not track state anymore."""


class ShellSession:
    """The working directory an agent's ``run_shell_command`` calls persist."""

    def __init__(
        self,
        start_cwd: Optional[str] = None,
        cwd_guard: Optional[Callable[[str], bool]] = None,
    ):
        """
        Args:
            start_cwd: Initial working directory. Defaults to the process cwd.
            cwd_guard: Consulted before absorbing a directory a command changed
                into. Returning False keeps the session where it was — without
                it, ``cd`` would be a way to reach paths the caller's path
                policy refuses.
        """
        resolved = Path(start_cwd).resolve() if start_cwd else Path.cwd()
        self._start_cwd = str(resolved)
        self._cwd = str(resolved)
        self._cwd_guard = cwd_guard
        self._closed = False

    @property
    def cwd(self) -> str:
        """Current working directory of the session."""
        return self._cwd

    @property
    def closed(self) -> bool:
        return self._closed

    def set_cwd(self, directory: str) -> bool:
        """Checkpoint the session's cwd.

        False (and the session unchanged) if *directory* is not a real
        directory, or the cwd guard refuses it. The caller decides what to do
        with a refusal; this never raises for a bad path, only for a session
        that has already been closed.
        """
        if self._closed:
            raise ShellSessionClosed(
                "This shell session was closed. Start a new task, or reset the "
                "session, to track directory changes again."
            )
        resolved = Path(directory).resolve()
        if not resolved.is_dir():
            return False
        if self._cwd_guard is not None and not self._cwd_guard(str(resolved)):
            return False
        self._cwd = str(resolved)
        return True

    def reset(self) -> None:
        """Forget any directory change and return to the starting directory."""
        self._cwd = self._start_cwd

    def close(self) -> None:
        """Tear the session down. Later ``set_cwd`` calls raise."""
        self._closed = True
