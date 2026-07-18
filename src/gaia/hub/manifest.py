# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Parser and validator for the ``gaia-agent.yaml`` package manifest.

Every Agent Hub package — builtin, custom, or community — ships a
``gaia-agent.yaml`` describing its identity, hub-display metadata, technical
requirements, and declared permissions.  This module is the single source of
truth for that format: :func:`parse` loads a file and returns a validated
:class:`AgentManifest`, or raises :class:`ManifestError` with an actionable
message naming what failed, what to do, and where to look.

Validation follows the project's fail-loudly rule (see ``CLAUDE.md``): a
malformed manifest never silently degrades to a partial/default object — it
raises.  The only defaulting that happens is for *unspecified* optional fields,
and security-relevant defaults choose the least-privileged value (an
unspecified ``security_tier`` defaults to ``experimental``).

Schema reference: ``docs/spec/agent-hub-restructure.mdx`` (Package Format →
``gaia-agent.yaml``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from gaia.agents.registry import _RESERVED_BUILTIN_IDS

# ---------------------------------------------------------------------------
# Validation vocabulary
# ---------------------------------------------------------------------------

# Agent id: lowercase alphanumeric with internal hyphens, 1–52 chars, must
# start and end with an alphanumeric.  Mirrors the npm-style slug used by the
# hub URL scheme (``/agents/<id>``).
_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,50}[a-z0-9])?$")

# Official SemVer 2.0.0 grammar (https://semver.org). Captures optional
# pre-release and build-metadata suffixes so "1.2.3-rc.1+build.5" is valid.
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# Permissions use domain-scoped syntax: ``<domain>:<action>`` (see the security
# model plan, docs/plans/security-model.mdx — "domain-scoped syntax
# (filesystem:read, network:write, etc.)").
_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")

VALID_LANGUAGES = frozenset({"python", "cpp"})

VALID_SECURITY_TIERS = frozenset({"verified", "community", "experimental"})

# Multi-component discriminator (#1716). Defaults to "agent" so the existing
# agent-only manifests keep validating unchanged.
VALID_TYPES = frozenset({"agent", "app", "component"})
DEFAULT_TYPE = "agent"

# Least-privileged default for an unspecified tier — an unknown package is
# treated as untrusted until a maintainer promotes it.
DEFAULT_SECURITY_TIER = "experimental"

# Recognized permission domains. The action half is free-form (lowercase
# identifier) so agents can declare granular capabilities, but the domain must
# be one we understand so a typo like ``filesytem:read`` fails loudly.
KNOWN_PERMISSION_DOMAINS = frozenset(
    {
        "filesystem",
        "network",
        "shell",
        "process",
        "clipboard",
        "screenshot",
        "camera",
        "microphone",
        "system",
        "env",
        "notifications",
    }
)

# Platform triples used by ``requirements.platforms`` and ``cpp.binaries``.
VALID_PLATFORMS = frozenset(
    {
        "win-x64",
        "win-arm64",
        "linux-x64",
        "linux-arm64",
        "darwin-x64",
        "darwin-arm64",
    }
)

VALID_INTERFACES = frozenset({"tui", "cli", "pipe", "api_server", "mcp_server"})

# Docs URL surfaced in error messages so the author knows where to look next.
_SPEC_URL = "https://amd-gaia.ai/docs/spec/agent-hub-restructure"


class ManifestError(ValueError):
    """Raised when a ``gaia-agent.yaml`` is missing, malformed, or invalid.

    The message always names three things (per CLAUDE.md): *what* failed,
    *what* the author should do, and *where* to look (the manifest path and/or
    the spec). Subclasses :class:`ValueError` so existing ``except ValueError``
    callers keep working.
    """


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


@dataclass
class Requirements:
    """System requirements block from ``requirements:``."""

    min_memory_gb: Optional[float] = None
    min_disk_gb: Optional[float] = None
    min_context_size: Optional[int] = None
    platforms: List[str] = field(default_factory=list)
    npu: bool = False
    gpu_vram_gb: Optional[float] = None


