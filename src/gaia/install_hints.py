# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One truthful install hint for agents that ship outside the core wheel.

Agents live in ``hub/agents/python/<id>/`` and are not published to PyPI, so
a plain ``pip install`` of an agent distribution cannot work. The meta-extra
that used to be recommended alongside it was worse than not working: being
unsatisfiable, it made pip/uv backtrack to an old core version that lacked
the extra and silently *downgrade* the user's install (issue #2240). Both
phrases were copy-pasted into ~20 error messages; this module replaces them.

What does work today is a git install pinned to the running core's version,
which this module is the single source of. Keep it dependency-free (stdlib
only) — it is imported from lightweight consumers such as
``gaia.mcp.servers.docker_mcp`` and ``gaia.eval.*``.

Publishing real wheels is tracked by #1179 / #1513; when that lands, this is
the one place to change.
"""

from __future__ import annotations

import importlib.metadata
from typing import Optional

_REPO = "https://github.com/amd/gaia.git"


def agent_install_command(agent_id: str) -> str:
    """Return the ``pip install`` command that actually installs *agent_id*.

    The git ref is pinned to the installed core's version so the agent a user
    is told to install always matches the core they are running. An unpinned
    ``main`` ref would build whatever HEAD happens to be at install time and
    execute its ``setup.py`` — there is no precedent for that anywhere in
    this repo, and it is not something to put in a user-facing hint.

    Raises:
        RuntimeError: If the ``amd-gaia`` distribution has no installed
            metadata to read a version from, since the alternative would be
            emitting an unpinned command (CLAUDE.md: no silent fallbacks).
    """
    try:
        core_version = importlib.metadata.version("amd-gaia")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            "Cannot build an agent install command: the 'amd-gaia' "
            "distribution has no installed metadata, so there is no version "
            "to pin the agent to. Install GAIA into this environment "
            "('pip install -e .' from a checkout, or 'pip install amd-gaia') "
            "and retry. See issue #2240."
        ) from exc
    return (
        f'pip install "git+{_REPO}@v{core_version}'
        f'#subdirectory=hub/agents/python/{agent_id}"'
    )


def agent_not_installed_message(
    agent_id: str,
    *,
    package: str,
    retry_command: str,
    also_see: Optional[str] = None,
) -> str:
    """Return the standard "this agent isn't installed" message.

    Args:
        agent_id: Hub directory name, e.g. ``chat`` — also the git
            subdirectory the install command points at.
        package: Human-readable name for the message, e.g. ``chat agent``.
        retry_command: What the user should re-run afterwards, e.g.
            ``gaia chat``.
        also_see: Optional extra pointer appended as its own sentence, e.g.
            ``gaia chat --ui``.

    Raises:
        RuntimeError: Propagated from :func:`agent_install_command` when the
            core version cannot be determined.
    """
    message = (
        f"The {package} is not installed. Install it with:\n"
        f"  {agent_install_command(agent_id)}\n"
        f"Then re-run `{retry_command}`."
    )
    if also_see:
        message += f" Custom and additional agents run in `{also_see}`."
    return message
