# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Process-environment helpers: ``.env`` loading and child-process environments.

Every ``.env`` load in GAIA goes through :func:`load_env`. Processes GAIA
starts for skills and tools get their environment from :func:`child_env`.
"""

import logging
import os
import re
import subprocess
import sys
from typing import Dict, Iterable, Mapping, Optional

import dotenv

logger = logging.getLogger(__name__)

NO_DOTENV_ENV_VAR = "GAIA_NO_DOTENV"
CHILD_ENV_DENY_ENV_VAR = "GAIA_CHILD_ENV_DENY"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# GAIA's own credentials: what the daemon hands its sidecars, plus the
# engineering MCP pairing token. Kept in sync with gaia.daemon.constants,
# gaia.daemon.custody.constants and gaia.engineering.handoff (a test pins this).
_INTERNAL_SECRET_NAMES = frozenset(
    {
        "GAIA_MODEL_BROKER_TOKEN",
        "GAIA_MODEL_BROKER_TOKEN_FILE",
        "GAIA_HOST_CUSTODY_SECRET",
        "GAIA_ENGINEERING_TOKEN",
    }
)
_SIDECAR_TOKEN_RE = re.compile(r"^GAIA_[A-Z0-9_]+_SIDECAR_TOKEN(_FILE)?$")


def dotenv_disabled() -> bool:
    """True when the operator set ``GAIA_NO_DOTENV`` in the real environment.

    Read from the pre-``.env`` snapshot so a ``.env`` file cannot switch the
    guard off (or on) for itself.
    """
    import gaia  # pylint: disable=import-outside-toplevel

    value = gaia.pre_dotenv_env(NO_DOTENV_ENV_VAR) or ""
    return value.strip().lower() in _TRUTHY


def load_env() -> bool:
    """Load ``.env`` into ``os.environ`` unless ``GAIA_NO_DOTENV`` is set.

    Returns True when a ``.env`` file was found and loaded.
    """
    if dotenv_disabled():
        return False
    return dotenv.load_dotenv()


def is_internal_secret(name: str) -> bool:
    """True for environment variables that carry GAIA's own credentials."""
    upper = name.upper()
    return upper in _INTERNAL_SECRET_NAMES or bool(_SIDECAR_TOKEN_RE.match(upper))


def _configured_deny() -> set:
    raw = os.environ.get(CHILD_ENV_DENY_ENV_VAR, "")
    return {part.upper() for part in re.split(r"[,\s]+", raw) if part}


def child_env(
    extra: Optional[Mapping[str, str]] = None, deny: Iterable[str] = ()
) -> Dict[str, str]:
    """A fresh environment for a child process GAIA spawns.

    A copy of ``os.environ`` without GAIA's internal credentials, without any
    name listed in ``GAIA_CHILD_ENV_DENY`` (comma or space separated) or in
    ``deny``, with ``extra`` overlaid last — a name the caller passes
    explicitly is always delivered. ``os.environ`` is never modified.
    """
    blocked = _configured_deny() | {name.upper() for name in deny}
    env = {
        name: value
        for name, value in os.environ.items()
        if not is_internal_secret(name) and name.upper() not in blocked
    }
    if extra:
        env.update(extra)
    return env


LOGIN_PATH_TIMEOUT_S = 5.0
_PATH_BEGIN = "__GAIA_LOGIN_PATH_BEGIN__"
_PATH_END = "__GAIA_LOGIN_PATH_END__"

_login_path_applied = False


class LoginPathError(RuntimeError):
    """The user's login ``PATH`` could not be read; the message says why."""


def _account_login_shell() -> str:
    """The login shell recorded for this account, never ``$SHELL``.

    ``$SHELL`` is ordinary environment, and ``load_env`` fills the environment
    from a ``.env`` file in the working directory: trusting it would let a
    project file pick the program GAIA runs at startup.
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


def read_login_path(
    shell: Optional[str] = None, timeout: float = LOGIN_PATH_TIMEOUT_S
) -> str:
    """The ``PATH`` a Terminal window for this user would have.

    Runs the login shell once, interactive and login so ``~/.zprofile`` and
    ``~/.zshrc`` both apply (Homebrew's ``brew shellenv`` lives in either). The
    value is read back between markers, so a banner an rc file prints cannot
    corrupt it. Raises :class:`LoginPathError` on a hang, a failed run or a
    missing marker.
    """
    shell = shell or _account_login_shell()
    script = f'printf "%s" "{_PATH_BEGIN}${{PATH}}{_PATH_END}"'
    try:
        done = (
            subprocess.run(  # nosec B603 - the account's own login shell, fixed script
                [shell, "-ilc", script],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
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
            f"{shell} -ilc exited {done.returncode} without printing PATH"
        )
    value = done.stdout[begin + len(_PATH_BEGIN) : end].strip()
    if not value:
        raise LoginPathError(f"{shell} -ilc printed an empty PATH")
    return value


def merge_paths(login: str, inherited: str) -> str:
    """Login entries first, then any inherited entry the login ``PATH`` lacks.

    Login order wins so a command resolves the way it does in the user's own
    terminal; inherited-only entries (GAIA's managed tool directories) are kept
    behind them rather than dropped.
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

    An app started from Finder, the Dock or launchd gets only the system
    directories, so Homebrew tools (``/opt/homebrew/bin``) are invisible to
    ``shutil.which`` and to every child process. This is the one place that
    repairs it, called once at each process entry point; it is not a per-command
    override, and a command still cannot set ``PATH`` itself.

    Returns True when the login ``PATH`` was applied. When it cannot be read the
    process keeps the ``PATH`` it has and a warning says why (stderr, never
    stdout, which is the event wire for stdio transports).
    """
    global _login_path_applied
    if sys.platform != "darwin":
        return False
    if _login_path_applied:
        return True
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
    _login_path_applied = True
    logger.debug("Applied login PATH: %s", os.environ["PATH"])
    return True