@dataclass
class PythonConfig:
    """Python entry-point block from ``python:``."""

    entry_module: Optional[str] = None
    entry_class: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)


@dataclass
class CppConfig:
    """Native (C++) binary block from ``cpp:``.

    ``binaries`` maps a platform triple (e.g. ``win-x64``) to the relative
    path of the executable shipped for that platform.
    """

    binaries: Dict[str, str] = field(default_factory=dict)
    static_linked: bool = False


@dataclass
class Interfaces:
    """Declared interface modes from ``interfaces:`` (follows PR #985)."""

    tui: bool = False
    cli: bool = False
    pipe: bool = False
    api_server: bool = False
    mcp_server: bool = False


# ---------------------------------------------------------------------------
# Top-level manifest
# ---------------------------------------------------------------------------


@dataclass
class AgentManifest:
    """A parsed, validated ``gaia-agent.yaml``.

    Construct via :func:`parse` (file) or :meth:`from_dict` (already-loaded
    mapping). Both validate eagerly and raise :class:`ManifestError` on the
    first problem.
    """

    # Identity
    id: str
    name: str
    version: str
    description: str
    author: str
    license: str

    # Technical
    language: str

    # Package kind: agent | app | component (defaults to agent, #1716).
    type: str = DEFAULT_TYPE

    # Hub display
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    icon: str = ""
    avatar: str = ""
    screenshots: List[str] = field(default_factory=list)
    readme: str = ""
    conversation_starters: List[str] = field(default_factory=list)

    # Technical (optional)
    min_gaia_version: Optional[str] = None
    models: List[str] = field(default_factory=list)
    tools_count: int = 0
    security_tier: str = DEFAULT_SECURITY_TIER

    # Blocks
    requirements: Requirements = field(default_factory=Requirements)
    python: Optional[PythonConfig] = None
    cpp: Optional[CppConfig] = None
    permissions: List[str] = field(default_factory=list)
    required_connections: List[str] = field(default_factory=list)
    interfaces: Interfaces = field(default_factory=Interfaces)

    # Provenance — set by :func:`parse`; not part of the YAML.
    source_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls, data: Any, *, source: Optional[Union[str, Path]] = None
    ) -> "AgentManifest":
        """Build and validate an :class:`AgentManifest` from a mapping.

        Args:
            data: The parsed YAML mapping (``dict``).
            source: Optional path to the originating file, used to make error
                messages point at the right manifest.

        Raises:
            ManifestError: If *data* is not a mapping, a required field is
                missing, or any field fails validation.
        """
        src = Path(source) if source is not None else None
        where = f" in {src}" if src else ""

        if not isinstance(data, dict):
            raise ManifestError(
                f"gaia-agent.yaml must be a YAML mapping (key: value){where}, "
                f"got {type(data).__name__}. See {_SPEC_URL}."
            )

        # --- required scalar fields ---
        required = ("id", "name", "version", "description", "author", "license")
        missing = [k for k in required if not _nonempty_str(data.get(k))]
        if missing:
            raise ManifestError(
                f"gaia-agent.yaml{where} is missing required field(s): "
                f"{', '.join(missing)}. Add them as top-level string keys. "
                f"See {_SPEC_URL}."
            )

        agent_id = data["id"]
        _validate_id(agent_id, where)
        _validate_semver(data["version"], "version", where)

        language = data.get("language")
        if not _nonempty_str(language):
            raise ManifestError(
                f"gaia-agent.yaml{where} is missing required field 'language'. "
                f"Set it to one of: {_sorted(VALID_LANGUAGES)}. See {_SPEC_URL}."
            )
        if language not in VALID_LANGUAGES:
            raise ManifestError(
                f"gaia-agent.yaml{where}: language {language!r} is not supported. "
                f"Use one of: {_sorted(VALID_LANGUAGES)}."
            )

        pkg_type = data.get("type", DEFAULT_TYPE)
        if pkg_type not in VALID_TYPES:
            raise ManifestError(
                f"gaia-agent.yaml{where}: type {pkg_type!r} is not a valid "
                f"package type. Use one of: {_sorted(VALID_TYPES)}, or omit it "
                f"to default to 'agent'. See {_SPEC_URL}."
            )

        security_tier = data.get("security_tier", DEFAULT_SECURITY_TIER)
        if security_tier not in VALID_SECURITY_TIERS:
            raise ManifestError(
                f"gaia-agent.yaml{where}: security_tier {security_tier!r} is "
                f"invalid. Use one of: {_sorted(VALID_SECURITY_TIERS)}."
            )

        if data.get("min_gaia_version") is not None:
            _validate_semver(data["min_gaia_version"], "min_gaia_version", where)

        permissions = _validate_permissions(data.get("permissions"), where)
        requirements = _parse_requirements(data.get("requirements"), where)
        interfaces = _parse_interfaces(data.get("interfaces"), where)
        python_cfg = _parse_python(data.get("python"), where)
        cpp_cfg = _parse_cpp(data.get("cpp"), where)

        # Language-specific section requirements. A C++ agent with no binary is
        # unrunnable — fail loudly rather than register a dead package.
        if language == "cpp" and (cpp_cfg is None or not cpp_cfg.binaries):
            raise ManifestError(
                f"gaia-agent.yaml{where}: language is 'cpp' but no 'cpp.binaries' "
                f"are declared. Map at least one platform "
                f"({_sorted(VALID_PLATFORMS)}) to a binary path."
            )

        return cls(
            id=agent_id,
            name=data["name"],
            version=data["version"],
            description=data["description"],
            author=data["author"],
            license=data["license"],
            language=language,
            type=pkg_type,
            category=data.get("category", "general") or "general",
            tags=_str_list(data.get("tags"), "tags", where),
            icon=data.get("icon", "") or "",
            avatar=data.get("avatar", "") or "",
            screenshots=_str_list(data.get("screenshots"), "screenshots", where),
            readme=data.get("readme", "") or "",
            conversation_starters=_str_list(
                data.get("conversation_starters"), "conversation_starters", where
            ),
            min_gaia_version=data.get("min_gaia_version"),
            models=_str_list(data.get("models"), "models", where),
            tools_count=_parse_int(data.get("tools_count"), "tools_count", where, 0),
            security_tier=security_tier,
            requirements=requirements,
            python=python_cfg,
            cpp=cpp_cfg,
            permissions=permissions,
            required_connections=_str_list(
                data.get("required_connections"), "required_connections", where
            ),
            interfaces=interfaces,
            source_path=src,
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse(path: Union[str, Path]) -> AgentManifest:
    """Load and validate a ``gaia-agent.yaml`` file.

    Args:
        path: Path to the manifest file (or a directory containing one).

    Returns:
        A validated :class:`AgentManifest`.

    Raises:
        ManifestError: If the file is missing, unreadable, not valid YAML, or
            fails schema validation. The message names the file and what to fix.
    """
    p = Path(path)
    if p.is_dir():
        p = p / "gaia-agent.yaml"

    if not p.exists():
        raise ManifestError(
            f"Manifest not found: {p}. Every agent package needs a "
            f"'gaia-agent.yaml' at its root. See {_SPEC_URL}."
        )

    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestError(f"Could not read manifest {p}: {e}") from e

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ManifestError(
            f"Invalid YAML in {p}: {e}. Fix the syntax and re-run. " f"See {_SPEC_URL}."
        ) from e

    if data is None:
        raise ManifestError(
            f"Manifest {p} is empty. It must declare at least the required "
            f"identity fields (id, name, version, description, author, license). "
            f"See {_SPEC_URL}."
        )

    return AgentManifest.from_dict(data, source=p)


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _sorted(values) -> str:
    return ", ".join(sorted(values))


def _validate_id(agent_id: Any, where: str) -> None:
    if not isinstance(agent_id, str) or not _ID_RE.match(agent_id):
        raise ManifestError(
            f"gaia-agent.yaml{where}: id {agent_id!r} is invalid. Use 1–52 "
            f"lowercase alphanumeric characters and internal hyphens "
            f"(must start and end with a letter or digit), e.g. 'my-agent'."
        )
    if agent_id in _RESERVED_BUILTIN_IDS:
        raise ManifestError(
            f"gaia-agent.yaml{where}: id {agent_id!r} is reserved for a built-in "
            f"GAIA agent. Choose a different id. Reserved: "
            f"{_sorted(_RESERVED_BUILTIN_IDS)}."
        )


def _validate_semver(value: Any, field_name: str, where: str) -> None:
    if not isinstance(value, str) or not _SEMVER_RE.match(value):
        raise ManifestError(
            f"gaia-agent.yaml{where}: {field_name} {value!r} is not valid SemVer. "
            f"Use MAJOR.MINOR.PATCH (e.g. '0.1.0' or '1.2.3-rc.1'). "
            f"See https://semver.org."
        )


def _validate_permissions(raw: Any, where: str) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ManifestError(
            f"gaia-agent.yaml{where}: permissions must be a list of "
            f"'<domain>:<action>' strings, got {type(raw).__name__}."
        )
    result: List[str] = []
    for perm in raw:
        if not isinstance(perm, str) or not _PERMISSION_RE.match(perm):
            raise ManifestError(
                f"gaia-agent.yaml{where}: permission {perm!r} has invalid syntax. "
                f"Use '<domain>:<action>' (lowercase), e.g. 'filesystem:read'."
            )
        domain = perm.split(":", 1)[0]
        if domain not in KNOWN_PERMISSION_DOMAINS:
            raise ManifestError(
                f"gaia-agent.yaml{where}: permission {perm!r} uses unknown domain "
                f"{domain!r}. Valid domains: {_sorted(KNOWN_PERMISSION_DOMAINS)}."
            )
        result.append(perm)
    return result


def _str_list(raw: Any, field_name: str, where: str) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ManifestError(
            f"gaia-agent.yaml{where}: {field_name} must be a list of strings."
        )
    return list(raw)


def _parse_int(raw: Any, field_name: str, where: str, default: int) -> int:
    if raw is None:
        return default
    # bool is an int subclass; reject it so `tools_count: true` fails loudly.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ManifestError(
            f"gaia-agent.yaml{where}: {field_name} must be an integer, got "
            f"{type(raw).__name__}."
        )
    if raw < 0:
        raise ManifestError(
            f"gaia-agent.yaml{where}: {field_name} must be >= 0, got {raw}."
        )
    return raw


