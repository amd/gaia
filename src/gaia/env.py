# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Process-environment helpers: ``.env`` loading and child-process environments.

Every ``.env`` load in GAIA goes through :func:`load_env`. Processes GAIA
starts for skills and tools get their environment from :func:`child_env`.
"""

import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

import dotenv

NO_DOTENV_ENV_VAR = "GAIA_NO_DOTENV"
CHILD_ENV_DENY_ENV_VAR = "GAIA_CHILD_ENV_DENY"

_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# All a .env outside GAIA's install may supply. Paths, URLs, proxies, CAs,
# loader variables and GAIA/Lemonade settings would let any folder redirect
# or run code in GAIA, so they must come from the real environment.
WORKING_DIR_CREDENTIALS = frozenset({"ANTHROPIC_API_KEY"})

_working_dir_credentials: Dict[str, str] = {}
_working_dir_credential_files: Dict[str, str] = {}
_reported_dotenv_files: set = set()


def _env_name(name: str) -> str:
    """*name* as this OS compares environment names (Windows ignores case)."""
    return name.upper() if os.name == "nt" else name


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


def _install_tree_dotenv() -> Optional[Path]:
    """The nearest ``.env`` at or above GAIA's own package directory.

    None in a frozen build: it unpacks under a shared temp folder, so a walk up
    from there would reach a ``.env`` any local user can write.
    """
    if getattr(sys, "frozen", False):
        return None
    start = Path(__file__).resolve().parent
    for folder in (start, *start.parents):
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate
    return None


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _load_working_dir_credentials(path: Path) -> None:
    """Keep the allowlisted credentials from *path*; name everything else."""
    values = dotenv.dotenv_values(path)
    names = {_env_name(name): name for name in WORKING_DIR_CREDENTIALS}
    ignored = []
    invalid = 0
    for name, value in values.items():
        allowed = names.get(_env_name(name))
        if allowed is not None:
            if value and allowed not in os.environ:
                _working_dir_credentials.setdefault(allowed, value)
                _working_dir_credential_files.setdefault(allowed, str(path))
        elif _ENV_NAME_RE.match(name):
            ignored.append(name)
        else:
            invalid += 1
    key = str(path.resolve())
    if (ignored or invalid) and key not in _reported_dotenv_files:
        _reported_dotenv_files.add(key)
        # Names come from the file: list only plain identifiers, never raw text.
        parts = sorted(ignored) + ([f"{invalid} invalid names"] if invalid else [])
        from gaia.logger import get_logger  # pylint: disable=import-outside-toplevel

        get_logger(__name__).warning(
            "Ignored %s from %r: a .env in the working directory may only supply "
            "%s. Export the others in your shell instead.",
            ", ".join(parts),
            str(path),
            ", ".join(sorted(WORKING_DIR_CREDENTIALS)),
        )


def load_env() -> bool:
    """Load ``.env`` settings unless ``GAIA_NO_DOTENV`` is set.

    The ``.env`` at or above GAIA's own install loads in full into
    ``os.environ``, as it always has. A ``.env`` found from the working
    directory instead may be any project's, so only
    :data:`WORKING_DIR_CREDENTIALS` are read from it — kept out of
    ``os.environ`` so no process GAIA starts inherits them (read them with
    :func:`credential`) — and the other names it holds are logged as ignored.
    Variables already set in the environment win. Returns True when a ``.env``
    file was found.
    """
    if dotenv_disabled():
        return False
    found = False
    install = _install_tree_dotenv()
    if install is not None:
        dotenv.load_dotenv(install)
        found = True
    # usecwd: python-dotenv otherwise searches from this file, never the cwd.
    cwd_path = dotenv.find_dotenv(usecwd=True)
    if cwd_path and not (install and _same_file(Path(cwd_path), install)):
        _load_working_dir_credentials(Path(cwd_path))
        found = True
    return found


def credential(name: str) -> Optional[str]:
    """*name* from the environment, else from a working-directory ``.env``.

    For GAIA's own use only: the ``.env`` value never enters ``os.environ``.
    """
    return os.environ.get(name) or _working_dir_credentials.get(name)


def working_dir_credential_file(name: str) -> Optional[str]:
    """The working-directory ``.env`` that supplied *name*, when it was one.

    None when the environment holds *name*, since that value is the one used.
    """
    if os.environ.get(name):
        return None
    return _working_dir_credential_files.get(name)


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
