# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The pre-publish security-audit gate — the client half of #2468.

``gaia skill publish`` must not put a skill in the catalog that nobody scanned.
The **analysis engine** — the scanner that reads ``tools.py``/``scripts/`` for
dangerous code and the instruction body for prompt injection — lives in
:mod:`gaia.skills.audit`, not here. This module is the call site: it locates that
engine, invokes it, and turns its verdict into a publish decision.

**When the engine is absent, publish fails.** It does not proceed "unaudited", and
it does not stamp a synthetic ALLOW. That would be precisely the silent fallback
CLAUDE.md prohibits: the report is what the hub Worker gates ``community`` and
``verified`` on (``workers/agent-hub/src/audit.ts``), so fabricating one would
launder an unscanned skill past a gate designed to stop it. The error names the
issue, the expected symbol, and the two legitimate ways forward.

**The contract this module expects of the engine**::

    from gaia.skills.audit import audit_skill
    report = audit_skill(directory)   # -> object/dict with .verdict/.engine/.findings

``verdict`` is the governance vocabulary (``ALLOW``/``REVIEW``/``BLOCK``,
``DecisionType`` in ``gaia/governance/schemas.py``) that ``audit.ts`` also
consumes. The lookup is by name, so if that symbol is ever renamed this module is
the one place to adapt — and until it is, every publish attempt says so out loud.

A CI job that already produced a report can pass it through with
``--audit-report <path>`` and never touch the engine at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from gaia.logger import get_logger
from gaia.skills.errors import SkillError

log = get_logger(__name__)

#: Module and callable the audit engine (#2468) is expected to expose.
AUDIT_MODULE = "gaia.skills.audit"
AUDIT_FUNCTION = "audit_skill"

AUDIT_ISSUE_URL = "https://github.com/amd/gaia/issues/2468"

#: Verdicts the Worker's gate understands.
VERDICT_ALLOW = "ALLOW"
VERDICT_REVIEW = "REVIEW"
VERDICT_BLOCK = "BLOCK"
VALID_VERDICTS = frozenset({VERDICT_ALLOW, VERDICT_REVIEW, VERDICT_BLOCK})

#: Keys that tie a verdict to the skill, version, tier, and bytes it was earned
#: on. The Worker refuses a gated tier whose report omits them, so they must be
#: forwarded verbatim rather than normalized away.
BINDING_FIELDS = (
    "skill",
    "version",
    "security_tier",
    "cleared_tiers",
    "content_digest",
    "manifest_digest",
)


class SkillAuditUnavailableError(SkillError):
    """The audit engine (#2468) is not installed, so publish cannot be gated.

    Distinct from :class:`SkillAuditFailedError`: this means the scan never ran,
    not that it ran and objected.
    """


class SkillAuditFailedError(SkillError):
    """The audit ran and refused the skill (``BLOCK``) or held it (``REVIEW``)."""


@dataclass(frozen=True)
class AuditReport:
    """A normalized audit result, in the shape ``POST /publish/skill`` accepts.

    Serialized by :meth:`to_json` and sent verbatim as the ``audit`` form part.
    ``workers/agent-hub/src/audit.ts`` parses the four verdict keys below and,
    for any tier whose gate demands an audit, additionally requires the
    :data:`BINDING_FIELDS` carried in :attr:`binding` — dropping them here would
    turn every ``community``/``verified`` publish into a 428.
    """

    verdict: str
    engine: str
    audited_at: str
    findings: list[Any] = field(default_factory=list)
    #: Engine-supplied keys that bind the verdict to what it audited.
    binding: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.verdict == VERDICT_ALLOW

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "verdict": self.verdict,
            "engine": self.engine,
            "audited_at": self.audited_at,
            "findings": list(self.findings),
        }
        payload.update(self.binding)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_obj(cls, raw: Any, *, where: str) -> "AuditReport":
        """Normalize whatever the engine returned (dataclass, object, or dict).

        Accepting all three keeps this module from dictating #2468's return type,
        but every required field is still validated — a report GAIA cannot read is
        an error, never an implied pass.
        """
        data = _wire_dict(raw)

        verdict = str(data.get("verdict") or "").upper()
        if verdict not in VALID_VERDICTS:
            raise SkillAuditUnavailableError(
                f"The security audit report from {where} has verdict "
                f"{data.get('verdict')!r}; expected one of "
                f"{', '.join(sorted(VALID_VERDICTS))}. GAIA will not publish on an "
                f"unreadable verdict. See {AUDIT_ISSUE_URL}"
            )

        engine = str(data.get("engine") or "").strip()
        if not engine:
            raise SkillAuditUnavailableError(
                f"The security audit report from {where} does not name the engine "
                "that produced it. The hub rejects an unattributed verdict "
                "(HTTP 400 invalid_audit_report), so publish stops here. Set "
                f"'engine' to '<engine-id>/<version>'. See {AUDIT_ISSUE_URL}"
            )

        audited_at = str(data.get("audited_at") or "").strip()
        if not audited_at:
            audited_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            log.warning(
                "Audit report from %s has no 'audited_at'; stamping now (%s)",
                where,
                audited_at,
            )

        findings = data.get("findings") or []
        if not isinstance(findings, (list, tuple)):
            raise SkillAuditUnavailableError(
                f"The security audit report from {where} has non-list 'findings'. "
                f"The hub rejects that shape. See {AUDIT_ISSUE_URL}"
            )

        return cls(
            verdict=verdict,
            engine=engine,
            audited_at=audited_at,
            findings=[_finding_dict(f) for f in findings],
            binding={
                key: data[key] for key in BINDING_FIELDS if data.get(key) is not None
            },
        )


