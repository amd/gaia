# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Version resolution for declared skills — the half ``gaia.skills.sets`` defers.

``gaia.skills.sets`` is the single parser for the declaration grammar
(``skills:``, ``skill_sets:``, ``default_skill_set:``), producing ``SkillRef``
objects that record a version range without acting on it. This module acts on it:
:func:`requirements_from_refs` adapts those refs into :class:`SkillRequirement`\\ s
and :func:`resolve_requirements` matches each range against what is installed
locally, orders by dependency, and splits required from optional.

**Ordering.** Declaration order is preserved, then a skill that another skill's
``tools_required`` depends on is loaded first. A cycle raises instead of picking a
winner: a "topological order" that silently breaks ties is not an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from gaia.logger import get_logger
from gaia.skills.errors import SkillNotFoundError, SkillValidationError
from gaia.skills.format import Skill
from gaia.skills.manager import SkillManager
from gaia.skills.versions import matches

log = get_logger(__name__)

#: Manifest filename an agent package ships (the hub's agent manifest).
AGENT_MANIFEST_FILENAME = "gaia-agent.yaml"


@dataclass(frozen=True)
class SkillRequirement:
    """One ``skills:`` entry: a name, a SemVer range, and whether it is required."""

    name: str
    version: str = "*"
    required: bool = True
    #: Where this requirement came from, for error messages ("gaia-agent.yaml",
    #: a skill-set name, …).
    origin: str = ""

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"

    def satisfied_by(self, skill: Skill) -> bool:
        """Whether *skill*'s version falls inside this requirement's range."""
        if self.version.strip() in ("", "*", "latest", "any"):
            return True
        if not skill.version:
            # An unversioned skill cannot prove it satisfies a pin. Treating it as
            # a match would let any local edit shadow a pinned hub install.
            return False
        return matches(skill.version, self.version)


def find_agent_manifest(module_file: Path) -> Optional[Path]:
    """Locate the ``gaia-agent.yaml`` belonging to the agent defined in *module_file*.

    Checks the module's own directory, then its parent. Those are the only two
    layouts GAIA ships: a hub package puts the manifest one level above the Python
    package (``hub/agents/<id>/python/gaia-agent.yaml`` beside
    ``gaia_agent_<id>/agent.py``), and a custom agent puts it next to its
    ``agent.py`` in ``~/.gaia/agents/<id>/``. The search stops there on purpose —
    walking further up would eventually claim an unrelated manifest from a
    site-packages sibling or the repo root.
    """
    module_dir = Path(module_file).resolve().parent
    for candidate in (module_dir, module_dir.parent):
        manifest = candidate / AGENT_MANIFEST_FILENAME
        if manifest.is_file():
            return manifest
    return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSkills:
    """The outcome of resolving a set of requirements against installed skills."""

    #: Load order: dependency-first, declaration order otherwise.
    order: list[Skill] = field(default_factory=list)
    #: Optional requirements that resolved to nothing (name → why).
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return [skill.name for skill in self.order]


