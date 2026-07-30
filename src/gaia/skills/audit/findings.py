# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The audit report data model — findings, severities, and the wire shape.

``AuditReport.to_dict()`` is the contract the hub Worker's
``parseAuditReport()`` (``workers/agent-hub/src/audit.ts``) consumes. It
requires ``verdict`` / ``engine`` / ``audited_at`` / ``findings``; the extra
``skill`` / ``version`` / ``security_tier`` / ``cleared_tiers`` /
``content_digest`` fields exist so a verdict is **bound to the artifact and the
tier it was produced for** — without them an ALLOW earned as ``experimental``
for v1.0.0 could be replayed to publish v1.1.0 as ``verified``.

Exploitable detail (``Finding.snippet``) is withheld from every payload unless
a caller explicitly opts in, per CLAUDE.md's security-disclosure policy.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Sequence

from gaia.governance.schemas import DecisionType, GovernanceDecision
from gaia.skills.format import SKILL_FILENAME, SKILL_TOOLS_FILENAME

#: Engine id + version. Bumped whenever a rule change can move a verdict, so a
#: recorded catalog entry always names the engine that produced it.
AUDIT_ENGINE = "gaia-skill-audit/0.1.0"

Severity = Literal["info", "low", "medium", "high", "critical"]

#: Ascending order — index is the rank, so ``max`` picks the worst.
SEVERITY_ORDER: tuple[Severity, ...] = ("info", "low", "medium", "high", "critical")

#: Finding categories, one per analyzer.
CATEGORY_DANGEROUS_CALL = "dangerous-call"
CATEGORY_PERMISSION_TRUTH = "permission-truth"
CATEGORY_SUPPLY_CHAIN = "supply-chain"
CATEGORY_PROMPT_INJECTION = "prompt-injection"
CATEGORY_TIER_CLAIM = "tier-claim"

CATEGORIES = (
    CATEGORY_DANGEROUS_CALL,
    CATEGORY_PERMISSION_TRUTH,
    CATEGORY_SUPPLY_CHAIN,
    CATEGORY_PROMPT_INJECTION,
    CATEGORY_TIER_CLAIM,
)

#: Directory / file names excluded from the digest and from analysis. Build
#: caches must not invalidate an otherwise-valid audit report.
DIGEST_EXCLUDED_DIRS = frozenset(
    {"__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules"}
)
DIGEST_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".zip"})


def worst_severity(findings: Iterable["Finding"]) -> Optional[Severity]:
    """Return the highest severity among ``findings``, or ``None`` if empty."""
    severities = [f.severity for f in findings]
    if not severities:
        return None
    return max(severities, key=SEVERITY_ORDER.index)


@dataclass(frozen=True)
class Finding:
    """One audit finding, addressed to the author who has to fix it."""

    rule_id: str
    severity: Severity
    category: str
    message: str
    #: Path relative to the skill directory. Empty for manifest-level findings.
    file: str = ""
    #: 1-indexed source line; 0 when the finding is not line-addressable.
    line: int = 0
    remediation: str = ""
    #: The offending source text. Withheld unless a caller opts in.
    snippet: Optional[str] = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(
                f"Finding {self.rule_id!r} has severity {self.severity!r}; expected "
                f"one of {', '.join(SEVERITY_ORDER)}."
            )
        if self.category not in CATEGORIES:
            raise ValueError(
                f"Finding {self.rule_id!r} has category {self.category!r}; expected "
                f"one of {', '.join(CATEGORIES)}."
            )

    @property
    def location(self) -> str:
        """``file:line`` for display, or ``<manifest>`` when not line-addressed."""
        if not self.file:
            return "<manifest>"
        return f"{self.file}:{self.line}" if self.line else self.file

    def to_dict(self, *, include_snippets: bool = False) -> dict[str, Any]:
        """Serialize; the snippet is opt-in (security-disclosure policy)."""
        payload: dict[str, Any] = {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "remediation": self.remediation,
        }
        if include_snippets and self.snippet is not None:
            payload["snippet"] = self.snippet
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            rule_id=data["rule_id"],
            severity=data["severity"],
            category=data["category"],
            message=data["message"],
            file=data.get("file", ""),
            line=int(data.get("line", 0)),
            remediation=data.get("remediation", ""),
            snippet=data.get("snippet"),
        )