def _wire_dict(raw: Any) -> dict[str, Any]:
    """The engine's own ``to_dict()`` is already the wire payload — prefer it.

    Falling back to attribute-scraping would leave `Finding` dataclasses in
    ``findings``, which :meth:`AuditReport.to_json` cannot serialize.
    """
    if isinstance(raw, dict):
        return raw
    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return _attrs_of(raw)


def _finding_dict(finding: Any) -> Any:
    to_dict = getattr(finding, "to_dict", None)
    return to_dict() if callable(to_dict) else finding


def _attrs_of(raw: Any) -> dict[str, Any]:
    return {
        key: getattr(raw, key, None)
        for key in ("verdict", "engine", "audited_at", "findings") + BINDING_FIELDS
    }


def load_audit_report(path: Path) -> AuditReport:
    """Read a report the caller already has (``--audit-report``)."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillAuditUnavailableError(
            f"Could not read the audit report at {path}: {exc}. Point "
            "--audit-report at the JSON emitted by 'gaia skill audit', or drop the "
            f"flag to run the audit now. See {AUDIT_ISSUE_URL}"
        ) from exc
    return AuditReport.from_obj(raw, where=str(path))


def audit_engine() -> Any:
    """Return the #2468 audit callable, or fail loudly explaining what is missing.

    Raises:
        SkillAuditUnavailableError: the engine module or symbol is absent.
    """
    import importlib

    try:
        module = importlib.import_module(AUDIT_MODULE)
    except ImportError as exc:
        raise SkillAuditUnavailableError(
            f"The pre-publish security audit is required, but the audit engine "
            f"({AUDIT_MODULE}) is not installed, so this skill cannot be scanned. "
            "GAIA refuses to publish an un-audited skill — the hub gates the "
            "'community' and 'verified' tiers on this report, and a skill that "
            "skipped the scan must not reach the catalog. Either: (1) upgrade to a "
            f"GAIA build that ships the audit engine (issue #2468), or (2) run the "
            "audit elsewhere and pass its JSON with "
            f"'gaia skill publish --audit-report <file>'. See {AUDIT_ISSUE_URL}"
        ) from exc

    engine = getattr(module, AUDIT_FUNCTION, None)
    if not callable(engine):
        raise SkillAuditUnavailableError(
            f"{AUDIT_MODULE} is installed but exposes no callable "
            f"{AUDIT_FUNCTION}(directory), so the audit cannot run and publish is "
            "refused rather than proceeding un-audited. The audit engine's entry "
            f"point may have been renamed — update {__name__}.{AUDIT_FUNCTION} to "
            f"match, or pass a report with --audit-report. See {AUDIT_ISSUE_URL}"
        )
    return engine


def run_audit(directory: Path) -> AuditReport:
    """Scan *directory* with the #2468 engine and return its normalized report.

    Raises:
        SkillAuditUnavailableError: the engine is absent, or its report is
            unreadable.
    """
    engine = audit_engine()
    log.info("Running the pre-publish security audit on %s", directory)
    try:
        raw = engine(Path(directory))
    except SkillError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised with context below
        raise SkillAuditUnavailableError(
            f"The security audit engine ({AUDIT_MODULE}.{AUDIT_FUNCTION}) raised "
            f"{exc.__class__.__name__}: {exc}. Publish is refused — an audit that "
            f"crashed is not an audit that passed. See {AUDIT_ISSUE_URL}"
        ) from exc
    return AuditReport.from_obj(raw, where=f"{AUDIT_MODULE}.{AUDIT_FUNCTION}")


def assert_gate_cleared(report: AuditReport, *, skill_name: str) -> AuditReport:
    """Refuse publish unless the audit verdict is ``ALLOW``.

    The Worker enforces the same rule server-side (403 ``audit_blocked`` / 409
    ``audit_review_required``); checking here means the publisher sees the reason
    before uploading anything.

    Raises:
        SkillAuditFailedError: on ``BLOCK`` or ``REVIEW``.
    """
    if report.verdict == VERDICT_BLOCK:
        raise SkillAuditFailedError(
            f"The security audit BLOCKED skill '{skill_name}' "
            f"({len(report.findings)} finding(s), engine {report.engine}). Fix the "
            "findings and re-audit — a blocked skill cannot be published. Findings: "
            f"{_summarize(report.findings)}. See {AUDIT_ISSUE_URL}"
        )
    if report.verdict == VERDICT_REVIEW:
        raise SkillAuditFailedError(
            f"The security audit returned REVIEW for skill '{skill_name}' "
            f"({len(report.findings)} finding(s), engine {report.engine}); it needs "
            "maintainer sign-off before publish. Resolve the findings to reach "
            f"ALLOW, or request review. Findings: {_summarize(report.findings)}. "
            f"See {AUDIT_ISSUE_URL}"
        )
    return report


def _summarize(findings: list[Any], limit: int = 5) -> str:
    if not findings:
        return "(none reported)"
    shown = [str(f) for f in findings[:limit]]
    if len(findings) > limit:
        shown.append(f"(+{len(findings) - limit} more)")
    return "; ".join(shown)


def gate_for_publish(
    directory: Path, *, skill_name: str, report_path: Optional[Path] = None
) -> AuditReport:
    """The full gate: obtain a report and refuse unless it says ``ALLOW``.

    Args:
        directory: The skill source directory to scan.
        skill_name: Named in errors.
        report_path: A pre-computed report to use instead of running the engine.
    """
    report = (
        load_audit_report(report_path)
        if report_path is not None
        else run_audit(directory)
    )
    return assert_gate_cleared(report, skill_name=skill_name)
