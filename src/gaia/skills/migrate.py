# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Convert an OpenClaw or Hermes skill into GAIA's ``SKILL.md`` (issue #692).

All three formats share the `Agent Skills <https://agentskills.io>`_ base and
nest their own fields under ``metadata.<vendor>`` — a pattern OpenClaw/ClawHub
and Hermes arrived at independently, and the reason a foreign skill survives the
trip losslessly. Migration reads the foreign namespace and writes a
``metadata.gaia`` block:

- **Fully modeled fields are consumed** — they move into ``metadata.gaia`` and
  leave the vendor block, because GAIA now owns their meaning.
- **Partially or un-modeled fields are preserved** under ``metadata.<vendor>``
  and named in the migration report, so nothing is dropped on the floor and a
  human knows exactly what still needs a look.

Two invariants hold for every migration:

1. **The result lands ``experimental``**, whatever tier the source claimed —
   the same trust-reset ``gaia skill import`` applies
   (:func:`~gaia.skills.format.reset_security_tier`).
2. **A skill needing a local capability is refused, never downgraded.** v1
   bridges instruction-only and connector-backed skills; a source that shells
   out (``requires.bins``) or reads config files (``requires.config``) maps onto
   ``shell`` / ``filesystem``, which have no enforcement until the Phase 2
   sandbox (`#1019 <https://github.com/amd/gaia/issues/1019>`_). Such a skill is
   reported **unmigratable with the reason** rather than quietly stripped of the
   permission to make the migration "succeed".

Migration never writes an invalid skill: the output is round-tripped through
:func:`~gaia.skills.format.parse_skill` before it is offered for install.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from gaia.logger import get_logger
from gaia.skills.errors import (
    FORMAT_DOCS_URL,
    SkillPermissionError,
    SkillValidationError,
)
from gaia.skills.format import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    NAME_PATTERN,
    SEMVER_PATTERN,
    SKILL_FILENAME,
    GaiaMetadata,
    Skill,
    SkillRequirements,
    parse_skill,
    reset_security_tier,
    split_frontmatter,
)
from gaia.skills.naming import skill_directory
from gaia.skills.permissions import refuse_unbridged_permissions

log = get_logger(__name__)

#: Vendor id for OpenClaw / ClawHub skills.
VENDOR_OPENCLAW = "openclaw"

#: Vendor id for Hermes Agent (Nous Research) skills.
VENDOR_HERMES = "hermes"

#: Migration sources, in the order :func:`detect_vendor` probes them.
VENDORS: tuple[str, ...] = (VENDOR_OPENCLAW, VENDOR_HERMES)

#: ``metadata`` keys that mean "this is an OpenClaw skill". ``clawdbot`` and
#: ``clawdis`` are OpenClaw's own documented aliases for the same namespace.
OPENCLAW_NAMESPACES: tuple[str, ...] = ("openclaw", "clawdbot", "clawdis")

#: ``metadata`` keys that mean "this is a Hermes skill".
HERMES_NAMESPACES: tuple[str, ...] = ("hermes",)

_NAMESPACES: dict[str, tuple[str, ...]] = {
    VENDOR_OPENCLAW: OPENCLAW_NAMESPACES,
    VENDOR_HERMES: HERMES_NAMESPACES,
}

# `install:` entries name a package manager either as the key ({node: "x"}) or
# as a type/manager field ({type: node, package: "x"}).
_PY_MANAGERS = frozenset({"uv", "pip", "pipx", "python", "poetry"})
_NODE_MANAGERS = frozenset({"node", "npm", "pnpm", "yarn", "bun"})


