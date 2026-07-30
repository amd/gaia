# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The permission-truth check: does the code do what the manifest says it does?

``metadata.gaia.permissions`` is the promise a skill makes to whoever installs
it. This module diffs that promise against the domains the code actually
touches, as observed by :mod:`gaia.skills.audit.code`. A mismatch is a hard
finding — a skill that quietly reaches a domain it never declared has
misrepresented itself, and the declaration is what the install-time consent
prompt and the Phase 2 sandbox will both be built on.

Four kinds of mismatch, all ``high``:

- **undeclared** — the code touches a domain absent from ``permissions``.
- **insufficient level** — it declared ``read`` and performs a write.
- **denied but used** — it declared ``<domain>:none`` and uses the domain anyway.
- **scope violation** — it declared a scope and writes to a literal path outside it.

Over-declaration (declared, never used) is the one advisory case: it is sloppy
rather than deceptive, and asking for less capability than you declared cannot
harm the user.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterable, Optional, Sequence

from gaia.skills.audit.code import CodeAnalysis, DomainUse
from gaia.skills.audit.findings import CATEGORY_PERMISSION_TRUTH, Finding
from gaia.skills.errors import FORMAT_DOCS_URL
from gaia.skills.permissions import Permission

#: Capability ordering within a domain: a higher rank subsumes a lower one, so
#: declaring ``write`` covers a read but not the other way round.
LEVEL_RANK: dict[str, int] = {
    "none": 0,
    "read": 1,
    "connect": 1,
    "control": 1,
    "execute": 1,
    "write": 2,
}

_PERMISSION_DOCS = f"{FORMAT_DOCS_URL}#permission-model"


def _rank(level: str) -> int:
    return LEVEL_RANK.get(level, 1)


def _finding(
    rule_id: str,
    severity: str,
    message: str,
    use: DomainUse,
    remediation: str,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        category=CATEGORY_PERMISSION_TRUTH,
        message=message,
        file=use.file,
        line=use.line,
        remediation=remediation,
        snippet=use.detail,
    )


def _scopes_for(permissions: Sequence[Permission], domain: str) -> list[str]:
    return [p.scope for p in permissions if p.domain == domain and p.scope]


def _within_scope(target: str, scopes: Iterable[str]) -> bool:
    """True when ``target`` falls under any declared scope.

    Both sides are normalized as POSIX paths. A ``*`` scope matches anything;
    otherwise the target must be at or under the scope prefix.
    """
    normalized = PurePosixPath(target.replace("\\", "/")).as_posix().lstrip("./")
    for scope in scopes:
        if scope == "*":
            return True
        candidate = PurePosixPath(scope.replace("\\", "/")).as_posix().lstrip("./")
        if normalized == candidate or normalized.startswith(candidate.rstrip("/") + "/"):
            return True
    return False


def _is_absolute_target(target: str) -> bool:
    """Only confident cases are scope-checked: absolute or home-relative paths."""
    return target.startswith(("/", "~")) or (len(target) > 2 and target[1] == ":")


def check_permission_truth(
    declared: Sequence[Permission], analysis: CodeAnalysis
) -> tuple[Finding, ...]:
    """Diff declared permissions against the domains the code touches."""
    findings: list[Finding] = []
    by_domain: dict[str, list[Permission]] = {}
    for permission in declared:
        by_domain.setdefault(permission.domain, []).append(permission)

    # --- what the code does but the manifest does not admit ---------------
    reported: set[tuple[str, str, str, int]] = set()
    for use in analysis.domain_uses:
        key = (use.domain, use.level, use.file, use.line)
        if key in reported:
            continue
        reported.add(key)

        matching = by_domain.get(use.domain, [])
        if not matching:
            findings.append(
                _finding(
                    f"permission.undeclared.{use.domain}",
                    "high",
                    f"Uses the '{use.domain}' domain ({use.detail}) but "
                    f"metadata.gaia.permissions does not declare it.",
                    use,
                    f"Declare it — add '{use.domain}:{use.level}' to "
                    "metadata.gaia.permissions — or remove the call. A skill "
                    "that reaches a domain it never declared has misrepresented "
                    f"itself to whoever installs it. See {_PERMISSION_DOCS}",
                )
            )
            continue

        granted = max(_rank(p.level) for p in matching)
        if granted == 0:
            findings.append(
                _finding(
                    "permission.denied_but_used",
                    "high",
                    f"Declares '{use.domain}:none' but uses the domain anyway "
                    f"({use.detail}).",
                    use,
                    f"An explicit '{use.domain}:none' is a promise not to touch "
                    "the domain. Either remove the call or declare the level "
                    f"the code needs. See {_PERMISSION_DOCS}",
                )
            )
            continue

        if _rank(use.level) > granted:
            declared_levels = ", ".join(sorted({p.level for p in matching}))
            findings.append(
                _finding(
                    "permission.insufficient_level",
                    "high",
                    f"Performs a '{use.level}' on '{use.domain}' ({use.detail}) "
                    f"but only declares '{declared_levels}'.",
                    use,
                    f"Declare '{use.domain}:{use.level}', or change the code to "
                    f"stay within '{declared_levels}'. See {_PERMISSION_DOCS}",
                )
            )
            continue

        findings.extend(_scope_findings(use, _scopes_for(matching, use.domain)))

    findings.extend(_unused_findings(declared, analysis))
    return tuple(findings)


