# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Process-environment helpers: ``.env`` loading and child-process environments.

Every ``.env`` load in GAIA goes through :func:`load_env`. Processes GAIA
starts for skills and tools get their environment from :func:`child_env`.

A ``.env`` comes from whatever directory GAIA runs in, so what it loads is for
GAIA alone: :func:`child_env` withholds every name it set except
:data:`DOTENV_CHILD_ALLOWLIST`.
"""

import os
import re
from typing import Dict, Iterable, Mapping, Optional, Set

import dotenv

NO_DOTENV_ENV_VAR = "GAIA_NO_DOTENV"
CHILD_ENV_DENY_ENV_VAR = "GAIA_CHILD_ENV_DENY"
# Names a ``.env`` set, handed to GAIA processes this one starts (sidecars,
# the UI server) so they withhold them too — those inherit them as real vars.
DOTENV_KEYS_ENV_VAR = "GAIA_DOTENV_KEYS"

# The only ``.env`` names a child process still receives: non-secret settings
# GAIA reads to find its config and its Lemonade server, which a GAIA or
# Lemonade process it starts must agree on. No credential belongs here.
DOTENV_CHILD_ALLOWLIST = frozenset(
    {
        "GAIA_CONFIG_DIR",
        "GAIA_CONFIG_FILE",
        "GAIA_CTX_SIZE",
        "GAIA_HOME",
        "GAIA_LEMONADE_EMBEDDED",
        "LEMONADE_BASE_URL",
        "LEMONADE_CTX_SIZE",
        "LEMONADE_HOST",
        "LEMONADE_MODEL",
        "LEMONADE_PORT",
        "LEMONADE_SERVER_PATH",
    }
)

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

_dotenv_names: Set[str] = set()


def _env_name(name: str) -> str:
    """*name* as this OS compares environment names (Windows ignores case)."""
    return name.upper() if os.name == "nt" else name


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

    The file is the nearest ``.env`` at or above the working directory — the
    same lookup the TUI's readiness gate makes, whatever way GAIA is installed.
    Variables already set in the environment win. Every name the file supplies
    is recorded so :func:`child_env` can withhold it. Returns True when a
    ``.env`` file was found and loaded.
    """
    if dotenv_disabled():
        return False
    # Without usecwd, python-dotenv searches from this file's directory, which
    # in a wheel install is site-packages, never the user's project.
    path = dotenv.find_dotenv(usecwd=True)
    if not path:
        return False
    import gaia  # pylint: disable=import-outside-toplevel

    startup = {_env_name(name) for name in gaia.pre_dotenv_environ()}
    before = {_env_name(name) for name in os.environ}
    loaded = dotenv.load_dotenv(path)
    # Both views: what this call added, and what the file holds that the real
    # environment lacked (a repeat call adds nothing but still came from it).
    added = {_env_name(name) for name in os.environ} - before
    in_file = {_env_name(name) for name in dotenv.dotenv_values(path)} - startup
    _dotenv_names.update((added | in_file) - {_env_name(DOTENV_KEYS_ENV_VAR)})
    names = dotenv_names()
    if names:
        os.environ[DOTENV_KEYS_ENV_VAR] = ",".join(sorted(names))
    return loaded


def dotenv_names() -> Set[str]:
    """Names that came from a ``.env`` — this process's or a GAIA parent's.

    The parent's list is read from the startup environment, so a ``.env`` file
    cannot shorten it.
    """
    import gaia  # pylint: disable=import-outside-toplevel

    inherited = gaia.pre_dotenv_env(DOTENV_KEYS_ENV_VAR) or ""
    names = {_env_name(part) for part in re.split(r"[,\s]+", inherited) if part}
    return (names | _dotenv_names) - {_env_name(DOTENV_KEYS_ENV_VAR)}


def _dotenv_withheld() -> Set[str]:
    allowed = {_env_name(name) for name in DOTENV_CHILD_ALLOWLIST}
    return dotenv_names() - allowed


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
    name a ``.env`` file set (bar :data:`DOTENV_CHILD_ALLOWLIST`), without any
    name listed in ``GAIA_CHILD_ENV_DENY`` (comma or space separated) or in
    ``deny``, with ``extra`` overlaid last — a name the caller passes
    explicitly is always delivered. ``os.environ`` is never modified.
    """
    blocked = _configured_deny() | {name.upper() for name in deny}
    from_dotenv = _dotenv_withheld()
    env = {
        name: value
        for name, value in os.environ.items()
        if not is_internal_secret(name)
        and name.upper() not in blocked
        and _env_name(name) not in from_dotenv
    }
    if extra:
        env.update(extra)
    return env