@dataclass
class MigrationOutcome:
    """What one source skill migrated into — or why it could not.

    ``migrated`` is the single source of truth: it is true exactly when
    ``skill`` holds a validated GAIA skill and ``blockers`` is empty.
    """

    source: Path
    vendor: str
    #: The validated GAIA skill, or ``None`` when the source is unmigratable.
    skill: Optional[Skill] = None
    #: Reasons the skill cannot be migrated. Non-empty ⇒ nothing was produced.
    blockers: list[str] = field(default_factory=list)
    #: Field-level notes a human should review (preserved or coerced values).
    notes: list[str] = field(default_factory=list)
    #: Human-readable ``<source field> → <gaia field>`` lines that were applied.
    mapped: list[str] = field(default_factory=list)
    #: Tier the source claimed, before the trust reset.
    claimed_tier: Optional[str] = None
    #: The source's own ``name`` field, so an unmigratable skill is still nameable.
    source_name: Optional[str] = None

    @property
    def migrated(self) -> bool:
        """True when a validated GAIA skill was produced."""
        return self.skill is not None and not self.blockers

    @property
    def name(self) -> str:
        """Best-known name for the source, for reporting."""
        if self.skill is not None:
            return self.skill.name
        if self.source_name:
            return self.source_name
        stem = self.source.parent.name if self.source.name == SKILL_FILENAME else None
        return stem or self.source.stem or str(self.source)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable report of this outcome."""
        return {
            "source": str(self.source),
            "vendor": self.vendor,
            "name": self.name,
            "migrated": self.migrated,
            "claimed_tier": self.claimed_tier,
            "security_tier": self.skill.security_tier if self.skill else None,
            "permissions": list(self.skill.gaia.permissions) if self.skill else [],
            "blockers": list(self.blockers),
            "notes": list(self.notes),
            "mapped": list(self.mapped),
        }


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------


#: Fields that identify a block as OpenClaw's even with no namespace key around
#: it. Real published skills put these directly under ``metadata`` or at the top
#: level about as often as they nest them properly.
_OPENCLAW_SHAPE = frozenset(
    {"requires", "install", "primaryEnv", "envVars", "emoji", "skillKey", "always"}
)

# Where a vendor's fields were found — decides where leftovers are written back.
_ORIGIN_METADATA_NS = "metadata-namespace"
_ORIGIN_TOP_NS = "top-level-namespace"
_ORIGIN_METADATA_INLINE = "metadata-inline"
_ORIGIN_FRONTMATTER_INLINE = "frontmatter-inline"


@dataclass
class _VendorFields:
    """A vendor's fields plus where they were found, so leftovers go back there."""

    label: str
    origin: str
    fields: dict[str, Any]
    key: Optional[str] = None
    #: Set when the namespace exists but is not a mapping — there is nothing to
    #: map, and the value is carried through verbatim rather than dropped.
    non_mapping: Any = None
    has_non_mapping: bool = False


def _looks_like_openclaw(block: Any) -> bool:
    """True when a mapping carries OpenClaw-shaped fields under no namespace."""
    return isinstance(block, dict) and bool(_OPENCLAW_SHAPE & set(block))


def detect_vendor(frontmatter: dict) -> Optional[str]:
    """Return the vendor whose fields this frontmatter carries, if any.

    Probes the ``metadata.<vendor>`` namespace first — the field GAIA's own
    format reserves for exactly this — then the shapes real published skills
    actually use: the namespace hoisted to the top level, and the vendor's
    fields inlined with no namespace at all. A document with none of those is
    already a plain Agent Skills skill and needs no migration, so ``None`` is
    returned rather than a guess.
    """
    metadata = frontmatter.get("metadata")
    for vendor in VENDORS:
        names = _NAMESPACES[vendor]
        if isinstance(metadata, dict) and any(key in metadata for key in names):
            return vendor
        if any(key in frontmatter for key in names):
            return vendor

    if _looks_like_openclaw(metadata) or _looks_like_openclaw(frontmatter):
        return VENDOR_OPENCLAW
    return None


def _locate_vendor_fields(frontmatter: dict, vendor: str) -> _VendorFields:
    """Find this vendor's fields wherever the source actually put them."""
    metadata = frontmatter.get("metadata")
    names = _NAMESPACES[vendor]

    # 1. The documented home: metadata.<vendor>. First alias wins — several real
    #    skills duplicate the same payload under two aliases for back-compat, and
    #    merging them would double-count every requirement.
    if isinstance(metadata, dict):
        for key in names:
            if key in metadata:
                block = metadata[key]
                return _VendorFields(
                    label=f"metadata.{key}",
                    origin=_ORIGIN_METADATA_NS,
                    fields=dict(block) if isinstance(block, dict) else {},
                    key=key,
                    non_mapping=None if isinstance(block, dict) else block,
                    has_non_mapping=not isinstance(block, dict),
                )

    # 2. The namespace hoisted to the top level (seen in the wild).
    for key in names:
        if key in frontmatter:
            block = frontmatter[key]
            return _VendorFields(
                label=key,
                origin=_ORIGIN_TOP_NS,
                fields=dict(block) if isinstance(block, dict) else {},
                key=key,
                non_mapping=None if isinstance(block, dict) else block,
                has_non_mapping=not isinstance(block, dict),
            )

    # 3/4. Vendor fields inlined with no namespace key at all.
    if vendor == VENDOR_OPENCLAW:
        if _looks_like_openclaw(metadata):
            return _VendorFields(
                label="metadata",
                origin=_ORIGIN_METADATA_INLINE,
                fields={k: v for k, v in metadata.items() if k != "gaia"},
            )
        if _looks_like_openclaw(frontmatter):
            return _VendorFields(
                label="(frontmatter)",
                origin=_ORIGIN_FRONTMATTER_INLINE,
                fields={
                    k: v
                    for k, v in frontmatter.items()
                    if k in _OPENCLAW_SHAPE or k == "os"
                },
            )

    return _VendorFields(
        label=f"metadata.{names[0]}",
        origin=_ORIGIN_METADATA_NS,
        fields={},
        key=names[0],
    )


