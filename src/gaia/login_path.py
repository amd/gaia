# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The user's login ``PATH`` for processes the macOS app starts.

An app opened from Finder, the Dock or launchd gets only the system folders on
``PATH``, so Homebrew tools (``/opt/homebrew/bin``) are invisible to
``shutil.which`` and to every child process. :func:`apply_login_path` reads the
``PATH`` a Terminal window would have and gives it to this process once.
"""

import logging
import os
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

LOGIN_PATH_TIMEOUT_S = 5.0
_PATH_BEGIN = "__GAIA_LOGIN_PATH_BEGIN__"
_PATH_END = "__GAIA_LOGIN_PATH_END__"
APPLIED_MARKER = "GAIA_LOGIN_PATH_APPLIED"

_login_path_applied = False


class LoginPathError(RuntimeError):
    """The user's login ``PATH`` could not be read; the message says why."""


def _account_login_shell() -> str:
    """The login shell recorded for this account, never ``$SHELL``.

    ``$SHELL`` is ordinary environment, and ``.env`` fills the environment from
    the working directory: trusting it would let a project file pick the
    program GAIA runs at startup.
    """
    try:
        import pwd  # pylint: disable=import-outside-toplevel

        shell = pwd.getpwuid(os.getuid()).pw_shell
    except (ImportError, KeyError) as exc:
        raise LoginPathError(
            f"no login shell on record for this account: {exc}"
        ) from exc
    if not shell or not os.path.isabs(shell) or not os.access(shell, os.X_OK):
        raise LoginPathError(f"the account's login shell {shell!r} is not runnable")
    return shell


def _print_path_script(shell: str) -> str:
    """The command that prints ``PATH`` between the markers, in *shell*'s syntax."""
    if os.path.basename(shell) == "fish":
        # fish keeps PATH as a list and has no ${VAR}.
        return f"printf '%s%s%s' {_PATH_BEGIN} (string join : $PATH) {_PATH_END}"
    return f'printf "%s" "{_PATH_BEGIN}${{PATH}}{_PATH_END}"'


def _shell_env() -> dict:
    """The startup environment without GAIA's own credentials.

    Rc files are the user's code and may log or echo what they inherit.
    """
    import gaia  # pylint: disable=import-outside-toplevel
    from gaia.env import is_internal_secret  # pylint: disable=import-outside-toplevel

    return {
        name: value
        for name, value in gaia.pre_dotenv_environ().items()
        if not is_internal_secret(name)
    }


def read_login_path(
    shell: Optional[str] = None, timeout: float = LOGIN_PATH_TIMEOUT_S
) -> str:
    """The ``PATH`` a Terminal window for this user would have.

    Runs the login shell once, interactive and login so ``~/.zprofile`` and
    ``~/.zshrc`` both apply (Homebrew's ``brew shellenv`` lives in either). It
    gets the environment as it was before ``.env`` was merged in: a project's
    ``.env`` must not be able to point ``ZDOTDIR``, ``HOME`` or ``ENV`` at
    startup files of its own. The value is read back between markers so a
    banner an rc file prints cannot corrupt it. POSIX-style shells and fish are
    supported; anything else ends in a :class:`LoginPathError` that names it.
    """
    shell = shell or _account_login_shell()
    try:
        done = subprocess.run(  # nosec B603 - the account's own login shell, fixed script
            [shell, "-ilc", _print_path_script(shell)],
            stdin=subprocess.DEVNULL,
            # Own session: an interactive shell with no controlling terminal
            # cannot take the user's terminal over, and a timeout reaches it.
            start_new_session=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=_shell_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise LoginPathError(
            f"{shell} -ilc did not finish within {timeout:g}s; a slow or "
            "interactive line in your shell startup files is the usual cause"
        ) from exc
    except OSError as exc:
        raise LoginPathError(f"could not run {shell}: {exc}") from exc
    begin = done.stdout.rfind(_PATH_BEGIN)
    end = done.stdout.rfind(_PATH_END)
    if begin == -1 or end < begin:
        raise LoginPathError(
            f"{shell} -ilc exited {done.returncode} without printing PATH "
            "(only POSIX-style shells and fish are supported)"
        )
    value = done.stdout[begin + len(_PATH_BEGIN) : end].strip()
    if not value:
        raise LoginPathError(f"{shell} -ilc printed an empty PATH")
    return value


def merge_paths(login: str, inherited: str) -> str:
    """Login entries first, then any inherited entry the login ``PATH`` lacks.

    Login order wins so a command resolves the way it does in the user's own
    terminal; inherited-only entries are kept behind them rather than dropped.
    """
    seen = set()
    merged = []
    for entry in (login + os.pathsep + inherited).split(os.pathsep):
        if entry and entry not in seen:
            seen.add(entry)
            merged.append(entry)
    return os.pathsep.join(merged)


def apply_login_path() -> bool:
    """Give this process the user's login ``PATH`` on macOS. Idempotent.

    Called once at each long-running entry point; it is not a per-command
    override, and a command still cannot set ``PATH`` itself. Returns True when
    the login ``PATH`` was applied. When it cannot be read the process keeps the
    ``PATH`` it has and a warning says why (stderr, never stdout, which is the
    event wire for stdio transports).

    Skipped, returning False, when ``PATH`` is already the user's: a process
    started from a shell has ``SHLVL`` (a Finder, Dock or launchd launch never
    does), and a child of a process that applied it carries the marker. Both are
    read from the pre-``.env`` environment so a project file cannot set them.
    Merging over a shell's ``PATH`` would put ``path_helper``'s order ahead of an
    activated virtualenv, and every command would pay for a login shell.
    """
    global _login_path_applied
    if sys.platform != "darwin":
        return False
    if _login_path_applied:
        return True
    import gaia  # pylint: disable=import-outside-toplevel

    if gaia.pre_dotenv_env("SHLVL") or gaia.pre_dotenv_env(APPLIED_MARKER):
        return False
    try:
        login = read_login_path()
    except LoginPathError as exc:
        print(
            f"WARNING: GAIA could not read your login PATH ({exc}). Tools "
            "installed outside the system directories, such as Homebrew's, may "
            "not be reachable from the agent's shell.",
            file=sys.stderr,
            flush=True,
        )
        return False
    os.environ["PATH"] = merge_paths(login, os.environ.get("PATH", ""))
    os.environ[APPLIED_MARKER] = "1"
    _login_path_applied = True
    logger.debug("Applied login PATH: %s", os.environ["PATH"])
    return True
