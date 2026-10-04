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

import importlib.metadata
import sys
from typing import Optional

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


def _installed_version(package: str) -> Optional[str]:
    """Installed version string for ``package``, or None when absent."""
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def source_install_command(wheel: str, *, force_reinstall: bool = False) -> str:
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
    # Pin the ref to the installed core: the wheels track this repo's trunk,
    # so pulling `main` against an older released core is exactly the version
    # skew that produced the misdiagnosed "not installed" ImportErrors.
    core_version = _installed_version("amd-gaia")
    ref = f"@v{core_version}" if core_version else ""
    flag = "--force-reinstall --no-deps " if force_reinstall else ""
    return (
        f'{sys.executable} -m pip install {flag}"{wheel} @ git+{_REPO_URL}{ref}'
        f'#subdirectory=hub/agents/{subdir}/python"'
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


def agent_wheel_failed_message(
    subject: str, wheel: str, error: ImportError, *, next_step: str = ""
) -> str:
    """Build the error text for an installed wheel that fails to import.

    A blanket "not installed" answer here misdiagnoses version skew (a wheel
    importing symbols a newer/older core does not have) as a missing package,
    and reinstalling from a mismatched ref reproduces the same failure. Name
    both package versions and surface the real import error instead.
    """
    installed = _installed_version(wheel) or "unknown"
    core = _installed_version("amd-gaia") or "unknown"
    if subject.endswith(" is not installed"):
        opening = (
            subject[: -len(" is not installed")]
            + " is installed, but it could not be imported"
        )
    else:
        opening = (
            f"{subject}. The `{wheel}` package is installed, but it could "
            "not be imported"
        )
    message = (
        f"{opening}.\n"
        f"Detected versions: {wheel} {installed}, amd-gaia {core}\n"
        f"Import error: {type(error).__name__}: {error}\n"
        "This is usually a version skew between the wheel and the installed "
        "core. Reinstall the wheel built from the matching core tag:\n"
        f"`{source_install_command(wheel, force_reinstall=True)}`"
    )
    if next_step:
        message = f"{message} {next_step}"
    return message


def agent_import_error_message(
    error: ImportError, subject: str, wheel: str, *, next_step: str = ""
) -> str:
    """Pick the right error message for a failed agent-wheel import.

    Only a ``ModuleNotFoundError`` that names the wheel's own top-level
    package means the package is genuinely absent. Anything else -- a
    missing transitive dependency, or an ImportError from a version-skewed
    wheel -- means the package is installed but broken.
    """
    top_level = wheel.replace("-", "_")
    if isinstance(error, ModuleNotFoundError) and (
        error.name == top_level or (error.name or "").startswith(top_level + ".")
    ):
        return agent_not_installed_message(subject, wheel, next_step=next_step)
    return agent_wheel_failed_message(subject, wheel, error, next_step=next_step)
