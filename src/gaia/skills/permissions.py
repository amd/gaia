# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The ``<domain>:<level>[:scope]`` permission grammar and its connector bridge.

Two bridges resolve today; everything else is refused rather than loaded
unenforced:

- **Connector-bridged** — ``mcp:connect:<connector-id>`` and
  ``network:<level>[:scope]`` resolve to the existing
  :class:`~gaia.connectors.providers.base.ConnectorRequirement`, the same
  primitive agents already declare via ``REQUIRED_CONNECTORS``.
- **Binary-bridged** — ``shell:execute:<binary>`` grants one named CLI, per
  skill, per agent instance, restricted to the read-only subcommands in
  :data:`gaia.skills.binaries.BINARY_POLICIES`. The scope is mandatory: an
  unscoped ``shell:execute`` asks for the whole shell, which no bridge covers.
- ``filesystem`` / ``database`` / ``desktop`` / ``env`` are local-capability
  domains with no bridge. They need the Phase 2 sandbox, so a skill declaring
  one is **refused**.

``network`` has no catalog connector in Phase 1 (nothing in the connector
catalog represents raw outbound HTTP). Unless the permission scope names a real
catalog connector, the requirement is emitted against the reserved pseudo-id
``network``; it records the declared egress so ``gaia skill info`` and the
Phase 2 egress policy can consume it. Phase 1 declares, it does not enforce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from gaia.connectors.providers.base import ConnectorRequirement
from gaia.logger import get_logger
from gaia.skills.binaries import (
    BINARY_NAME_RE,
    BINARY_POLICIES,
    refuse_unpoliced_binaries,
)
from gaia.skills.errors import (
    FORMAT_DOCS_URL,
    SkillPermissionError,
    SkillValidationError,
)

log = get_logger(__name__)

# Reserved pseudo connector id for declared raw outbound HTTP (see module docstring).
NETWORK_CONNECTOR_ID = "network"

# <domain>: {allowed levels}
DOMAIN_LEVELS: dict[str, frozenset[str]] = {
    "filesystem": frozenset({"read", "write", "none"}),
    "network": frozenset({"read", "write", "none"}),
    "shell": frozenset({"execute", "none"}),
    "mcp": frozenset({"connect", "none"}),
    "env": frozenset({"read", "none"}),
    "database": frozenset({"read", "write", "none"}),
    "desktop": frozenset({"control", "none"}),
}

#: Domains that map onto the connector grant model.
CONNECTOR_BRIDGED_DOMAINS = frozenset({"network", "mcp"})

#: Domains bridged by a scoped, policy-gated binary grant (``shell:execute:gh``).
#: Only meaningful *with* a scope — see :attr:`Permission.is_binary_bridged`.
BINARY_BRIDGED_DOMAINS = frozenset({"shell"})

