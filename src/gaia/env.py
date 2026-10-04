# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Process-environment helpers: ``.env`` loading and child-process environments.

Every ``.env`` load in GAIA goes through :func:`load_env`. Processes GAIA
starts for skills and tools get their environment from :func:`child_env`.
"""

import os
import re
from typing import Dict, Iterable, Mapping, Optional

import dotenv

NO_DOTENV_ENV_VAR = "GAIA_NO_DOTENV"
CHILD_ENV_DENY_ENV_VAR = "GAIA_CHILD_ENV_DENY"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# GAIA's own credentials, handed to sidecars by the daemon. Kept in sync with
# gaia.daemon.constants / gaia.daemon.custody.constants (a unit test pins this).
_INTERNAL_SECRET_NAMES = frozenset(
    {
        "GAIA_MODEL_BROKER_TOKEN",
        "GAIA_MODEL_BROKER_TOKEN_FILE",
        "GAIA_HOST_CUSTODY_SECRET",
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