def _parse_number(raw: Any, field_name: str, where: str) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ManifestError(
            f"gaia-agent.yaml{where}: {field_name} must be a number, got "
            f"{type(raw).__name__}."
        )
    if raw < 0:
        raise ManifestError(
            f"gaia-agent.yaml{where}: {field_name} must be >= 0, got {raw}."
        )
    return float(raw)


def _parse_bool(raw: Any, field_name: str, where: str, default: bool) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ManifestError(
            f"gaia-agent.yaml{where}: {field_name} must be true or false, got "
            f"{type(raw).__name__}."
        )
    return raw


def _validate_platforms(raw: Any, field_name: str, where: str) -> List[str]:
    platforms = _str_list(raw, field_name, where)
    bad = [p for p in platforms if p not in VALID_PLATFORMS]
    if bad:
        raise ManifestError(
            f"gaia-agent.yaml{where}: {field_name} has unknown platform(s) "
            f"{bad}. Valid platforms: {_sorted(VALID_PLATFORMS)}."
        )
    return platforms


def _require_mapping(raw: Any, section: str, where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ManifestError(
            f"gaia-agent.yaml{where}: '{section}' must be a mapping, got "
            f"{type(raw).__name__}."
        )
    return raw


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_requirements(raw: Any, where: str) -> Requirements:
    if raw is None:
        return Requirements()
    data = _require_mapping(raw, "requirements", where)
    return Requirements(
        min_memory_gb=_parse_number(
            data.get("min_memory_gb"), "requirements.min_memory_gb", where
        ),
        min_disk_gb=_parse_number(
            data.get("min_disk_gb"), "requirements.min_disk_gb", where
        ),
        min_context_size=(
            None
            if data.get("min_context_size") is None
            else _parse_int(
                data.get("min_context_size"),
                "requirements.min_context_size",
                where,
                0,
            )
        ),
        platforms=_validate_platforms(
            data.get("platforms"), "requirements.platforms", where
        ),
        npu=_parse_bool(data.get("npu"), "requirements.npu", where, False),
        gpu_vram_gb=_parse_number(
            data.get("gpu_vram_gb"), "requirements.gpu_vram_gb", where
        ),
    )


def _parse_interfaces(raw: Any, where: str) -> Interfaces:
    if raw is None:
        return Interfaces()
    data = _require_mapping(raw, "interfaces", where)
    unknown = set(data) - VALID_INTERFACES
    if unknown:
        raise ManifestError(
            f"gaia-agent.yaml{where}: interfaces has unknown key(s) "
            f"{_sorted(unknown)}. Valid interfaces: {_sorted(VALID_INTERFACES)}."
        )
    return Interfaces(
        tui=_parse_bool(data.get("tui"), "interfaces.tui", where, False),
        cli=_parse_bool(data.get("cli"), "interfaces.cli", where, False),
        pipe=_parse_bool(data.get("pipe"), "interfaces.pipe", where, False),
        api_server=_parse_bool(
            data.get("api_server"), "interfaces.api_server", where, False
        ),
        mcp_server=_parse_bool(
            data.get("mcp_server"), "interfaces.mcp_server", where, False
        ),
    )


def _parse_python(raw: Any, where: str) -> Optional[PythonConfig]:
    if raw is None:
        return None
    data = _require_mapping(raw, "python", where)
    entry_module = data.get("entry_module")
    entry_class = data.get("entry_class")
    if entry_module is not None and not _nonempty_str(entry_module):
        raise ManifestError(
            f"gaia-agent.yaml{where}: python.entry_module must be a non-empty "
            f"string (e.g. 'gaia_agent_chat.agent')."
        )
    if entry_class is not None and not _nonempty_str(entry_class):
        raise ManifestError(
            f"gaia-agent.yaml{where}: python.entry_class must be a non-empty "
            f"string (e.g. 'ChatAgent')."
        )
    return PythonConfig(
        entry_module=entry_module,
        entry_class=entry_class,
        dependencies=_str_list(data.get("dependencies"), "python.dependencies", where),
    )


def _parse_cpp(raw: Any, where: str) -> Optional[CppConfig]:
    if raw is None:
        return None
    data = _require_mapping(raw, "cpp", where)
    binaries_raw = data.get("binaries")
    binaries: Dict[str, str] = {}
    if binaries_raw is not None:
        if not isinstance(binaries_raw, dict):
            raise ManifestError(
                f"gaia-agent.yaml{where}: cpp.binaries must be a mapping of "
                f"platform -> binary path, got {type(binaries_raw).__name__}."
            )
        for plat, bin_path in binaries_raw.items():
            if plat not in VALID_PLATFORMS:
                raise ManifestError(
                    f"gaia-agent.yaml{where}: cpp.binaries has unknown platform "
                    f"{plat!r}. Valid platforms: {_sorted(VALID_PLATFORMS)}."
                )
            if not _nonempty_str(bin_path):
                raise ManifestError(
                    f"gaia-agent.yaml{where}: cpp.binaries[{plat!r}] must be a "
                    f"non-empty path string."
                )
            binaries[plat] = bin_path
    return CppConfig(
        binaries=binaries,
        static_linked=_parse_bool(
            data.get("static_linked"), "cpp.static_linked", where, False
        ),
    )
