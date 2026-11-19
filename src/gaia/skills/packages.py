# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Python packages a skill declares, checked when the skill loads.

A skill names what its tools import under
``metadata.gaia.requirements.dependencies``. Before this module nothing looked
at that list: a skill whose package was missing either failed to load on the
import error or loaded and then failed mid-task. Now a load checks the list
against the interpreter the tools run in and, where the user has agreed to it,
installs what is missing from PyPI with the same installer and pins as
``gaia init --data-libraries``.

Installing without asking is limited to a skill GAIA trusts (bundled with the
agent, or installed from the hub at ``verified``) and a user who has full
access on or has not turned the recommended libraries off. Every other case
loads the skill's instructions, leaves its tools out, and says which packages
are missing and the exact command that installs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List

from gaia.agents.install_hints import pip_install_hint
from gaia.frozen import is_frozen
from gaia.logger import get_logger
from gaia.skills.errors import SkillValidationError
from gaia.skills.format import Skill
from gaia.skills.manager import ROOT_AGENT_BUNDLED

if TYPE_CHECKING:
    from gaia.installer.data_libraries import LibraryStatus

log = get_logger(__name__)

#: The tier a hub install is attested at before its code is trusted unasked.
_TRUSTED_TIER = "verified"


@dataclass
class PackageCheck:
    """What a load found, and did, about one skill's declared packages."""

    #: Requirements installed by this load, as handed to the installer.
    installed: List[str] = field(default_factory=list)
    #: Requirements still not met; the skill's tools stay unregistered.
    missing: List[str] = field(default_factory=list)
    #: Why they are missing and what to run, for the load result.
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.missing


def is_trusted(skill: Skill) -> bool:
    """Whether GAIA may install *skill*'s packages without asking each time."""
    return skill.root == ROOT_AGENT_BUNDLED or skill.security_tier == _TRUSTED_TIER


def _pins() -> dict:
    from gaia.installer import data_libraries

    try:
        return data_libraries.pinned_libraries()
    except RuntimeError as e:
        # An install whose metadata predates the extra: the skill's own
        # version range is then the whole contract.
        log.warning("Skill packages are checked without GAIA's pins: %s", e)
        return {}


def package_status(skill: Skill) -> List["LibraryStatus"]:
    """Each package *skill* declares with the version this Python has.

    Raises:
        SkillValidationError: an entry is not a plain package requirement.
    """
    declared = list(skill.gaia.requirements.dependencies)
    if not declared:
        return []
    # Imported only for a skill that declares packages: most declare none.
    from gaia.installer import data_libraries

    try:
        return data_libraries.requirement_status(
            {skill.name: declared}, _pins(), include_pins=False
        )
    except ValueError as e:
        raise SkillValidationError(
            f"Skill '{skill.name}': metadata.gaia.requirements.dependencies: {e}"
        ) from e


def _install_command(skill: Skill, wanted: List["LibraryStatus"]) -> str:
    from gaia.installer import data_libraries

    if skill.root == ROOT_AGENT_BUNDLED:
        return data_libraries.SETUP_COMMAND
    return pip_install_hint(*[s.requirement for s in wanted])


def _unavailable(skill: Skill) -> str:
    tools = ", ".join(skill.tool_names)
    return f"its tools ({tools}) are unavailable" if tools else "it cannot run them"


def ensure_packages(skill: Skill, console: Any = None) -> PackageCheck:
    """Check *skill*'s declared packages, installing the missing ones if allowed.

    Never raises for a missing package or a failed install: the result says
    what is missing and what to run, so the caller can load the skill's
    instructions without its tools.

    Raises:
        SkillValidationError: the skill declares something that is not a plain
            package requirement (a URL, a path, an installer option).
    """
    # The binary has no package metadata to read and nowhere to install to.
    if is_frozen():
        return PackageCheck()
    status = package_status(skill)
    wanted = [s for s in status if not s.ok]
    if not wanted:
        return PackageCheck()
    from gaia.installer import data_libraries

    missing = [s.requirement for s in wanted]
    names = ", ".join(missing)
    command = _install_command(skill, wanted)
    again = f"run `{command}` in a terminal, then load the skill again"
    opening = f"Skill '{skill.name}' is loaded, but {_unavailable(skill)}"

    if not is_trusted(skill):
        return PackageCheck(
            missing=missing,
            note=(
                f"{opening}: it needs {names}, and GAIA installs packages on "
                f"its own only for skills it ships or has verified. Tell the "
                f"user to {again}."
            ),
        )
    full_access = getattr(console, "full_access", False) is True
    if not full_access and data_libraries.declined():
        return PackageCheck(
            missing=missing,
            note=(
                f"{opening}: it needs {names}, and the user turned the "
                f"recommended Python libraries off. Tell the user to {again}."
            ),
        )

    log.info("Skill '%s' needs %s; installing from PyPI", skill.name, names)
    try:
        data_libraries.install(log.info, lambda: package_status(skill))
    except data_libraries.DataLibrariesError as e:
        log.error("Skill '%s': packages not installed: %s", skill.name, e)
        return PackageCheck(
            missing=missing,
            note=(
                f"{opening}: {names} could not be installed "
                f"({str(e).splitlines()[0]}). Tell the user to {again}."
            ),
        )
    log.info("Skill '%s': installed %s", skill.name, names)
    return PackageCheck(installed=missing)
