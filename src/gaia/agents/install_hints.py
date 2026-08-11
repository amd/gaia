# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared "agent wheel not installed" messaging.

The ``gaia-agent-*`` wheels (chat, email, code, sd, docker, ...) are built
and packaged (``hub/agents/<id>/python/``) but publishing them to PyPI is
still paused (see ``.github/workflows/publish_agents.yml``, tracked by
#1179 / #1513). Until that lands, ``pip install gaia-agent-<id>`` and
``pip install "amd-gaia[agents]"`` both fail on a clean environment (#2240)
-- so every call site that used to recommend them needs to point at the one
install path that actually resolves today: pip installing straight from the
package's subdirectory in this repo.
"""

import sys

# hub/agents/<subdir>/python for each wheel this module has a hint for. Keep
# in sync with the directories under hub/agents/ (ls hub/agents/).
_AGENT_SOURCE_SUBDIRS = {
    "gaia-agent-analyst": "analyst",
    "gaia-agent-blender": "blender",
    "gaia-agent-browser": "browser",
    "gaia-agent-chat": "chat",
    "gaia-agent-code": "code",
    "gaia-agent-connectors-demo": "connectors-demo",
    "gaia-agent-docker": "docker",
    "gaia-agent-docqa": "docqa",
    "gaia-agent-email": "email",
    "gaia-agent-emr": "emr",
    "gaia-agent-fileio": "fileio",
    "gaia-agent-jira": "jira",
    "gaia-agent-routing": "routing",
    "gaia-agent-sd": "sd",
    "gaia-agent-summarize": "summarize",
}

_REPO_URL = "https://github.com/amd/gaia.git"


def source_install_command(wheel: str) -> str:
    """Return the pip command that installs ``wheel`` straight from source.

    Uses ``sys.executable -m pip`` rather than a bare ``uv`` binary: a stock
    ``python -m venv`` has neither the ``uv`` executable on PATH nor the
    ``uv`` Python module, so hard-coding ``uv pip install`` here would just
    move the #2240 dead end from ``pip install gaia-agent-<id>`` to this
    hint's own recommended command. ``python -m pip`` is the one frontend
    every stock venv provides (the same last-resort fallback
    ``InitCommand._install_pip_extras`` already uses for this reason).

    Raises ``KeyError`` if ``wheel`` isn't a known ``gaia-agent-*`` package --
    that's a bug at the call site (a typo'd wheel name), not a runtime
    condition to swallow.
    """
    subdir = _AGENT_SOURCE_SUBDIRS[wheel]
    return (
        f'{sys.executable} -m pip install "{wheel} @ git+{_REPO_URL}#subdirectory='
        f'hub/agents/{subdir}/python"'
    )


def agent_not_installed_message(
    subject: str, wheel: str, *, next_step: str = ""
) -> str:
    """Build the standard "agent not installed" error text for ``wheel``.

    ``subject`` is the complete first sentence, without a trailing period,
    e.g. ``"The chat agent is not installed"`` or ``"The drafting eval needs
    the email agent"``. ``next_step`` is an optional trailing instruction,
    e.g. ``"Then re-run `gaia chat`."``.

    The ``gaia-agent-*`` wheels aren't on PyPI yet (#2240), so this
    deliberately does NOT recommend ``pip install gaia-agent-<id>`` or
    ``pip install "amd-gaia[agents]"`` -- both fail on a clean environment.
    It points at the verified working install instead: pip installing
    straight from this repo's subdirectory.
    """
    command = source_install_command(wheel)
    message = (
        f"{subject}. The `{wheel}` package isn't published yet "
        f"(see https://github.com/amd/gaia/issues/2240); install it from "
        f"source instead:\n`{command}`"
    )
    if next_step:
        message = f"{message} {next_step}"
    return message