@dataclass(frozen=True)
class AuditReport:
    """The verdict for one skill at one version, bound to the bytes audited."""

    skill: str
    security_tier: str
    verdict: DecisionType
    reason: str
    version: Optional[str] = None
    findings: tuple[Finding, ...] = ()
    #: Every tier whose gate these findings clear. Empty means none.
    cleared_tiers: tuple[str, ...] = ()
    content_digest: str = ""
    audited_at: str = field(default_factory=lambda: _utc_now_iso())
    engine: str = AUDIT_ENGINE

    # ------------------------------------------------------------------
    # Derived views
    # ------------------------------------------------------------------

    @property
    def worst(self) -> Optional[Severity]:
        """Highest severity found, or ``None`` for a clean skill."""
        return worst_severity(self.findings)

    @property
    def is_clean(self) -> bool:
        """True when the scan produced no findings at all."""
        return not self.findings

    def counts_by_severity(self) -> dict[str, int]:
        """Finding count per severity, worst first, omitting empty buckets."""
        return {
            severity: sum(1 for f in self.findings if f.severity == severity)
            for severity in reversed(SEVERITY_ORDER)
            if any(f.severity == severity for f in self.findings)
        }

    def findings_by_category(self, category: str) -> tuple[Finding, ...]:
        """Every finding in one category, in discovery order."""
        return tuple(f for f in self.findings if f.category == category)

    @property
    def rule_ids(self) -> list[str]:
        """Distinct rule ids in first-seen order."""
        seen: list[str] = []
        for finding in self.findings:
            if finding.rule_id not in seen:
                seen.append(finding.rule_id)
        return seen

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self, *, include_snippets: bool = False) -> dict[str, Any]:
        """The payload the hub Worker's ``audit`` form part carries."""
        return {
            # Required by audit.ts parseAuditReport().
            "verdict": self.verdict,
            "engine": self.engine,
            "audited_at": self.audited_at,
            "findings": [
                f.to_dict(include_snippets=include_snippets) for f in self.findings
            ],
            # Binds the verdict to what was audited (replay defense).
            "skill": self.skill,
            "version": self.version,
            "security_tier": self.security_tier,
            "cleared_tiers": list(self.cleared_tiers),
            "content_digest": self.content_digest,
            # Human-facing summary.
            "reason": self.reason,
            "counts": self.counts_by_severity(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditReport":
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            skill=data["skill"],
            security_tier=data["security_tier"],
            verdict=data["verdict"],
            reason=data.get("reason", ""),
            version=data.get("version"),
            findings=tuple(Finding.from_dict(f) for f in data.get("findings", [])),
            cleared_tiers=tuple(data.get("cleared_tiers", [])),
            content_digest=data.get("content_digest", ""),
            audited_at=data["audited_at"],
            engine=data.get("engine", AUDIT_ENGINE),
        )

    def to_governance_decision(self) -> GovernanceDecision:
        """Project onto the shared governance decision type."""
        return GovernanceDecision(
            decision=self.verdict,
            reason=self.reason,
            policy_version=self.engine,
            rule_ids=self.rule_ids,
            metadata={
                "skill": self.skill,
                "version": self.version,
                "security_tier": self.security_tier,
                "cleared_tiers": list(self.cleared_tiers),
                "content_digest": self.content_digest,
                "counts": self.counts_by_severity(),
            },
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Content digest
# ----------------------------------------------------------------------


def auditable_files(directory: Path | str) -> list[Path]:
    """Every file the audit reads, sorted by relative POSIX path.

    Build caches and binary artifacts are excluded so they can never
    invalidate an otherwise-valid report.
    """
    directory = Path(directory)
    files: list[Path] = []
    for path in directory.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(directory)
        if any(part in DIGEST_EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix in DIGEST_EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(directory).as_posix())


def content_digest(directory: Path | str) -> str:
    """A stable ``sha256:<hex>`` over a skill directory's audited bytes.

    One record per file: the relative POSIX path, a NUL separator, the file's
    length, another NUL, then the bytes. Including the path means a rename is a
    different digest; including the length keeps concatenation unambiguous.
    The hub Worker mirrors this algorithm to detect a report replayed against
    different content.
    """
    directory = Path(directory)
    digest = hashlib.sha256()
    for path in auditable_files(directory):
        payload = path.read_bytes()
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\x00")
        digest.update(payload)
    return f"sha256:{digest.hexdigest()}"


def python_sources(directory: Path | str) -> list[Path]:
    """The Python files the code analyzer inspects: ``tools.py`` + ``scripts/``.

    Any other ``*.py`` in the skill directory is included too — a skill that
    hides code in ``helper.py`` must not escape the scan.
    """
    directory = Path(directory)
    return [p for p in auditable_files(directory) if p.suffix == ".py"]


def relative_path(path: Path, directory: Path) -> str:
    """``path`` relative to the skill directory as a POSIX string."""
    try:
        return path.relative_to(directory).as_posix()
    except ValueError:  # pragma: no cover - callers always pass a child path
        return path.name


def declared_file_names() -> Sequence[str]:
    """The two filenames the format itself defines (for error messages)."""
    return (SKILL_FILENAME, SKILL_TOOLS_FILENAME)
