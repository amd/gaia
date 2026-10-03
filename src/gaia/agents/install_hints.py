# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared "agent wheel not installed" messaging.

The ``gaia-agent-*`` wheels (gaia, chat, email) are built
and packaged (``hub/agents/<id>/python/``) but publishing them to PyPI is
still paused (see ``.github/workflows/publish_agents.yml``, tracked by
#1179 / #1513). Until that lands, ``pip install gaia-agent-<id>`` and
``pip install "amd-gaia[agents]"`` both fail on a clean environment (#2240)
-- so every call site that used to recommend them needs to point at the one
install path that actually resolves today: pip installing straight from the
package's subdirectory in this repo.
"""

import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from typing import Optional

from gaia.version import __version__ as _CORE_VERSION

# hub/agents/<subdir>/python for each wheel this module has a hint for. Keep
# in sync with the directories under hub/agents/ (ls hub/agents/).
_AGENT_SOURCE_SUBDIRS = {
    "gaia-agent-chat": "chat",
    "gaia-agent-email": "email",
    "gaia-agent-gaia": "gaia",
}

_REPO_URL = "https://github.com/amd/gaia.git"

# Agent ids the ``gaia-agent-chat`` wheel registers — its three prompt
# profiles. All three are ``hidden`` (resolvable, not selectable), so a stored
# session can still ask for one on a box that never installed the wheel; the
# answer there is this package's install command, not "pick another agent".
CHAT_WHEEL_AGENT_IDS = frozenset({"chat", "doc", "file"})


def source_install_command(wheel: str, *, force_reinstall: bool = False) -> str:
    """Return the pip command that installs ``wheel`` straight from source.

    Uses ``sys.executable -m pip`` rather than a bare ``uv`` binary: a stock
    ``python -m venv`` has neither the ``uv`` executable on PATH nor the
    ``uv`` Python module, so hard-coding ``uv pip install`` here would just
    move the #2240 dead end from ``pip install gaia-agent-<id>`` to this
    hint's own recommended command. ``python -m pip`` is the one frontend
    every stock venv provides (the same last-resort fallback
    ``InitCommand._install_pip_extras`` already uses for this reason).

    The URL is pinned to the installed core's release tag: an unpinned URL
    pulls ``main``, whose agent code can need core symbols this release
    lacks (#4586).

    Raises ``KeyError`` if ``wheel`` isn't a known ``gaia-agent-*`` package --
    that's a bug at the call site (a typo'd wheel name), not a runtime
    condition to swallow.
    """
    subdir = _AGENT_SOURCE_SUBDIRS[wheel]
    flag = "--force-reinstall " if force_reinstall else ""
    return (
        f'{sys.executable} -m pip install {flag}"{wheel} @ git+{_REPO_URL}'
        f'@v{_CORE_VERSION}#subdirectory=hub/agents/{subdir}/python"'
    )


def _installed_version(wheel: str) -> Optional[str]:
    try:
        return _dist_version(wheel)
    except PackageNotFoundError:
        return None


def agent_not_installed_message(
    subject: str,
    wheel: str,
    *,
    next_step: str = "",
    error: Optional[BaseException] = None,
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

    If ``wheel`` is in fact installed, the import failed for another reason
    (usually version skew with the core), so ``subject`` would be wrong: the
    message instead reports ``error``, both versions, and a pinned reinstall.
    """
    wheel_version = _installed_version(wheel)
    if wheel_version is not None:
        detail = f": {error}" if error is not None else ""
        command = source_install_command(wheel, force_reinstall=True)
        message = (
            f"`{wheel}` {wheel_version} is installed but failed to import "
            f"against `amd-gaia` {_CORE_VERSION}{detail}. The agent and core "
            f"versions likely don't match; reinstall the agent from the tag "
            f"matching your core:\n`{command}`"
        )
    else:
        command = source_install_command(wheel)
        message = (
            f"{subject}. The `{wheel}` package isn't published yet "
            f"(see https://github.com/amd/gaia/issues/2240); install it from "
            f"source instead:\n`{command}`"
        )
    if next_step:
        message = f"{message} {next_step}"
    return message