# ----------------------------------------------------------------------
# Field coercion
# ----------------------------------------------------------------------


def _normalize_name(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """Coerce a foreign skill name to GAIA's ``lowercase-with-hyphens`` grammar."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    original = raw.strip()
    candidate = re.sub(r"[^a-z0-9]+", "-", original.lower()).strip("-")
    candidate = candidate[:MAX_NAME_LENGTH].strip("-")
    if not candidate or not NAME_PATTERN.match(candidate):
        return None, None
    note = (
        None
        if candidate == original
        else f"name: {original!r} → {candidate!r} (GAIA names are lowercase-with-hyphens)"
    )
    return candidate, note


def _normalize_version(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """Coerce a foreign version to SemVer, or drop it with a note."""
    if raw is None:
        return None, None
    text = str(raw).strip().lstrip("vV")
    if not text:
        return None, None
    if SEMVER_PATTERN.match(text):
        return text, None
    if re.fullmatch(r"\d+\.\d+", text):
        return f"{text}.0", f"version: {raw!r} → '{text}.0' (GAIA requires full SemVer)"
    if re.fullmatch(r"\d+", text):
        return (
            f"{text}.0.0",
            f"version: {raw!r} → '{text}.0.0' (GAIA requires full SemVer)",
        )
    return None, (
        f"version: {raw!r} is not SemVer and was dropped — the migrated skill is "
        "unversioned. Set 'version: MAJOR.MINOR.PATCH' by hand if you need one."
    )


def _string_list(value: Any) -> list[str]:
    """Coerce a scalar-or-list frontmatter field to a list of strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _env_var_names(entries: Any) -> list[str]:
    """Pull env var names out of OpenClaw's ``envVars`` shape.

    Documented as ``[{name: ..., required: ..., description: ...}]``; published
    skills also use ``key`` for the same field, and plain strings.
    """
    names: list[str] = []
    for entry in entries if isinstance(entries, (list, tuple)) else []:
        if isinstance(entry, dict):
            for field_name in ("name", "key"):
                value = entry.get(field_name)
                if isinstance(value, str) and value.strip():
                    names.append(value.strip())
                    break
        elif isinstance(entry, str) and entry.strip():
            names.append(entry.strip())
    return names


def _classify_install_entry(entry: Any) -> tuple[Optional[str], Optional[str]]:
    """Return ``(bucket, package)`` for one ``install:`` entry.

    ``bucket`` is ``"python"``, ``"node"``, or ``None`` when the entry names a
    manager GAIA's ``requirements`` cannot express (brew, go, cargo, nix, …).
    """
    if not isinstance(entry, dict):
        return None, None

    for manager, package in entry.items():
        key = str(manager).lower()
        if key in _PY_MANAGERS and isinstance(package, str):
            return "python", package.strip()
        if key in _NODE_MANAGERS and isinstance(package, str):
            return "node", package.strip()

    declared = entry.get("type") or entry.get("manager") or entry.get("kind")
    package = entry.get("package") or entry.get("name") or entry.get("spec")
    if isinstance(declared, str) and isinstance(package, str):
        key = declared.lower()
        if key in _PY_MANAGERS:
            return "python", package.strip()
        if key in _NODE_MANAGERS:
            return "node", package.strip()
    return None, None


# ----------------------------------------------------------------------
# Vendor field mapping
# ----------------------------------------------------------------------


def _map_requires(
    requires: Any,
    *,
    outcome: MigrationOutcome,
    namespace: str,
    gaia: GaiaMetadata,
    requirements: SkillRequirements,
) -> set[str]:
    """Map a vendor ``requires`` block. Returns the keys it fully consumed."""
    if not isinstance(requires, dict):
        return set()

    consumed: set[str] = set()
    prefix = f"{namespace}.requires"

    # `env` declares which variables must be present — exactly GAIA's advisory
    # requirements.env_vars. It grants no capability, so it is not an `env:read`
    # permission and does not make the skill unmigratable.
    env = _string_list(requires.get("env"))
    if env:
        requirements.env_vars.extend(e for e in env if e not in requirements.env_vars)
        outcome.mapped.append(
            f"{prefix}.env → metadata.gaia.requirements.env_vars ({', '.join(env)})"
        )
        consumed.add("env")

    tools = _string_list(requires.get("tools"))
    if tools:
        gaia.tools_required.extend(t for t in tools if t not in gaia.tools_required)
        outcome.mapped.append(
            f"{prefix}.tools → metadata.gaia.tools_required ({', '.join(tools)})"
        )
        consumed.add("tools")

    # Shelling out to a binary is a `shell:execute` capability. Declared
    # faithfully here; refuse_unbridged_permissions() turns it into a blocker.
    for key in ("bins", "anyBins"):
        bins = _string_list(requires.get(key))
        if not bins:
            continue
        for binary in bins:
            grant = f"shell:execute:{binary}"
            if grant not in gaia.permissions:
                gaia.permissions.append(grant)
        outcome.mapped.append(
            f"{prefix}.{key} → metadata.gaia.permissions "
            f"({', '.join(f'shell:execute:{b}' for b in bins)})"
        )
        if key == "anyBins":
            outcome.notes.append(
                f"{prefix}.anyBins requires only *one* of {', '.join(bins)}, but GAIA's "
                "permission grammar has no any-of form, so each is declared separately. "
                "Narrow the list by hand to the binary you actually use."
            )
        consumed.add(key)

    config = _string_list(requires.get("config"))
    if config:
        for path in config:
            grant = f"filesystem:read:{path}"
            if grant not in gaia.permissions:
                gaia.permissions.append(grant)
        outcome.mapped.append(
            f"{prefix}.config → metadata.gaia.permissions "
            f"({', '.join(f'filesystem:read:{p}' for p in config)})"
        )
        consumed.add("config")

    # str() every key: YAML 1.1 turns a bare `on:`/`no:` key into a bool, and
    # sorting or joining a mixed-type key set raises.
    leftover = sorted(str(key) for key in set(requires) - consumed)
    if leftover:
        outcome.notes.append(
            f"{prefix} keys {', '.join(leftover)} have no GAIA equivalent and were "
            f"preserved under {namespace} for review."
        )
    return consumed


def _map_openclaw(
    block: dict[str, Any],
    *,
    outcome: MigrationOutcome,
    namespace: str,
    gaia: GaiaMetadata,
    requirements: SkillRequirements,
) -> dict[str, Any]:
    """Map ``metadata.openclaw`` onto ``metadata.gaia``; return what remains."""
    remaining = dict(block)

    consumed_requires = _map_requires(
        block.get("requires"),
        outcome=outcome,
        namespace=namespace,
        gaia=gaia,
        requirements=requirements,
    )
    if consumed_requires:
        leftover_requires = {
            k: v
            for k, v in (block.get("requires") or {}).items()
            if k not in consumed_requires
        }
        if leftover_requires:
            remaining["requires"] = leftover_requires
        else:
            remaining.pop("requires", None)

    # `env` as a sibling of `requires` is the envVars shape under another name.
    env_names = (
        _string_list(block.get("primaryEnv"))
        + _env_var_names(block.get("envVars"))
        + _env_var_names(block.get("env"))
        + _env_var_names(block.get("optionalEnv"))
    )
    if env_names:
        added = [e for e in env_names if e not in requirements.env_vars]
        requirements.env_vars.extend(added)
        outcome.mapped.append(
            f"{namespace}.primaryEnv/envVars/env → "
            f"metadata.gaia.requirements.env_vars ({', '.join(env_names)})"
        )
        # envVars/env carry per-variable descriptions GAIA does not model, so the
        # block stays for reference; primaryEnv is fully expressed by env_vars.
        remaining.pop("primaryEnv", None)

    install = block.get("install")
    if isinstance(install, (list, tuple)) and install:
        unmapped: list[Any] = []
        for entry in install:
            bucket, package = _classify_install_entry(entry)
            if bucket == "python" and package not in requirements.dependencies:
                requirements.dependencies.append(package)
            elif bucket == "node" and package not in requirements.node_dependencies:
                requirements.node_dependencies.append(package)
            elif bucket is None:
                unmapped.append(entry)
        if requirements.dependencies or requirements.node_dependencies:
            outcome.mapped.append(
                f"{namespace}.install → metadata.gaia.requirements "
                "dependencies/node_dependencies"
            )
        if unmapped:
            outcome.notes.append(
                f"{namespace}.install has {len(unmapped)} entry(ies) whose "
                "package manager GAIA does not model (brew/go/cargo/nix and friends). "
                "GAIA never runs install steps — install those yourself. The full "
                f"install list is preserved under {namespace}."
            )

    advisory = [k for k in ("os", "always", "skillKey", "nix", "config") if k in block]
    if advisory:
        outcome.notes.append(
            f"{namespace} keys {', '.join(advisory)} are OpenClaw runtime "
            f"directives GAIA does not act on; preserved under {namespace}."
        )
    return remaining


def _map_hermes(
    block: dict[str, Any],
    *,
    outcome: MigrationOutcome,
    namespace: str,
    gaia: GaiaMetadata,
    requirements: SkillRequirements,
) -> dict[str, Any]:
    """Map ``metadata.hermes`` onto ``metadata.gaia``; return what remains."""
    remaining = dict(block)

    consumed_requires = _map_requires(
        block.get("requires"),
        outcome=outcome,
        namespace=namespace,
        gaia=gaia,
        requirements=requirements,
    )
    if consumed_requires:
        leftover_requires = {
            k: v
            for k, v in (block.get("requires") or {}).items()
            if k not in consumed_requires
        }
        if leftover_requires:
            remaining["requires"] = leftover_requires
        else:
            remaining.pop("requires", None)

    descriptive = [k for k in ("category", "tags", "author") if k in block]
    if descriptive:
        outcome.notes.append(
            f"{namespace} keys {', '.join(descriptive)} are descriptive "
            f"metadata GAIA does not model; preserved under {namespace}."
        )
    return remaining


_MAPPERS = {VENDOR_OPENCLAW: _map_openclaw, VENDOR_HERMES: _map_hermes}


# ----------------------------------------------------------------------
# Migration
# ----------------------------------------------------------------------


def migrate_text(
    text: str,
    *,
    vendor: str = "auto",
    source: Path | str = "<string>",
    name: Optional[str] = None,
) -> MigrationOutcome:
    """Migrate one foreign ``SKILL.md``'s text into a GAIA :class:`Skill`.

    Args:
        text: The source file contents.
        vendor: ``openclaw``, ``hermes``, or ``auto`` to detect from
            ``metadata.<vendor>``.
        source: Path quoted in the report and in error messages.
        name: Override the migrated skill's name.

    Returns:
        A :class:`MigrationOutcome`. Check ``migrated`` before using ``skill`` —
        an unmigratable source yields ``skill=None`` and populated ``blockers``.

    Raises:
        SkillValidationError: if the text is not a frontmatter document at all,
            if ``vendor`` is unknown, or if ``vendor='auto'`` finds no vendor
            namespace to migrate from.
    """
    source_path = Path(source)
    try:
        frontmatter, body = split_frontmatter(text, source=str(source))
    except SkillValidationError as exc:
        # ~5% of published OpenClaw skills ship no frontmatter at all. That is a
        # per-skill blocker, not a reason to abort a whole collection.
        return MigrationOutcome(
            source=source_path,
            vendor=vendor if vendor in VENDORS else "unknown",
            blockers=[
                f"{exc} A skill with no frontmatter has no name or description to "
                "migrate; add them to the source and migrate again."
            ],
        )

    if vendor == "auto":
        detected = detect_vendor(frontmatter)
        if detected is None:
            raise SkillValidationError(
                f"{source}: no vendor namespace found, so there is nothing to migrate. "
                f"Expected one of metadata.{', metadata.'.join(OPENCLAW_NAMESPACES + HERMES_NAMESPACES)}. "
                "A skill with no vendor namespace is already a plain Agent Skills "
                "document — install it directly with 'gaia skill import'. To force a "
                f"format anyway, pass --from openclaw or --from hermes. See {FORMAT_DOCS_URL}"
                "#cross-format-compatibility--migration"
            )
        vendor = detected
    elif vendor not in VENDORS:
        raise SkillValidationError(
            f"Unknown migration source {vendor!r}. Supported: "
            f"{', '.join(VENDORS)}, or 'auto' to detect. See {FORMAT_DOCS_URL}"
            "#cross-format-compatibility--migration"
        )

    raw_name = frontmatter.get("name")
    outcome = MigrationOutcome(
        source=source_path,
        vendor=vendor,
        source_name=raw_name.strip() if isinstance(raw_name, str) else None,
    )
    located = _locate_vendor_fields(frontmatter, vendor)
    namespace, block = located.label, located.fields
    if located.origin in (
        _ORIGIN_TOP_NS,
        _ORIGIN_METADATA_INLINE,
        _ORIGIN_FRONTMATTER_INLINE,
    ):
        outcome.notes.append(
            f"{source}: this skill's {vendor} fields are at '{located.label}', not the "
            f"documented metadata.{_NAMESPACES[vendor][0]}. They were migrated from "
            "where they actually are."
        )
    duplicates = [
        key
        for key in _NAMESPACES[vendor][1:]
        if located.key
        and key != located.key
        and (
            key in frontmatter
            or (
                isinstance(frontmatter.get("metadata"), dict)
                and key in frontmatter["metadata"]
            )
        )
    ]
    if duplicates:
        outcome.notes.append(
            f"{source}: '{located.label}' was used; the equivalent alias(es) "
            f"{', '.join(duplicates)} are also present and were preserved unread "
            "rather than merged (merging would double-count every requirement). "
            "Delete the stale one after reviewing."
        )

    # --- required base fields -------------------------------------------
    resolved_name, rename_note = _normalize_name(name or frontmatter.get("name"))
    if resolved_name is None:
        outcome.blockers.append(
            f"{source}: the skill's 'name' is missing, or cannot be expressed as a GAIA "
            "skill name (lowercase letters and digits separated by single hyphens). "
            f"Pass --name <valid-name> to choose one. See {FORMAT_DOCS_URL}#naming"
        )
    elif rename_note and name is None:
        outcome.notes.append(rename_note)

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        outcome.blockers.append(
            f"{source}: required field 'description' is missing or empty. It is the "
            "trigger signal the model reads to decide relevance, and it cannot be "
            "invented — add one to the source skill and migrate again. "
            f"See {FORMAT_DOCS_URL}#adopted-base-agent-skills-standard"
        )
        description = ""
    elif len(description.strip()) > MAX_DESCRIPTION_LENGTH:
        outcome.blockers.append(
            f"{source}: description is {len(description.strip())} characters; GAIA's "
            f"limit is {MAX_DESCRIPTION_LENGTH}. Truncating it would silently weaken the "
            "trigger signal, so the migration stops here — shorten it in the source "
            f"(move the detail into the body) and migrate again. See {FORMAT_DOCS_URL}"
            "#adopted-base-agent-skills-standard"
        )

    version, version_note = _normalize_version(frontmatter.get("version"))
    if version_note:
        outcome.notes.append(version_note)

    # --- vendor namespace ------------------------------------------------
    gaia = GaiaMetadata()
    requirements = SkillRequirements()
    remaining: Any = {}
    if block:
        remaining = _MAPPERS[vendor](
            block,
            outcome=outcome,
            namespace=namespace,
            gaia=gaia,
            requirements=requirements,
        )
    elif located.has_non_mapping:
        # e.g. `metadata.openclaw: "some string"`. Nothing to map, but the value
        # is carried through verbatim rather than dropped on the floor.
        remaining = located.non_mapping
        outcome.notes.append(
            f"{source}: {namespace} is a "
            f"{type(located.non_mapping).__name__}, not a mapping, so there were no "
            "fields to migrate. Its value was preserved verbatim; the skill migrated "
            "as instruction-only."
        )
    else:
        outcome.notes.append(
            f"{source}: {namespace} is absent or empty — migrated as an "
            "instruction-only skill."
        )
    gaia.requirements = requirements

    log.debug(
        "Migrating %s: vendor=%s namespace=%s origin=%s mapped=%d note(s)=%d",
        source,
        vendor,
        namespace,
        located.origin,
        len(outcome.mapped),
        len(outcome.notes),
    )

    # --- carry the rest of metadata through ------------------------------
    # Whatever the mapper did not consume goes back exactly where it came from,
    # so a round-trip through GAIA never silently relocates a vendor's fields.
    consumed = set(block) - set(remaining) if isinstance(remaining, dict) else set()
    other_metadata: dict[str, Any] = {}
    raw_metadata = frontmatter.get("metadata")
    if isinstance(raw_metadata, dict):
        for key, value in raw_metadata.items():
            if key == "gaia":
                continue
            if located.origin == _ORIGIN_METADATA_NS and key == located.key:
                if remaining:
                    other_metadata[key] = remaining
            elif located.origin == _ORIGIN_METADATA_INLINE and key in consumed:
                continue
            else:
                other_metadata[key] = value

    # A source may already carry a metadata.gaia block claiming a tier. Adopt the
    # claim onto the skill so the reset below is what actually revokes it, rather
    # than the claim merely never being read.
    claimed = raw_metadata.get("gaia") if isinstance(raw_metadata, dict) else None
    if isinstance(claimed, dict):
        claimed_tier = claimed.get("security_tier")
        if isinstance(claimed_tier, str) and claimed_tier.strip():
            gaia.security_tier = claimed_tier.strip()

    known_top_level = {"name", "description", "license", "version", "metadata"}
    extra_fields: dict[str, Any] = {}
    for key, value in frontmatter.items():
        if key in known_top_level:
            continue
        if located.origin == _ORIGIN_TOP_NS and key == located.key:
            if remaining:
                extra_fields[key] = remaining
            continue
        if located.origin == _ORIGIN_FRONTMATTER_INLINE and key in consumed:
            continue
        extra_fields[key] = value
    if extra_fields:
        outcome.notes.append(
            f"{source}: top-level key(s) "
            f"{', '.join(sorted(str(key) for key in extra_fields))} are not part "
            "of the Agent Skills base; preserved verbatim in the frontmatter."
        )

    license_value = frontmatter.get("license")

    if outcome.blockers:
        return outcome

    skill = Skill(
        name=resolved_name,
        description=description.strip(),
        body=body.strip("\n"),
        license=license_value if isinstance(license_value, str) else None,
        version=version,
        gaia=gaia,
        other_metadata=other_metadata,
        extra_fields=extra_fields,
    )

    # Every migrated skill re-earns trust — the same reset `gaia skill import`
    # applies, never a second security path.
    previous_tier = reset_security_tier(skill)
    if previous_tier != skill.security_tier:
        outcome.claimed_tier = previous_tier

    # v1 accepts instruction-only and connector-bridged skills. A mapped local
    # capability is refused with the runtime's own message — not stripped to
    # make the migration look like it worked.
    try:
        refuse_unbridged_permissions(skill.parsed_permissions(), skill_name=skill.name)
    except SkillPermissionError as exc:
        outcome.blockers.append(f"{exc}")
        return outcome

    # A migration that emits an invalid skill is a bug, not a partial success:
    # round-trip the output through the real parser before offering it.
    try:
        parse_skill(skill.to_markdown(), source=f"<migrated {skill.name}>")
    except SkillValidationError as exc:
        raise SkillValidationError(
            f"Migrating {source} produced a SKILL.md that GAIA's own parser rejects: "
            f"{exc}\nThis is a defect in the {vendor} migrator, not in your skill — "
            "please report it at https://github.com/amd/gaia/issues with the source "
            "skill attached."
        ) from exc

    outcome.skill = skill
    return outcome


def migrate_skill_dir(
    path: Path | str, *, vendor: str = "auto", name: Optional[str] = None
) -> MigrationOutcome:
    """Migrate the skill at ``path`` (a directory or its ``SKILL.md``)."""
    path = Path(path).expanduser()
    skill_file = path / SKILL_FILENAME if path.is_dir() else path

    if not skill_file.is_file():
        raise SkillValidationError(
            f"No {SKILL_FILENAME} at {skill_file}. Point 'gaia skill migrate' at a "
            f"skill directory containing {SKILL_FILENAME}, or at a directory of such "
            "directories to migrate a whole collection."
        )
    try:
        text = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillValidationError(
            f"Could not read {skill_file}: {exc}. Check the file's permissions and "
            "that it is UTF-8 encoded."
        ) from exc

    return migrate_text(text, vendor=vendor, source=skill_file, name=name)


def find_source_skills(path: Path | str) -> list[Path]:
    """Return every skill directory at ``path`` — itself, or its children.

    Lets one command handle both a single skill and a checked-out collection
    (a ClawHub clone), which is how a real migration actually arrives.
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise SkillValidationError(
            f"{path} does not exist. Point 'gaia skill migrate' at a skill directory, "
            "its SKILL.md, or a directory containing several skill directories."
        )
    if path.is_file():
        # Return the file itself, not its directory — pointing at ./OTHER.md must
        # migrate that file, never a SKILL.md that happens to sit beside it.
        return [path]
    if (path / SKILL_FILENAME).is_file():
        return [path]

    found = sorted(
        child.parent for child in path.glob(f"*/{SKILL_FILENAME}") if child.is_file()
    )
    if not found:
        raise SkillValidationError(
            f"No {SKILL_FILENAME} found in {path} or any of its immediate "
            f"subdirectories. A skill is a directory whose one required file is "
            f"{SKILL_FILENAME}."
        )
    return found


def install_migrated(
    outcome: MigrationOutcome,
    destination_root: Path,
    *,
    force: bool = False,
    copy_support_files: bool = True,
) -> Path:
    """Write a migrated skill into ``destination_root/<name>/``.

    Args:
        outcome: A migrated outcome. Must have ``migrated`` true.
        destination_root: Usually ``SkillManager().user_root``.
        force: Replace an existing installed skill of the same name.
        copy_support_files: Also copy the source's non-``SKILL.md`` files, so
            scripts and templates the instructions reference still resolve.

    Returns:
        The directory the skill was written to.
    """
    if not outcome.migrated or outcome.skill is None:
        raise SkillValidationError(
            f"Refusing to install '{outcome.name}': it was not migrated "
            f"({len(outcome.blockers)} blocker(s)). Resolve the blockers first."
        )

    skill = outcome.skill
    # skill.name came out of a foreign vendor's manifest; --force rmtrees this
    # path, so it is validated as a bare name inside the root before it is used.
    target = skill_directory(
        destination_root, skill.name, source=f"migrate {outcome.source}"
    )
    source_dir = outcome.source.parent if outcome.source.is_file() else None
    if target.exists():
        if (
            force
            and source_dir is not None
            and target.resolve() == source_dir.resolve()
        ):
            # --force replaces the directory; over the source that is a delete.
            raise SkillValidationError(
                f"Refusing to install '{skill.name}' on top of its own source at "
                f"{target}: --force replaces the directory, which would destroy the "
                "source skill and every support file beside it. Migrate to a "
                "different directory with --out <dir>, then swap it in yourself."
            )
        if not force:
            raise SkillValidationError(
                f"Skill '{skill.name}' is already installed at {target}. Pass --force "
                "to replace it, or --name to install under a different name."
            )
        shutil.rmtree(target)
    target.mkdir(parents=True)

    if copy_support_files and source_dir is not None:
        for entry in sorted(source_dir.iterdir()):
            if entry.name == SKILL_FILENAME:
                continue
            destination = target / entry.name
            if entry.is_dir():
                shutil.copytree(entry, destination)
            else:
                shutil.copy2(entry, destination)

    skill.write(target / SKILL_FILENAME)
    return target


def format_report(outcomes: Sequence[MigrationOutcome]) -> str:
    """Render the human-readable migration report."""
    lines: list[str] = []
    for outcome in outcomes:
        skill = outcome.skill
        if outcome.migrated and skill is not None:
            lines.append(f"✅ {skill.name}  ({outcome.vendor} → gaia)")
            lines.append(f"   source       : {outcome.source}")
            lines.append(f"   security tier: {skill.security_tier}")
            if outcome.claimed_tier:
                lines.append(
                    f"   tier reset   : {outcome.claimed_tier} → "
                    f"{skill.security_tier} (migrated skills re-earn trust)"
                )
            if skill.gaia.permissions:
                lines.append(f"   permissions  : {', '.join(skill.gaia.permissions)}")
        else:
            lines.append(f"❌ {outcome.name}  (unmigratable)")
            lines.append(f"   source       : {outcome.source}")
            for blocker in outcome.blockers:
                lines.append(f"   ✗ {blocker}")
        for entry in outcome.mapped:
            lines.append(f"   → {entry}")
        for note in outcome.notes:
            lines.append(f"   ⚠ {note}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n" if lines else ""


__all__ = [
    "VENDOR_OPENCLAW",
    "VENDOR_HERMES",
    "VENDORS",
    "OPENCLAW_NAMESPACES",
    "HERMES_NAMESPACES",
    "MigrationOutcome",
    "detect_vendor",
    "migrate_text",
    "migrate_skill_dir",
    "find_source_skills",
    "install_migrated",
    "format_report",
]