def resolve_requirements(
    requirements: Sequence[SkillRequirement],
    *,
    manager: SkillManager,
) -> ResolvedSkills:
    """Resolve requirements against the skills installed on this machine.

    Resolution is local: ``gaia skill install`` puts a skill in a discovery root,
    and this reads what is there. It does not reach the network — an agent must
    not silently pull code at startup.

    A *required* entry that is missing or version-incompatible raises. An
    *optional* one is recorded in :attr:`ResolvedSkills.skipped` with the reason,
    so "the agent ran without it" is visible rather than invisible.

    Raises:
        SkillNotFoundError: a required skill is not installed.
        SkillValidationError: a required skill is installed at an incompatible
            version, or the requirements contain a dependency cycle.
    """
    installed = manager.discover()
    chosen: dict[str, Skill] = {}
    skipped: dict[str, str] = {}
    order: list[str] = []

    for requirement in requirements:
        skill = installed.get(requirement.name)
        if skill is None:
            reason = (
                f"skill '{requirement.name}' is not installed "
                f"(declared in {requirement.origin or 'the agent manifest'})"
            )
            if requirement.required:
                raise SkillNotFoundError(
                    f"Required {reason}. Install it with "
                    f"'gaia skill install {requirement}', or drop the entry from "
                    f"the manifest. Run 'gaia skill list' to see what is installed."
                )
            skipped[requirement.name] = reason
            log.info(
                "Optional skill '%s' is not installed — skipping", requirement.name
            )
            continue

        if not requirement.satisfied_by(skill):
            found = skill.version or "(unversioned)"
            reason = (
                f"skill '{requirement.name}' is installed at {found}, which does not "
                f"satisfy {requirement.version!r}"
            )
            if requirement.required:
                raise SkillValidationError(
                    f"Version conflict: {reason} (declared in "
                    f"{requirement.origin or 'the agent manifest'}, resolved from "
                    f"{skill.directory}). Install a matching version with "
                    f"'gaia skill install {requirement} --force', or loosen the pin. "
                    "GAIA will not load a version the manifest excluded."
                )
            skipped[requirement.name] = reason
            log.info(
                "Optional skill '%s' version mismatch — skipping (%s)",
                requirement.name,
                reason,
            )
            continue

        chosen[requirement.name] = skill
        order.append(requirement.name)

    ordered = _topological(order, chosen)
    return ResolvedSkills(order=[chosen[name] for name in ordered], skipped=skipped)


def _topological(order: Sequence[str], chosen: dict[str, Skill]) -> list[str]:
    """Order skills so a skill's in-set tool providers load before it.

    A skill's ``tools_required`` names registry tools it consumes. When another
    skill in the same set *provides* one (as ``<name>/<tool>`` or bare
    ``<tool>``), it must be registered first. Declaration order breaks all
    remaining ties, so the result is deterministic.

    Raises:
        SkillValidationError: on a dependency cycle.
    """
    providers: dict[str, str] = {}
    for name, skill in chosen.items():
        for tool in skill.tool_names:
            providers[f"{name}/{tool}"] = name
            providers.setdefault(tool, name)

    dependencies: dict[str, set[str]] = {name: set() for name in order}
    for name in order:
        for tool in chosen[name].gaia.tools_required:
            provider = providers.get(tool)
            if provider is not None and provider != name:
                dependencies[name].add(provider)

    resolved: list[str] = []
    state: dict[str, int] = {}  # 0 = visiting, 1 = done

    def visit(name: str, path: tuple[str, ...]) -> None:
        marker = state.get(name)
        if marker == 1:
            return
        if marker == 0:
            cycle = " -> ".join([*path, name])
            raise SkillValidationError(
                f"Circular skill dependency: {cycle}. Two skills each consume a tool "
                "the other provides, so there is no order that satisfies both. Break "
                "the cycle by moving the shared tool into its own skill, or by "
                "dropping one from the agent's 'skills:' block."
            )
        state[name] = 0
        for dependency in sorted(dependencies.get(name, ())):
            visit(dependency, (*path, name))
        state[name] = 1
        resolved.append(name)

    for name in order:
        visit(name, ())
    return resolved


def requirements_from_refs(
    refs: Iterable[Any], *, origin: str = ""
) -> list[SkillRequirement]:
    """Adapt any ``name``/``version``/``required`` objects into requirements.

    The seam ``gaia.skills.sets`` uses: hand it the refs a skill set resolved to
    and pass the result to :func:`resolve_requirements` for version matching,
    dependency ordering, and the required/optional split.

    Duck-typed on purpose (``.name`` / ``.version`` / ``.required``) so neither
    module has to import the other.
    """
    requirements: list[SkillRequirement] = []
    for ref in refs:
        name = getattr(ref, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise SkillValidationError(
                f"{origin or 'skill reference'}: expected an object with a non-empty "
                f"'name', got {ref!r}."
            )
        requirements.append(
            SkillRequirement(
                name=name.strip(),
                version=(getattr(ref, "version", None) or "*").strip() or "*",
                required=bool(getattr(ref, "required", True)),
                origin=origin,
            )
        )
    return requirements
