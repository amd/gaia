# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Declarative skill sets — the ``skills:`` / ``skill_sets:`` manifest grammar.

An agent declares two things in its ``gaia-agent.yaml``:

* ``skills:`` — always-on skills, loaded on every launch.
* ``skill_sets:`` — named, mutually-exclusive bundles. Exactly **one** is
  active per launch, chosen by :meth:`SkillSets.resolve`.

Example::

    skills:
      - mailbox-hygiene            # always on

    skill_sets:
      personal: [inbox-triage, newsletter-digest]
      work: [inbox-triage, meeting-scheduling]

    default_skill_set: personal

Selection order (:meth:`SkillSets.resolve`) is explicit request → agent-supplied
selector → ``default_skill_set``. An unknown name never falls back to the
default: it raises :class:`~gaia.skills.errors.SkillSetError` naming the valid
sets (GAIA's no-silent-fallbacks rule, ``CLAUDE.md``).

This module is pure data + validation — it neither reads the filesystem nor
loads a skill, so both the manifest validator (:mod:`gaia.hub.manifest`) and the
base :class:`~gaia.agents.base.agent.Agent` can share it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from gaia.skills.errors import DOCS_URL, SkillSetError, SkillValidationError

# Set names use the same slug shape as skill names so a set can be typed on a
# command line without quoting: lowercase alphanumeric with internal hyphens.
SET_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")

# Manifest keys this module owns. ``gaia.hub.manifest`` delegates all three.
SKILLS_KEY = "skills"
SKILL_SETS_KEY = "skill_sets"
DEFAULT_SET_KEY = "default_skill_set"

# Recognized keys in the mapping form of a skill reference.
_REF_KEYS = frozenset({"name", "version", "required"})

_SETS_DOCS_URL = f"{DOCS_URL}#skill-sets"

# Where a resolved set name came from. Surfaced in logs and asserted by tests so
# "which rule picked this set" is never guesswork.
SOURCE_EXPLICIT = "explicit"
SOURCE_SELECTOR = "selector"
SOURCE_DEFAULT = "default"
SOURCE_NONE = "none"


@dataclass(frozen=True)
class SkillRef:
    """One entry in a ``skills:`` list or a ``skill_sets:`` bundle.

    ``version`` is a **declaration surface only** in this phase: it is parsed,
    validated as a string, and surfaced, but no constraint solving happens until
    the marketplace phase (#2467) can install versioned skills. ``required:
    false`` marks an optional enhancement — a missing optional skill is logged
    and skipped, a missing required one fails the launch.
    """

    name: str
    version: Optional[str] = None
    required: bool = True


@dataclass(frozen=True)
class SkillSetResolution:
    """The outcome of :meth:`SkillSets.resolve`.

    ``name`` is the active set (``None`` when the agent declares no sets),
    ``skills`` is the always-on list followed by that set's list in declaration
    order, and ``source`` records which rule chose it (one of
    ``explicit``/``selector``/``default``/``none``).
    """

    name: Optional[str]
    skills: Tuple[SkillRef, ...]
    source: str


@dataclass(frozen=True)
class SkillSets:
    """A parsed, validated ``skills:`` + ``skill_sets:`` declaration.

    Build with :func:`parse_skill_sets`; the constructor does not validate.
    An agent that declares neither block gets the empty instance, which is
    falsy — so every existing agent keeps its current behaviour exactly.
    """

    always: Tuple[SkillRef, ...] = ()
    sets: Mapping[str, Tuple[SkillRef, ...]] = field(default_factory=dict)
    default_set: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(self.always or self.sets)

    @property
    def set_names(self) -> List[str]:
        """Declared set names, in manifest declaration order."""
        return list(self.sets)

    def skills_for(self, name: Optional[str]) -> Tuple[SkillRef, ...]:
        """Always-on skills plus the named set's skills, in declaration order.

        Raises:
            SkillSetError: *name* is not a declared set.
        """
        if name is None:
            return self.always
        if name not in self.sets:
            raise SkillSetError(self._unknown_set_message(name))
        return self.always + tuple(self.sets[name])

    def resolve(
        self,
        *,
        requested: Optional[str] = None,
        selected: Optional[str] = None,
    ) -> SkillSetResolution:
        """Pick the active set: *requested* → *selected* → ``default_skill_set``.

        Args:
            requested: An explicit choice (a ``--skill-set`` flag or config
                field). Highest precedence; an unknown value always raises.
            selected: The agent's selector-hook answer, used only when
                *requested* is ``None``. An unknown value raises too — a
                selector that computed a name this agent does not declare is a
                wiring bug, not a reason to guess.

        Raises:
            SkillSetError: an unknown set name, or a *requested* name on an
                agent that declares no sets.
        """
        requested = (requested or "").strip() or None
        selected = (selected or "").strip() or None

        if not self.sets:
            if requested:
                raise SkillSetError(
                    f"Skill set {requested!r} was requested but this agent "
                    "declares no 'skill_sets:' block, so there is nothing to "
                    "select. Drop the --skill-set argument, or add a "
                    f"'skill_sets:' block to its gaia-agent.yaml. "
                    f"See {_SETS_DOCS_URL}."
                )
            return SkillSetResolution(None, self.always, SOURCE_NONE)

        for candidate, source in (
            (requested, SOURCE_EXPLICIT),
            (selected, SOURCE_SELECTOR),
            (self.default_set, SOURCE_DEFAULT),
        ):
            if candidate is None:
                continue
            if candidate not in self.sets:
                raise SkillSetError(self._unknown_set_message(candidate, source))
            return SkillSetResolution(
                candidate, self.always + tuple(self.sets[candidate]), source
            )

        # Unreachable via parse_skill_sets (a non-empty skill_sets block requires
        # default_skill_set), but a hand-built SkillSets could get here.
        raise SkillSetError(
            "No skill set could be resolved: nothing was requested, the "
            "selector returned nothing, and no 'default_skill_set' is "
            f"declared. Declared sets: {', '.join(self.set_names)}. Set "
            f"'{DEFAULT_SET_KEY}:' in gaia-agent.yaml. See {_SETS_DOCS_URL}."
        )

    def _unknown_set_message(self, name: str, source: str = SOURCE_EXPLICIT) -> str:
        valid = ", ".join(self.set_names) or "(none)"
        origin = {
            SOURCE_EXPLICIT: "Skill set",
            SOURCE_SELECTOR: "The agent's skill-set selector returned skill set",
            SOURCE_DEFAULT: f"'{DEFAULT_SET_KEY}' names skill set",
        }.get(source, "Skill set")
        return (
            f"{origin} {name!r} is not declared by this agent. Valid sets: "
            f"{valid}. Pass one of those names, or add {name!r} to the "
            f"'{SKILL_SETS_KEY}:' block in gaia-agent.yaml. "
            f"See {_SETS_DOCS_URL}."
        )


def parse_skill_sets(data: Any, *, where: str = "") -> SkillSets:
    """Parse and validate the ``skills:`` / ``skill_sets:`` blocks of a manifest.

    Args:
        data: The full manifest mapping (already YAML-loaded). Keys other than
            ``skills``, ``skill_sets``, and ``default_skill_set`` are ignored.
        where: Location suffix for error messages, e.g. ``" in /path/to.yaml"``.

    Returns:
        A validated :class:`SkillSets`. Empty (and falsy) when the manifest
        declares neither block.

    Raises:
        SkillValidationError: any malformed entry. Nothing is partially
            accepted — a manifest either declares a coherent set of skills or
            fails to load.
    """
    if data is None:
        return SkillSets()
    if not isinstance(data, Mapping):
        raise SkillValidationError(
            f"Cannot read skill declarations{where}: expected a mapping, got "
            f"{type(data).__name__}. See {_SETS_DOCS_URL}."
        )

    always = _parse_ref_list(data.get(SKILLS_KEY), SKILLS_KEY, where)
    always_names = {ref.name for ref in always}

    raw_sets = data.get(SKILL_SETS_KEY)
    sets: Dict[str, Tuple[SkillRef, ...]] = {}
    if raw_sets is not None:
        if not isinstance(raw_sets, Mapping):
            raise SkillValidationError(
                f"'{SKILL_SETS_KEY}:'{where} must be a mapping of set name → "
                f"list of skills, got {type(raw_sets).__name__}. "
                f"See {_SETS_DOCS_URL}."
            )
        for set_name, raw_list in raw_sets.items():
            if not isinstance(set_name, str) or not SET_NAME_PATTERN.match(set_name):
                raise SkillValidationError(
                    f"'{SKILL_SETS_KEY}:'{where} has invalid set name "
                    f"{set_name!r}. Use lowercase letters, digits, and internal "
                    f"hyphens (1–32 chars), e.g. 'work'. See {_SETS_DOCS_URL}."
                )
            refs = _parse_ref_list(
                raw_list, f"{SKILL_SETS_KEY}.{set_name}", where, allow_empty=False
            )
            clash = sorted(ref.name for ref in refs if ref.name in always_names)
            if clash:
                raise SkillValidationError(
                    f"'{SKILL_SETS_KEY}.{set_name}'{where} re-declares skill(s) "
                    f"already in the always-on '{SKILLS_KEY}:' list: "
                    f"{', '.join(clash)}. An always-on skill loads for every "
                    f"set — remove it from the set, or from '{SKILLS_KEY}:' if "
                    f"it is set-specific. See {_SETS_DOCS_URL}."
                )
            sets[set_name] = refs

    default_set = data.get(DEFAULT_SET_KEY)
    if default_set is not None and not isinstance(default_set, str):
        raise SkillValidationError(
            f"'{DEFAULT_SET_KEY}:'{where} must be a string naming one of the "
            f"declared skill sets, got {type(default_set).__name__}. "
            f"See {_SETS_DOCS_URL}."
        )
    default_set = (default_set or "").strip() or None

    if sets and default_set is None:
        raise SkillValidationError(
            f"'{SKILL_SETS_KEY}:'{where} declares set(s) "
            f"{', '.join(sets)} but no '{DEFAULT_SET_KEY}:'. Add "
            f"'{DEFAULT_SET_KEY}: <name>' so a launch that selects nothing "
            f"still resolves a set explicitly. See {_SETS_DOCS_URL}."
        )
    if default_set is not None and default_set not in sets:
        valid = ", ".join(sets) or "(none declared)"
        raise SkillValidationError(
            f"'{DEFAULT_SET_KEY}: {default_set}'{where} does not name a "
            f"declared skill set. Valid sets: {valid}. See {_SETS_DOCS_URL}."
        )

    return SkillSets(always=always, sets=sets, default_set=default_set)


def _parse_ref_list(
    raw: Any, field_name: str, where: str, *, allow_empty: bool = True
) -> Tuple[SkillRef, ...]:
    """Parse a list of skill references (plain strings and/or mappings)."""
    # A bare ``work:`` key with nothing indented under it parses as None. For a
    # set that is the same authoring mistake as an empty list — checked before
    # the None shortcut, or the emptiness error below is unreachable.
    if raw is None and allow_empty:
        return ()
    if raw is None:
        raise SkillValidationError(
            f"'{field_name}:'{where} names no skills. A skill set must list at "
            f"least one skill (a bare '{field_name.split('.')[-1]}:' key with "
            f"nothing under it parses as empty), or be removed. "
            f"See {_SETS_DOCS_URL}."
        )
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise SkillValidationError(
            f"'{field_name}:'{where} must be a list of skill names (or "
            f"mappings with 'name'), got {type(raw).__name__}. "
            f"See {_SETS_DOCS_URL}."
        )
    if not raw and not allow_empty:
        raise SkillValidationError(
            f"'{field_name}:'{where} is empty. A skill set must name at least "
            f"one skill, or be removed. See {_SETS_DOCS_URL}."
        )

    refs: List[SkillRef] = []
    seen: Dict[str, int] = {}
    for index, entry in enumerate(raw):
        ref = _parse_ref(entry, f"{field_name}[{index}]", where)
        if ref.name in seen:
            raise SkillValidationError(
                f"'{field_name}:'{where} lists skill {ref.name!r} twice "
                f"(entries {seen[ref.name]} and {index}). Remove the "
                f"duplicate. See {_SETS_DOCS_URL}."
            )
        seen[ref.name] = index
        refs.append(ref)
    return tuple(refs)


def _parse_ref(entry: Any, field_name: str, where: str) -> SkillRef:
    """Parse one reference: ``"name"`` or ``{name, version?, required?}``."""
    if isinstance(entry, str):
        return SkillRef(name=_validated_name(entry, field_name, where))

    if not isinstance(entry, Mapping):
        raise SkillValidationError(
            f"'{field_name}'{where} must be a skill name or a mapping with a "
            f"'name' key, got {type(entry).__name__}. See {_SETS_DOCS_URL}."
        )

    unknown = sorted(set(entry) - _REF_KEYS)
    if unknown:
        raise SkillValidationError(
            f"'{field_name}'{where} has unrecognized key(s): "
            f"{', '.join(str(k) for k in unknown)}. Valid keys: "
            f"{', '.join(sorted(_REF_KEYS))}. See {_SETS_DOCS_URL}."
        )

    name = _validated_name(entry.get("name"), field_name, where)

    version = entry.get("version")
    if version is not None and not (isinstance(version, str) and version.strip()):
        raise SkillValidationError(
            f"'{field_name}.version'{where} must be a non-empty string (a "
            f"version or range, e.g. '>=1.0.0'), got {version!r}. "
            f"See {_SETS_DOCS_URL}."
        )

    required = entry.get("required", True)
    if not isinstance(required, bool):
        raise SkillValidationError(
            f"'{field_name}.required'{where} must be true or false, got "
            f"{required!r}. See {_SETS_DOCS_URL}."
        )

    return SkillRef(
        name=name,
        version=version.strip() if isinstance(version, str) else None,
        required=required,
    )


def _validated_name(value: Any, field_name: str, where: str) -> str:
    from gaia.skills.format import NAME_PATTERN

    if not isinstance(value, str) or not value.strip():
        raise SkillValidationError(
            f"'{field_name}'{where} is missing a skill name. Every entry needs "
            f"a non-empty 'name'. See {_SETS_DOCS_URL}."
        )
    name = value.strip()
    if not NAME_PATTERN.match(name):
        raise SkillValidationError(
            f"'{field_name}'{where} names skill {name!r}, which is not a valid "
            "skill name. Use lowercase letters, digits, and internal hyphens "
            f"(e.g. 'inbox-triage'). See {_SETS_DOCS_URL}."
        )
    return name


__all__ = [
    "SkillRef",
    "SkillSets",
    "SkillSetResolution",
    "parse_skill_sets",
    "SET_NAME_PATTERN",
    "SKILLS_KEY",
    "SKILL_SETS_KEY",
    "DEFAULT_SET_KEY",
    "SOURCE_EXPLICIT",
    "SOURCE_SELECTOR",
    "SOURCE_DEFAULT",
    "SOURCE_NONE",
]
