# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Declarative skill consumption — the ``skills:`` block of ``gaia-agent.yaml``.

Scope D of #2467: an agent *declares* the skills it composes instead of calling
``load_skill`` from Python, and an installed hub skill is referenced by
``name@version`` exactly the way agent-to-agent ``dependencies:`` are resolved —
highest version satisfying the pin, deterministic order, fail loud on conflict::

    skills:
      - name: web-research
        version: ">=1.0.0"
        required: true
      - name: incident-review
        version: ">=0.1.0"
        required: false      # optional enhancement; the agent runs without it

**Why this is not per-agent code.** :meth:`gaia.agents.base.Agent.load_declared_skills`
runs for every ``Agent`` subclass, bundled or not, and finds the manifest beside
the agent's own module. A custom harness under ``~/.gaia/agents/<id>/`` therefore
gets the same consumption path as a hub package by shipping a ``gaia-agent.yaml``
next to its ``agent.py`` — no subclass hook, no registration call.

**Ordering.** Declaration order is preserved, then a skill that another skill's
``tools_required`` depends on is loaded first. A cycle raises instead of picking a
winner: a "topological order" that silently breaks ties is not an order.

**``skill_sets:`` belongs to #2466.** That issue owns the set grammar and its
expansion. It plugs in here by producing :class:`SkillRequirement` values and
handing them to :func:`resolve_requirements`, so there is exactly one resolution
path and one place that enforces conflicts. Do not add a second grammar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from gaia.logger import get_logger
from gaia.skills.errors import (
    DOCS_URL,
    SkillNotFoundError,
    SkillValidationError,
)
from gaia.skills.format import Skill
from gaia.skills.manager import SkillManager
from gaia.skills.versions import matches

log = get_logger(__name__)

#: Manifest filename an agent package ships (the hub's agent manifest).
AGENT_MANIFEST_FILENAME = "gaia-agent.yaml"

#: The manifest key this module reads.
SKILLS_KEY = "skills"


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


def parse_requirements(
    raw: Any, *, where: str, origin: str = AGENT_MANIFEST_FILENAME
) -> list[SkillRequirement]:
    """Parse a manifest ``skills:`` block into requirements.

    Accepts the mapping form from the spec and the shorthand string form
    (``- web-research`` / ``- web-research@^1.0``), which is what a hand-typed
    manifest tends to contain.

    Raises:
        SkillValidationError: for any malformed entry. A skills block GAIA cannot
            read is an error — skipping it would silently drop capability the
            agent's author declared.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SkillValidationError(
            f"{where}: '{SKILLS_KEY}' must be a list of skill entries, got "
            f"{type(raw).__name__}. Each entry is either 'name' / 'name@range' or a "
            "mapping with 'name', optional 'version', and optional 'required'. "
            f"See {DOCS_URL}"
        )

    requirements: list[SkillRequirement] = []
    seen: dict[str, SkillRequirement] = {}
    for index, entry in enumerate(raw):
        requirement = _parse_entry(entry, where=where, index=index, origin=origin)
        previous = seen.get(requirement.name)
        if previous is not None:
            if previous.version != requirement.version:
                raise SkillValidationError(
                    f"{where}: '{SKILLS_KEY}' declares skill "
                    f"'{requirement.name}' twice with conflicting version ranges "
                    f"({previous.version!r} and {requirement.version!r}). Pick one "
                    "range — GAIA will not guess which pin you meant."
                )
            log.debug(
                "%s: skill '%s' declared twice with the same range; keeping one",
                where,
                requirement.name,
            )
            continue
        seen[requirement.name] = requirement
        requirements.append(requirement)
    return requirements


def _parse_entry(
    entry: Any, *, where: str, index: int, origin: str
) -> SkillRequirement:
    location = f"{where}: {SKILLS_KEY}[{index}]"

    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            raise SkillValidationError(
                f"{location} is an empty string. Name the skill, e.g. "
                "'web-research' or 'web-research@^1.0'."
            )
        name, _, version = text.partition("@")
        return SkillRequirement(
            name=name.strip(), version=(version.strip() or "*"), origin=origin
        )

    if not isinstance(entry, dict):
        raise SkillValidationError(
            f"{location} must be a string ('name' / 'name@range') or a mapping with "
            f"a 'name' key, got {type(entry).__name__}."
        )

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SkillValidationError(
            f"{location} is missing a non-empty 'name'. Every skill entry names the "
            "skill it composes."
        )

    version = entry.get("version", "*")
    if version is None:
        version = "*"
    if not isinstance(version, str):
        raise SkillValidationError(
            f"{location}: 'version' must be a SemVer range string such as "
            f"'>=1.0.0' or '^1.2', got {type(version).__name__}."
        )

    required = entry.get("required", True)
    if not isinstance(required, bool):
        raise SkillValidationError(
            f"{location}: 'required' must be true or false, got "
            f"{type(required).__name__}."
        )

    return SkillRequirement(
        name=name.strip(), version=version.strip() or "*", required=required, origin=origin
    )


def load_manifest_requirements(
    manifest_path: Path, *, origin: Optional[str] = None
) -> list[SkillRequirement]:
    """Read the ``skills:`` block out of a ``gaia-agent.yaml``.

    A manifest with no ``skills:`` key yields ``[]`` — declaring none is the
    normal case, not a problem.
    """
    import yaml

    path = Path(manifest_path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SkillValidationError(
            f"Could not read the agent manifest at {path}: {exc}. Fix the YAML — an "
            "unreadable manifest may be hiding a 'skills:' block, so GAIA will not "
            "assume the agent declares none."
        ) from exc

    if data is None:
        return []
    if not isinstance(data, dict):
        raise SkillValidationError(
            f"{path} must contain a YAML mapping at its top level, got "
            f"{type(data).__name__}."
        )
    return parse_requirements(
        data.get(SKILLS_KEY), where=str(path), origin=origin or str(path)
    )


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
            log.info("Optional skill '%s' is not installed — skipping", requirement.name)
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
            log.info("Optional skill '%s' version mismatch — skipping (%s)", requirement.name, reason)
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


def resolve_manifest(
    manifest_path: Path, *, manager: SkillManager
) -> ResolvedSkills:
    """Read a ``gaia-agent.yaml``'s ``skills:`` block and resolve it."""
    return resolve_requirements(
        load_manifest_requirements(manifest_path), manager=manager
    )


def requirements_from_names(names: Iterable[str], *, origin: str = "") -> list[SkillRequirement]:
    """Build requirements from ``name`` / ``name@range`` strings.

    The entry point for callers holding a flat list — notably #2466's
    ``skill_sets:`` expansion, which turns a set into the skills it contains and
    then resolves them through :func:`resolve_requirements` like any other block.
    """
    return [
        _parse_entry(name, where=origin or "<names>", index=i, origin=origin)
        for i, name in enumerate(names)
    ]
