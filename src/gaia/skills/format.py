# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``SKILL.md`` parser, writer, and validator.

The on-disk contract is the `Agent Skills <https://agentskills.io>`_ base
(``name``, ``description``, optional ``license`` / ``metadata``) plus GAIA's
two additions: a top-level ``version`` and everything else nested under
``metadata.gaia``. A bare standard skill — only ``name`` and ``description`` —
loads as a valid instruction-only skill with the most conservative defaults
(``security_tier: experimental``, no tools, no permissions).

Round-trip is identity: ``parse(write(parse(text)))`` equals ``parse(text)``.
Unknown top-level keys, other ``metadata.<vendor>`` namespaces, and unknown
``metadata.gaia`` keys are all preserved so nothing is lost by passing a
foreign skill through GAIA.

The standard's ``compatibility`` and ``allowed-tools`` keys parse but are
**ignored** — they overlap ``metadata.gaia`` and are not a permission
mechanism. See ``docs/plans/skill-format.mdx``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from gaia.logger import get_logger
from gaia.skills.errors import FORMAT_DOCS_URL, SkillValidationError
from gaia.skills.permissions import Permission, parse_permissions

log = get_logger(__name__)

#: The one required file in a skill directory.
SKILL_FILENAME = "SKILL.md"

#: Optional module providing the skill's own ``@tool`` functions.
SKILL_TOOLS_FILENAME = "tools.py"

SECURITY_TIERS: tuple[str, ...] = ("verified", "community", "experimental")
DEFAULT_SECURITY_TIER = "experimental"

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Official SemVer 2.0.0 pattern (semver.org).
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

#: Standard keys GAIA parses but deliberately ignores (see module docstring).
IGNORED_STANDARD_KEYS = ("compatibility", "allowed-tools", "disallowed-tools")

_FRONTMATTER_RE = re.compile(
    r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL
)