#: Domains that need the Phase 2 sandbox — refused.
LOCAL_CAPABILITY_DOMAINS = (
    frozenset(DOMAIN_LEVELS) - CONNECTOR_BRIDGED_DOMAINS - BINARY_BRIDGED_DOMAINS
)

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Permission:
    """One parsed ``<domain>:<level>[:scope]`` grant."""

    domain: str
    level: str
    scope: str | None = None

    def __str__(self) -> str:
        base = f"{self.domain}:{self.level}"
        return f"{base}:{self.scope}" if self.scope else base

    @property
    def is_connector_bridged(self) -> bool:
        """True when this domain maps onto the connector grant model."""
        return self.domain in CONNECTOR_BRIDGED_DOMAINS

    @property
    def is_binary_bridged(self) -> bool:
        """True for ``shell:execute:<binary>`` — one named, policy-gated CLI."""
        return (
            self.domain in BINARY_BRIDGED_DOMAINS
            and self.level == "execute"
            and bool(self.scope)
        )

    @property
    def grants_nothing(self) -> bool:
        """True for an explicit ``<domain>:none`` denial — inert either way."""
        return self.level == "none"

    @property
    def is_local_capability(self) -> bool:
        """True when this needs the (deferred) Phase 2 sandbox.

        ``<domain>:none`` is excluded: an explicit denial asks for no capability,
        so refusing it would reject a skill for declaring *less* than the default.
        A bare ``shell:execute`` counts: only a *scoped* binary grant is bridged,
        and asking for the whole shell is asking for the sandbox.
        """
        if self.grants_nothing:
            return False
        if self.domain in BINARY_BRIDGED_DOMAINS:
            return not self.is_binary_bridged
        return self.domain in LOCAL_CAPABILITY_DOMAINS

    @classmethod
    def parse(cls, raw: str, *, skill_name: str = "<unknown>") -> "Permission":
        """Parse one permission string, failing loudly on any malformed part."""
        if not isinstance(raw, str) or not raw.strip():
            raise SkillValidationError(
                f"Skill '{skill_name}' declares an empty permission. "
                "Each entry of metadata.gaia.permissions must be a "
                "'<domain>:<level>[:scope]' string, e.g. 'network:read:*.brave.com'. "
                f"Grammar: {FORMAT_DOCS_URL}#permission-model"
            )

        parts = raw.strip().split(":", 2)
        if len(parts) < 2:
            raise SkillValidationError(
                f"Skill '{skill_name}' declares permission {raw!r}, which is missing "
                "its level. Use '<domain>:<level>[:scope]', e.g. 'network:read' or "
                f"'mcp:connect:mcp-tavily'. Grammar: {FORMAT_DOCS_URL}#permission-model"
            )

        domain, level = parts[0].strip(), parts[1].strip()
        scope = parts[2].strip() if len(parts) == 3 and parts[2].strip() else None

        if domain not in DOMAIN_LEVELS:
            raise SkillValidationError(
                f"Skill '{skill_name}' declares permission {raw!r} with unknown domain "
                f"{domain!r}. Valid domains: {', '.join(sorted(DOMAIN_LEVELS))}. "
                f"Grammar: {FORMAT_DOCS_URL}#permission-model"
            )
        if not _TOKEN_RE.match(level) or level not in DOMAIN_LEVELS[domain]:
            raise SkillValidationError(
                f"Skill '{skill_name}' declares permission {raw!r} with level "
                f"{level!r}, which {domain!r} does not define. Valid levels for "
                f"{domain!r}: {', '.join(sorted(DOMAIN_LEVELS[domain]))}. "
                f"Grammar: {FORMAT_DOCS_URL}#permission-model"
            )

        if domain in BINARY_BRIDGED_DOMAINS and level == "execute":
            _validate_binary_scope(scope, raw=raw, skill_name=skill_name)

        return cls(domain=domain, level=level, scope=scope)


def _validate_binary_scope(scope: str | None, *, raw: str, skill_name: str) -> None:
    """A ``shell:execute`` scope, when present, must name one bare binary.

    A missing scope is not a *grammar* error — the grammar makes scope optional —
    so it is refused later, by :func:`refuse_unbridged_permissions`, alongside the
    other capabilities no bridge covers. A path-shaped scope is malformed here:
    it would point the grant at something other than the binary it documents.
    """
    if not scope:
        return
    if not BINARY_NAME_RE.match(scope):
        raise SkillValidationError(
            f"Skill '{skill_name}' declares {raw!r}, whose scope {scope!r} is not a "
            "bare executable name. Name the command as it is typed — 'gh', not a "
            "path, a flag, or a full command line. "
            f"Grammar: {FORMAT_DOCS_URL}#permission-model"
        )


def parse_permissions(
    raw: Iterable[str], *, skill_name: str = "<unknown>"
) -> list[Permission]:
    """Parse every permission string of a skill, preserving declaration order."""
    return [Permission.parse(entry, skill_name=skill_name) for entry in raw]


def refuse_unbridged_permissions(
    permissions: Sequence[Permission], *, skill_name: str
) -> None:
    """Refuse a skill that asks for a capability this runtime cannot enforce.

    The single chokepoint: install, publish, migrate, ``register_skill_tools``,
    and ``Agent.load_skill`` all funnel through here, so a capability refused
    here is refused everywhere.

    Raises:
        SkillPermissionError: for a local-capability domain, a bare
            ``shell:execute``, or a binary grant with no read-only policy.
    """
    refuse_unpoliced_binaries(permissions, skill_name=skill_name)

    unbridged = [p for p in permissions if p.is_local_capability]
    if not unbridged:
        return

    declared = ", ".join(str(p) for p in unbridged)
    if any(p.domain in BINARY_BRIDGED_DOMAINS for p in unbridged):
        raise SkillPermissionError(
            f"Skill '{skill_name}' declares {declared}, which asks for the whole "
            "shell. GAIA grants shell access one binary at a time: scope it to the "
            "CLI the skill actually runs, e.g. 'shell:execute:gh'. Declarable "
            f"binaries: {', '.join(sorted(BINARY_POLICIES)) or '(none)'}. The "
            "sandbox that would contain unrestricted shell is deferred to a later "
            f"phase. See {FORMAT_DOCS_URL}#permission-model and "
            "https://github.com/amd/gaia/issues/1019 (Phase 2)."
        )
    raise SkillPermissionError(
        f"Skill '{skill_name}' declares local-capability permission(s): {declared}. "
        "GAIA's skills runtime bridges connector-backed permissions "
        f"({', '.join(sorted(CONNECTOR_BRIDGED_DOMAINS))}) and scoped binary grants "
        "(shell:execute:<binary>); the sandbox that enforces "
        f"{', '.join(sorted(LOCAL_CAPABILITY_DOMAINS))} is deferred to a later phase, "
        "so this skill is refused rather than loaded without enforcement. "
        "To load it now, drop those permissions and use the agent's own tools for "
        "local access, or wait for the permission sandbox. "
        f"See {FORMAT_DOCS_URL}#permission-model and "
        "https://github.com/amd/gaia/issues/1019 (Phase 2)."
    )


