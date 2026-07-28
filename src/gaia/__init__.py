# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GAIA - Generative AI Is Awesome

AMD's framework for running generative AI applications locally on AMD hardware.
"""

# Load environment variables from .env file BEFORE any other imports
# This ensures all SDK components respect .env configuration
import logging as _logging
import os as _os
import typing as _typing

from dotenv import load_dotenv

# The REAL process environment, captured before ``.env`` is merged in.
_PRE_DOTENV_ENVIRON = dict(_os.environ)

load_dotenv()


def pre_dotenv_env(name: str) -> "_typing.Optional[str]":
    """Read a variable from the process environment as it was at startup.

    Security-sensitive opt-ins must use this instead of ``os.environ``: a
    ``.env`` file travels with a directory and is not a decision the operator
    made, so it must not be able to grant a permission (#2210 —
    ``GAIA_AUTO_APPROVE_TOOLS``). Everything else should keep reading
    ``os.environ``; configuring the rest through ``.env`` is intended.
    """
    return _PRE_DOTENV_ENVIRON.get(name)


# Warn loudly when a ``.env`` tried to grant unattended tool approval. The
# guarantee comes from ``pre_dotenv_env`` above, not from scrubbing — several
# modules call ``load_dotenv()`` again on import, which would re-inject it.
# Keep the name in sync with ``gaia.agents.base.console.AUTO_APPROVE_ENV_VAR``
# (a unit test pins this).
_AUTO_APPROVE_TOOLS_VAR = "GAIA_AUTO_APPROVE_TOOLS"
if (
    _AUTO_APPROVE_TOOLS_VAR in _os.environ
    and _AUTO_APPROVE_TOOLS_VAR not in _PRE_DOTENV_ENVIRON
):
    _logging.getLogger(__name__).warning(
        "Ignoring %s from a .env file — unattended approval of confirmation-gated "
        "tools must come from the process environment, not a project file.",
        _AUTO_APPROVE_TOOLS_VAR,
    )

# pylint: disable=wrong-import-position
from gaia.agents.base import Agent, MCPAgent, tool  # noqa: F401, E402
from gaia.database import DatabaseAgent, DatabaseMixin  # noqa: F401, E402
from gaia.utils import FileChangeHandler, FileWatcher, FileWatcherMixin  # noqa: F401

__all__ = [
    "Agent",
    "DatabaseAgent",
    "DatabaseMixin",
    "FileChangeHandler",
    "FileWatcher",
    "FileWatcherMixin",
    "MCPAgent",
    "tool",
]