@dataclass
class SkillRequirements:
    """``metadata.gaia.requirements`` — all advisory in Phase 1."""

    model: Optional[str] = None
    context: Optional[str] = None
    python: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)
    node_dependencies: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    hardware: dict[str, Any] = field(default_factory=dict)
    #: Requirement keys GAIA does not model, preserved verbatim for round-trip.
    extra: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """True when no constraint is declared (the omitted-default state)."""
        return self == SkillRequirements()

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to the frontmatter shape, omitting empty fields."""
        out: dict[str, Any] = {}
        for key in ("model", "context", "python"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        for key in ("dependencies", "node_dependencies", "env_vars"):
            value = getattr(self, key)
            if value:
                out[key] = list(value)
        if self.hardware:
            out["hardware"] = dict(self.hardware)
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: Any, *, skill_name: str) -> "SkillRequirements":
        """Build from the frontmatter mapping, failing loudly on a bad shape."""
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise SkillValidationError(
                f"Skill '{skill_name}': metadata.gaia.requirements must be a mapping, "
                f"got {type(data).__name__}. Example: "
                "'requirements: {python: \">=3.10\"}'. "
                f"See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
            )

        known = {
            "model",
            "context",
            "python",
            "dependencies",
            "node_dependencies",
            "env_vars",
            "hardware",
        }
        for key in ("dependencies", "node_dependencies", "env_vars"):
            value = data.get(key)
            if value is not None and not isinstance(value, list):
                raise SkillValidationError(
                    f"Skill '{skill_name}': metadata.gaia.requirements.{key} must be a "
                    f"list, got {type(value).__name__}. "
                    f"See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
                )
        hardware = data.get("hardware") or {}
        if not isinstance(hardware, dict):
            raise SkillValidationError(
                f"Skill '{skill_name}': metadata.gaia.requirements.hardware must be a "
                f"mapping, got {type(hardware).__name__}. "
                f"See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
            )

        return cls(
            model=data.get("model"),
            context=data.get("context"),
            python=data.get("python"),
            dependencies=list(data.get("dependencies") or []),
            node_dependencies=list(data.get("node_dependencies") or []),
            env_vars=list(data.get("env_vars") or []),
            hardware=dict(hardware),
            extra={k: v for k, v in data.items() if k not in known},
        )


@dataclass
class SkillTool:
    """One entry of ``metadata.gaia.tools`` — a tool the skill *provides*.

    Distinct from ``tools_required``, which names registry tools the skill
    *consumes*. Never conflate the two.
    """

    name: str
    description: str = ""
    #: ``{param_name: {"type": ..., "required": ..., "default": ...}}``
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    returns: Optional[dict[str, Any]] = None
    atomic: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to the frontmatter shape."""
        out: dict[str, Any] = {"name": self.name}
        if self.description:
            out["description"] = self.description
        out["parameters"] = {k: dict(v) for k, v in self.parameters.items()}
        if self.returns is not None:
            out["returns"] = dict(self.returns)
        if self.atomic:
            out["atomic"] = self.atomic
        return out

    @classmethod
    def from_dict(cls, data: Any, *, skill_name: str) -> "SkillTool":
        """Build from a frontmatter entry, failing loudly on a bad shape."""
        if not isinstance(data, dict):
            raise SkillValidationError(
                f"Skill '{skill_name}': each metadata.gaia.tools entry must be a "
                f"mapping with a 'name', got {type(data).__name__}. "
                f"See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
            )
        name = data.get("name")
        if not name or not isinstance(name, str):
            raise SkillValidationError(
                f"Skill '{skill_name}': a metadata.gaia.tools entry is missing its "
                "'name'. Every declared tool must name the @tool function in "
                f"{SKILL_TOOLS_FILENAME} that implements it. "
                f"See {FORMAT_DOCS_URL}#tool-registration"
            )

        raw_params = data.get("parameters") or {}
        if not isinstance(raw_params, dict):
            raise SkillValidationError(
                f"Skill '{skill_name}': tool '{name}' has 'parameters' of type "
                f"{type(raw_params).__name__}; it must be a mapping of "
                "parameter name to {type, required, default}. "
                f"See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
            )
        parameters: dict[str, dict[str, Any]] = {}
        for param_name, spec in raw_params.items():
            if not isinstance(spec, dict):
                raise SkillValidationError(
                    f"Skill '{skill_name}': tool '{name}' parameter "
                    f"'{param_name}' must be a mapping like "
                    "'{type: string, required: true}', got "
                    f"{type(spec).__name__}. "
                    f"See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
                )
            parameters[param_name] = dict(spec)

        returns = data.get("returns")
        if returns is not None and not isinstance(returns, dict):
            raise SkillValidationError(
                f"Skill '{skill_name}': tool '{name}' has 'returns' of type "
                f"{type(returns).__name__}; it must be a mapping like "
                f"'{{type: object}}'. See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
            )

        return cls(
            name=name,
            description=data.get("description", "") or "",
            parameters=parameters,
            returns=dict(returns) if returns is not None else None,
            atomic=bool(data.get("atomic", False)),
        )