def to_connector_requirement(
    permission: Permission,
    *,
    skill_name: str,
    catalog_ids: Sequence[str] | None = None,
) -> ConnectorRequirement | None:
    """Resolve one connector-bridged permission to a ``ConnectorRequirement``.

    Args:
        permission: A parsed permission. Local-capability domains return ``None``
            (they are refused earlier by :func:`refuse_unbridged_permissions`).
        skill_name: Used in the requirement's ``reason`` and in error messages.
        catalog_ids: Known connector ids. Defaults to the live connector catalog.

    Returns:
        The requirement, or ``None`` for ``<domain>:none`` and non-bridged domains.

    Raises:
        SkillValidationError: if an ``mcp:connect`` permission does not name a
            connector present in the catalog.
    """
    if permission.level == "none" or not permission.is_connector_bridged:
        return None

    if catalog_ids is None:
        catalog_ids = _catalog_ids()

    if permission.domain == "mcp":
        if not permission.scope:
            raise SkillValidationError(
                f"Skill '{skill_name}' declares 'mcp:connect' without naming a "
                "connector. Scope it to a connector id, e.g. "
                "'mcp:connect:mcp-tavily'. Available connectors: "
                f"{', '.join(sorted(catalog_ids)) or '(catalog unavailable)'}. "
                "Run 'gaia connectors list' to see them."
            )
        if permission.scope not in catalog_ids:
            raise SkillValidationError(
                f"Skill '{skill_name}' declares 'mcp:connect:{permission.scope}', but "
                f"no connector with id {permission.scope!r} exists in the catalog. "
                f"Available: {', '.join(sorted(catalog_ids)) or '(catalog unavailable)'}. "
                "Run 'gaia connectors list' to see them."
            )
        return ConnectorRequirement(
            connector_id=permission.scope,
            scopes=(),
            reason=f"Skill '{skill_name}' connects to the '{permission.scope}' MCP server.",
        )

    # network:<level>[:scope]
    scope = permission.scope or "*"
    if permission.scope and permission.scope in catalog_ids:
        return ConnectorRequirement(
            connector_id=permission.scope,
            scopes=(permission.level,),
            reason=f"Skill '{skill_name}' needs {permission.level} access via "
            f"the '{permission.scope}' connector.",
        )

    log.debug(
        "Skill '%s': network permission %s has no catalog connector; recording it "
        "against the reserved '%s' pseudo-connector (declaration only — Phase 1 "
        "does not enforce egress).",
        skill_name,
        permission,
        NETWORK_CONNECTOR_ID,
    )
    return ConnectorRequirement(
        connector_id=NETWORK_CONNECTOR_ID,
        scopes=(f"{permission.level}:{scope}",),
        reason=f"Skill '{skill_name}' declares {permission.level} network access to {scope}.",
    )


def connector_requirements(
    permissions: Sequence[Permission],
    *,
    skill_name: str,
    catalog_ids: Sequence[str] | None = None,
) -> list[ConnectorRequirement]:
    """Resolve every bridged permission to a de-duplicated requirement list."""
    if catalog_ids is None:
        catalog_ids = _catalog_ids()

    resolved: list[ConnectorRequirement] = []
    for permission in permissions:
        requirement = to_connector_requirement(
            permission, skill_name=skill_name, catalog_ids=catalog_ids
        )
        if requirement is not None and requirement not in resolved:
            resolved.append(requirement)
    return resolved


def _catalog_ids() -> tuple[str, ...]:
    """Return every connector id in the live catalog.

    Imports ``gaia.connectors.catalog`` for its side effect — that module is
    what populates the process-level ``REGISTRY`` singleton.
    """
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY

    return tuple(spec.id for spec in REGISTRY.all())