def _scope_findings(use: DomainUse, scopes: Sequence[str]) -> list[Finding]:
    """Flag a literal target outside every declared scope.

    Only literal, absolute targets are checked. A computed path cannot be
    resolved statically, and guessing would produce exactly the false positives
    that make authors stop reading the report.
    """
    target = use.literal_target
    if not scopes or not target or not _is_absolute_target(target):
        return []
    if _within_scope(target, scopes):
        return []
    # The declared scope is public catalog metadata so it can appear in the
    # message; the literal target is extracted from source, so it goes in the
    # snippet only.
    return [
        Finding(
            rule_id="permission.scope_violation",
            severity="high",
            category=CATEGORY_PERMISSION_TRUTH,
            message=(
                f"Targets a path outside the declared '{use.domain}' scope "
                f"({', '.join(scopes)})."
            ),
            file=use.file,
            line=use.line,
            remediation=(
                "Keep the access inside the declared scope, or widen the "
                "declaration to cover it. A scope the code ignores gives the "
                f"installing user a false picture. See {_PERMISSION_DOCS}"
            ),
            snippet=f"{use.detail} -> {target}",
        )
    ]


def _unused_findings(
    declared: Sequence[Permission], analysis: CodeAnalysis
) -> list[Finding]:
    """Advisory: a declared permission the code never exercises."""
    used = analysis.domains()
    findings: list[Finding] = []
    for permission in declared:
        if permission.grants_nothing or permission.domain in used:
            continue
        # 'mcp' is exercised through the connector bridge rather than a call the
        # AST can see, so an unused mcp grant is not evidence of over-declaration.
        if permission.domain == "mcp":
            continue
        findings.append(
            Finding(
                rule_id="permission.unused",
                severity="info",
                category=CATEGORY_PERMISSION_TRUTH,
                message=(
                    f"Declares '{permission}' but no code in the skill uses the "
                    f"'{permission.domain}' domain."
                ),
                file="SKILL.md",
                line=0,
                remediation=(
                    "Drop the declaration. Asking for capability you do not use "
                    "trains users to click through consent prompts, and it will "
                    "widen the sandbox this skill runs in later."
                ),
            )
        )
    return findings


def observed_permission_strings(analysis: CodeAnalysis) -> list[str]:
    """The permission list the observed code would actually need.

    Used by the CLI to print a ready-to-paste ``permissions:`` block.
    """
    strongest: dict[str, str] = {}
    for use in analysis.domain_uses:
        current = strongest.get(use.domain)
        if current is None or _rank(use.level) > _rank(current):
            strongest[use.domain] = use.level
    return [f"{domain}:{level}" for domain, level in sorted(strongest.items())]


def undeclared_domains(
    declared: Sequence[Permission], analysis: CodeAnalysis
) -> list[str]:
    """Domains the code touches that no permission declares at all."""
    declared_domains = {p.domain for p in declared if not p.grants_nothing}
    return sorted(analysis.domains() - declared_domains)


def first_scope(permissions: Sequence[Permission], domain: str) -> Optional[str]:
    """The first declared scope for ``domain``, if any."""
    scopes = _scopes_for(permissions, domain)
    return scopes[0] if scopes else None