@dataclass
class GaiaMetadata:
    """The ``metadata.gaia`` namespace. Omit it entirely for a bare skill."""

    security_tier: str = DEFAULT_SECURITY_TIER
    permissions: list[str] = field(default_factory=list)
    requirements: SkillRequirements = field(default_factory=SkillRequirements)
    tools: list[SkillTool] = field(default_factory=list)
    tools_required: list[str] = field(default_factory=list)
    #: ``metadata.gaia`` keys GAIA does not model, preserved for round-trip.
    extra: dict[str, Any] = field(default_factory=dict)

    def is_default(self) -> bool:
        """True when every field holds its omitted-default value."""
        return self == GaiaMetadata()

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to the frontmatter shape, omitting defaults."""
        out: dict[str, Any] = {}
        if self.security_tier != DEFAULT_SECURITY_TIER:
            out["security_tier"] = self.security_tier
        if self.permissions:
            out["permissions"] = list(self.permissions)
        if not self.requirements.is_empty():
            out["requirements"] = self.requirements.to_dict()
        if self.tools:
            out["tools"] = [t.to_dict() for t in self.tools]
        if self.tools_required:
            out["tools_required"] = list(self.tools_required)
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: Any, *, skill_name: str) -> "GaiaMetadata":
        """Build from the ``metadata.gaia`` mapping, failing loudly on a bad shape."""
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise SkillValidationError(
                f"Skill '{skill_name}': metadata.gaia must be a mapping, got "
                f"{type(data).__name__}. Omit it entirely for an instruction-only "
                f"skill. See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
            )

        tier = data.get("security_tier", DEFAULT_SECURITY_TIER)
        if tier not in SECURITY_TIERS:
            raise SkillValidationError(
                f"Skill '{skill_name}': metadata.gaia.security_tier is {tier!r}, which "
                f"is not one of {', '.join(SECURITY_TIERS)}. Omit the field to take "
                f"the safe default ('{DEFAULT_SECURITY_TIER}'). "
                f"See {FORMAT_DOCS_URL}#security-tiers"
            )

        permissions = data.get("permissions") or []
        if not isinstance(permissions, list):
            raise SkillValidationError(
                f"Skill '{skill_name}': metadata.gaia.permissions must be a list of "
                f"'<domain>:<level>[:scope]' strings, got {type(permissions).__name__}. "
                f"See {FORMAT_DOCS_URL}#permission-model"
            )

        tools = data.get("tools") or []
        if not isinstance(tools, list):
            raise SkillValidationError(
                f"Skill '{skill_name}': metadata.gaia.tools must be a list, got "
                f"{type(tools).__name__}. Each entry declares one @tool function this "
                f"skill provides. See {FORMAT_DOCS_URL}#tools-vs-tools_required"
            )

        tools_required = data.get("tools_required") or []
        if not isinstance(tools_required, list):
            raise SkillValidationError(
                f"Skill '{skill_name}': metadata.gaia.tools_required must be a list of "
                f"registry tool names, got {type(tools_required).__name__}. "
                f"See {FORMAT_DOCS_URL}#tools-vs-tools_required"
            )

        known = {
            "security_tier",
            "permissions",
            "requirements",
            "tools",
            "tools_required",
        }
        return cls(
            security_tier=tier,
            permissions=[str(p) for p in permissions],
            requirements=SkillRequirements.from_dict(
                data.get("requirements"), skill_name=skill_name
            ),
            tools=[SkillTool.from_dict(t, skill_name=skill_name) for t in tools],
            tools_required=[str(t) for t in tools_required],
            extra={k: v for k, v in data.items() if k not in known},
        )


@dataclass
class Skill:
    """A parsed ``SKILL.md``: frontmatter + Markdown body.

    Location fields (``path``, ``root``, ``read_only``) describe *where* the
    skill came from, not *what* it is, so they are excluded from equality —
    that is what makes round-trip identity meaningful across directories.
    """

    name: str
    description: str
    body: str = ""
    license: Optional[str] = None
    version: Optional[str] = None
    gaia: GaiaMetadata = field(default_factory=GaiaMetadata)
    #: Other ``metadata.<vendor>`` namespaces (hermes, openclaw, …), preserved.
    other_metadata: dict[str, Any] = field(default_factory=dict)
    #: Top-level keys GAIA does not model — including the deliberately ignored
    #: ``compatibility`` / ``allowed-tools`` — preserved for round-trip.
    extra_fields: dict[str, Any] = field(default_factory=dict)

    # --- provenance (never part of equality) ---
    path: Optional[Path] = field(default=None, compare=False, repr=False)
    root: Optional[str] = field(default=None, compare=False, repr=False)
    read_only: bool = field(default=False, compare=False, repr=False)

    # ------------------------------------------------------------------
    # Derived views
    # ------------------------------------------------------------------

    @property
    def directory(self) -> Optional[Path]:
        """The skill's directory, or ``None`` when parsed from a string."""
        return self.path.parent if self.path else None

    @property
    def security_tier(self) -> str:
        """Install-time trust tier; ``experimental`` unless declared."""
        return self.gaia.security_tier

    @property
    def is_instruction_only(self) -> bool:
        """True when the skill ships instructions and no tools of its own."""
        return not self.gaia.tools

    @property
    def tool_names(self) -> list[str]:
        """Unqualified names of the tools this skill provides."""
        return [t.name for t in self.gaia.tools]

    def namespaced_tool_name(self, tool_name: str) -> str:
        """Return ``<skill-name>/<tool>`` — the registry key used on load."""
        return f"{self.name}/{tool_name}"

    @property
    def tools_path(self) -> Optional[Path]:
        """Path to the skill's ``tools.py``, if the skill is on disk."""
        directory = self.directory
        return directory / SKILL_TOOLS_FILENAME if directory else None

    def parsed_permissions(self) -> list[Permission]:
        """Parse the declared permission strings (fails loudly on bad grammar)."""
        return parse_permissions(self.gaia.permissions, skill_name=self.name)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_frontmatter(self) -> dict[str, Any]:
        """Build the frontmatter mapping in canonical key order."""
        out: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.license is not None:
            out["license"] = self.license
        if self.version is not None:
            out["version"] = self.version

        metadata: dict[str, Any] = {}
        gaia_block = self.gaia.to_dict()
        if gaia_block or not self.gaia.is_default():
            metadata["gaia"] = gaia_block
        metadata.update(self.other_metadata)
        if metadata:
            out["metadata"] = metadata

        out.update(self.extra_fields)
        return out

    def to_markdown(self) -> str:
        """Render the skill back to ``SKILL.md`` text."""
        frontmatter = yaml.safe_dump(
            self.to_frontmatter(),
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
        body = self.body.strip("\n")
        return (
            f"---\n{frontmatter}---\n\n{body}\n" if body else f"---\n{frontmatter}---\n"
        )

    def write(self, path: Optional[Path] = None) -> Path:
        """Write the skill to ``path`` (default: its own ``path``)."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError(
                "Skill.write() needs a destination: this Skill was parsed from a "
                "string, so it has no path of its own. Pass path=<dir>/SKILL.md."
            )
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_markdown(), encoding="utf-8")
        return target


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------


def split_frontmatter(text: str, *, source: str = "<string>") -> tuple[dict, str]:
    """Split ``SKILL.md`` text into its frontmatter mapping and Markdown body.

    The structural half of :func:`parse_skill` — it proves the file *is* a
    frontmatter document without asserting anything about GAIA's schema. That
    split is what lets :mod:`gaia.skills.migrate` read a foreign skill whose
    ``name`` or ``version`` GAIA would reject; the migrated output still goes
    through :func:`parse_skill` before it is written.

    Raises:
        SkillValidationError: if the frontmatter is missing or is not a mapping.
    """
    if not isinstance(text, str):
        raise SkillValidationError(
            f"{source}: expected SKILL.md text, got {type(text).__name__}."
        )

    stripped = text.lstrip("﻿")
    match = _FRONTMATTER_RE.match(stripped)
    if not match:
        raise SkillValidationError(
            f"{source}: no YAML frontmatter found. A SKILL.md must open with a '---' "
            "line, the YAML fields, and a closing '---' line, e.g.:\n"
            "  ---\n  name: my-skill\n  description: What it does and when to use it.\n"
            f"  ---\nSee {FORMAT_DOCS_URL}#adopted-base-agent-skills-standard"
        )

    body = stripped[match.end() :]

    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillValidationError(
            f"{source}: the YAML frontmatter is invalid: {exc}. Fix the YAML syntax "
            f"(watch for tabs and unquoted ':'). See {FORMAT_DOCS_URL}"
        ) from exc

    if frontmatter is None:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        raise SkillValidationError(
            f"{source}: the frontmatter must be a YAML mapping of fields, got "
            f"{type(frontmatter).__name__}. See {FORMAT_DOCS_URL}"
        )

    return frontmatter, body


def reset_security_tier(skill: Skill) -> str:
    """Stamp the safe default tier on a skill, returning the tier it replaced.

    The one trust-reset shared by every path that brings a skill in from outside
    (``gaia skill import``, ``gaia skill migrate``): a tier claimed somewhere
    else carries no weight here, so it is re-earned rather than believed.
    """
    previous = skill.gaia.security_tier
    skill.gaia.security_tier = DEFAULT_SECURITY_TIER
    return previous


def parse_skill(text: str, *, source: str = "<string>") -> Skill:
    """Parse ``SKILL.md`` text into a :class:`Skill`.

    Args:
        text: The full file contents (frontmatter + Markdown body).
        source: Where the text came from, quoted in error messages.

    Raises:
        SkillValidationError: on missing/malformed frontmatter or a field that
            violates the schema. Nothing partial is ever returned.
    """
    frontmatter, body = split_frontmatter(text, source=source)

    name = frontmatter.get("name")
    if not name or not isinstance(name, str):
        raise SkillValidationError(
            f"{source}: required field 'name' is missing or not a string. Add "
            "'name: <skill-directory-name>' to the frontmatter. "
            f"See {FORMAT_DOCS_URL}#naming"
        )

    description = frontmatter.get("description")
    if not description or not isinstance(description, str):
        raise SkillValidationError(
            f"{source}: required field 'description' is missing or not a string. It is "
            "the trigger signal the model reads to decide relevance — say what the "
            "skill does and when to use it. "
            f"See {FORMAT_DOCS_URL}#adopted-base-agent-skills-standard"
        )

    for key in ("license", "version"):
        value = frontmatter.get(key)
        if value is not None and not isinstance(value, str):
            raise SkillValidationError(
                f"{source}: field '{key}' must be a string, got "
                f"{type(value).__name__}. Quote it if it looks numeric "
                f'(e.g. version: "1.0.0"). See {FORMAT_DOCS_URL}'
            )

    raw_metadata = frontmatter.get("metadata") or {}
    if not isinstance(raw_metadata, dict):
        raise SkillValidationError(
            f"{source}: field 'metadata' must be a mapping of vendor namespaces "
            f"(e.g. 'metadata: {{gaia: {{...}}}}'), got {type(raw_metadata).__name__}. "
            f"See {FORMAT_DOCS_URL}#the-metadatagaia-namespace"
        )

    present_ignored = [k for k in IGNORED_STANDARD_KEYS if k in frontmatter]
    if present_ignored:
        log.debug(
            "%s: ignoring standard key(s) %s — they are not part of GAIA's adopted "
            "base and are never a permission mechanism; permissions come from "
            "metadata.gaia.permissions.",
            source,
            ", ".join(present_ignored),
        )

    known_top_level = {"name", "description", "license", "version", "metadata"}
    skill = Skill(
        name=name,
        description=description,
        body=body.strip("\n"),
        license=frontmatter.get("license"),
        version=frontmatter.get("version"),
        gaia=GaiaMetadata.from_dict(raw_metadata.get("gaia"), skill_name=name),
        other_metadata={k: v for k, v in raw_metadata.items() if k != "gaia"},
        extra_fields={k: v for k, v in frontmatter.items() if k not in known_top_level},
    )

    validate_skill(skill, source=source)
    return skill


def parse_skill_file(
    path: Path | str,
    *,
    root: Optional[str] = None,
    read_only: bool = False,
    check_directory_name: bool = True,
) -> Skill:
    """Parse a ``SKILL.md`` from disk.

    Args:
        path: Either the ``SKILL.md`` file or the directory containing it.
        root: Label of the discovery root this skill came from.
        read_only: True for imported roots (``.claude/skills/``).
        check_directory_name: Enforce ``name`` == directory name.

    Raises:
        SkillValidationError: if the file is missing or fails validation.
    """
    path = Path(path)
    skill_file = path / SKILL_FILENAME if path.is_dir() else path

    if not skill_file.is_file():
        raise SkillValidationError(
            f"No {SKILL_FILENAME} at {skill_file}. A skill is a directory whose only "
            f"required file is {SKILL_FILENAME}. Create one with "
            f"'gaia skill create <name>'. See {FORMAT_DOCS_URL}"
        )

    try:
        text = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillValidationError(
            f"Could not read {skill_file}: {exc}. Check the file's permissions and "
            "that it is UTF-8 encoded."
        ) from exc

    skill = parse_skill(text, source=str(skill_file))
    skill.path = skill_file
    skill.root = root
    skill.read_only = read_only

    if check_directory_name:
        directory_name = skill_file.parent.name
        if skill.name != directory_name:
            raise SkillValidationError(
                f"{skill_file}: frontmatter says name: {skill.name!r} but the "
                f"directory is named {directory_name!r}. The two must match — rename "
                f"the directory to '{skill.name}' or change the frontmatter to "
                f"'name: {directory_name}'. See {FORMAT_DOCS_URL}#naming"
            )

    return skill


def parse_skill_metadata(
    path: Path | str,
    *,
    root: Optional[str] = None,
    read_only: bool = False,
) -> Skill:
    """Parse only the frontmatter of a ``SKILL.md``, dropping the body.

    Level 1 of progressive disclosure: discovery keeps every skill's metadata
    resident but never pays for its instructions until the skill triggers.
    """
    path = Path(path)
    skill_file = path / SKILL_FILENAME if path.is_dir() else path
    skill = parse_skill_file(skill_file, root=root, read_only=read_only)
    skill.body = ""
    return skill


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def validate_skill(skill: Skill, *, source: str = "<skill>") -> None:
    """Validate a parsed skill's fields. Raises on the first violation.

    Checks the schema-level invariants only — the ``tools`` ↔ ``tools.py``
    cross-check needs the module and lives in :mod:`gaia.skills.loader`.
    """
    if len(skill.name) > MAX_NAME_LENGTH:
        raise SkillValidationError(
            f"{source}: name {skill.name!r} is {len(skill.name)} characters; the limit "
            f"is {MAX_NAME_LENGTH}. Shorten it (and its directory name to match). "
            f"See {FORMAT_DOCS_URL}#naming"
        )

    if not NAME_PATTERN.match(skill.name):
        raise SkillValidationError(
            f"{source}: name {skill.name!r} is not a valid skill name. Use lowercase "
            "letters and digits separated by single hyphens (e.g. 'web-research') — "
            "no uppercase, underscores, spaces, or leading/trailing/consecutive "
            f"hyphens. See {FORMAT_DOCS_URL}#naming"
        )

    if len(skill.description) > MAX_DESCRIPTION_LENGTH:
        raise SkillValidationError(
            f"{source}: description is {len(skill.description)} characters; the limit "
            f"is {MAX_DESCRIPTION_LENGTH}. It is a trigger signal, not documentation — "
            "move the detail into the Markdown body. "
            f"See {FORMAT_DOCS_URL}#adopted-base-agent-skills-standard"
        )

    if skill.version is not None and not SEMVER_PATTERN.match(skill.version):
        raise SkillValidationError(
            f"{source}: version {skill.version!r} is not valid SemVer. Use "
            "MAJOR.MINOR.PATCH (e.g. '1.0.0'); omit the field if the skill is "
            f"unversioned. See {FORMAT_DOCS_URL}#field-reference"
        )

    # Parsing the permission strings is the validation — bad grammar raises here.
    skill.parsed_permissions()

    declared = [t.name for t in skill.gaia.tools]
    duplicates = {n for n in declared if declared.count(n) > 1}
    if duplicates:
        raise SkillValidationError(
            f"{source}: metadata.gaia.tools declares {', '.join(sorted(duplicates))} "
            "more than once. Each tool name must be unique within a skill. "
            f"See {FORMAT_DOCS_URL}#tool-registration"
        )
