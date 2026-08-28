# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Read ``skill-lock.json`` back: detect drift between it and the skills on disk.

:mod:`gaia.skills.lock` records what ``gaia skill install`` resolved, verified,
and enforced. Nothing re-read it, which made the lock decoration: after install,
a skill directory can be swapped, downgraded, or hand-edited and the loader would
still hand its instructions to the model and import its ``tools.py`` — under the
tier badge the *original* bytes earned. This module closes that.

Four things are compared, per skill in the user root:

=============  =====================================================
``version``     ``SKILL.md``'s version vs. the resolved one
``tier``        ``SKILL.md``'s ``security_tier`` vs. the enforced one
``content``     the directory's digest vs. the one recorded at install
presence        locked-but-gone, and on-disk-but-untracked
=============  =====================================================

The digest is :func:`gaia.skills.audit.findings.content_digest` — the same
algorithm the audit engine and the hub Worker use, so a skill has exactly one
content identity across audit, publish, and install.

**Drift is fatal for a signature-backed skill and a warning otherwise.** A
``community`` / ``verified`` install means a signature over those exact bytes was
verified and a permission ceiling applied to that exact manifest; different bytes
under the same badge defeat both, so :func:`assert_no_fatal_drift` refuses the
load. An ``experimental`` or locally-authored skill was never attested — the user
already accepted unverified code, and editing a skill you wrote is the normal
workflow — so those report and keep working.

**What this does not defend against.** ``skill-lock.json`` is a plain file in the
user's own config directory with no integrity protection. An attacker who can
write to it can rewrite a digest as easily as a skill. The threat this addresses
is tampering with the *skill directory* — a swapped bundle, an edited manifest, a
downgraded version — which is the realistic case and, until now, entirely silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from gaia.logger import get_logger
from gaia.skills.audit.findings import content_digest
from gaia.skills.errors import SkillDriftError, SkillError
from gaia.skills.format import SKILL_FILENAME, Skill, parse_skill_metadata
from gaia.skills.lock import (
    LOCK_FILENAME,
    SOURCE_HUB,
    SOURCE_LOCAL,
    LockEntry,
    SkillLock,
    lock_path,
)
from gaia.skills.tiers import LOWEST_TIER, TIER_ORDER

log = get_logger(__name__)

#: ``SKILL.md`` declares a different version than the lock resolved.
DRIFT_VERSION = "version"
#: ``SKILL.md`` declares a different ``security_tier`` than install enforced.
DRIFT_TIER = "tier"
#: The directory's bytes no longer hash to the recorded digest.
DRIFT_CONTENT = "content"
#: The lock tracks a skill that is no longer in the user root.
DRIFT_MISSING = "missing"
#: A skill directory in the user root that the lock does not track.
DRIFT_UNTRACKED = "untracked"
#: A lock entry written before content digests were recorded — content cannot be
#: checked at all, which is not the same as "checked and clean".
DRIFT_UNRECORDED = "unrecorded"

DRIFT_KINDS = (
    DRIFT_VERSION,
    DRIFT_TIER,
    DRIFT_CONTENT,
    DRIFT_MISSING,
    DRIFT_UNTRACKED,
    DRIFT_UNRECORDED,
)


def _attested(entry: LockEntry) -> bool:
    """Whether the lock says a signature earned this skill more than the floor.

    Only a hub install has an attestation to defeat: a ``local`` entry's tier is
    the author's own claim, never something a signature or a ceiling enforced.
    An unrecognized tier is treated as attested — a lock this build cannot read
    is not a reason to relax the check.
    """
    if entry.source != SOURCE_HUB:
        return False
    tier = entry.installed_tier or LOWEST_TIER
    if tier not in TIER_ORDER:
        return True
    return TIER_ORDER.index(tier) > TIER_ORDER.index(LOWEST_TIER)


@dataclass(frozen=True)
class Drift:
    """One difference between the lock and the disk."""

    skill: str
    kind: str
    expected: str
    actual: str
    #: True when this drift must block loading (see the module docstring).
    fatal: bool
    #: What is wrong and what to do about it, in one sentence each.
    summary: str
    remediation: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "kind": self.kind,
            "expected": self.expected,
            "actual": self.actual,
            "fatal": self.fatal,
            "summary": self.summary,
            "remediation": self.remediation,
            "path": self.path,
        }

    def __str__(self) -> str:
        return f"{self.summary} {self.remediation}"


@dataclass(frozen=True)
class DriftReport:
    """Every drift found under one skills root."""

    root: Path
    lock_file: Path
    drifts: tuple[Drift, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.drifts

    @property
    def fatal(self) -> tuple[Drift, ...]:
        return tuple(d for d in self.drifts if d.fatal)

    @property
    def warnings(self) -> tuple[Drift, ...]:
        return tuple(d for d in self.drifts if not d.fatal)

    def for_skill(self, name: str) -> tuple[Drift, ...]:
        return tuple(d for d in self.drifts if d.skill == name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "lock_file": str(self.lock_file),
            "clean": self.clean,
            "fatal": len(self.fatal),
            "warnings": len(self.warnings),
            "drifts": [d.to_dict() for d in self.drifts],
        }

    def render(self) -> str:
        """A human-readable report; empty string when there is no drift."""
        if self.clean:
            return ""
        lines = []
        for name in sorted({d.skill for d in self.drifts}):
            lines.append(f"{name}")
            for drift in self.for_skill(name):
                marker = "✗" if drift.fatal else "⚠"
                lines.append(f"  {marker} [{drift.kind}] {drift.summary}")
                lines.append(f"      → {drift.remediation}")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------


def _read_metadata(directory: Path) -> tuple[Optional[Skill], str]:
    """Parse a skill directory's front matter. Returns ``(skill, error)``."""
    try:
        return parse_skill_metadata(directory), ""
    except SkillError as exc:
        return None, str(exc)


def _version_drift(entry: LockEntry, skill: Skill, directory: Path) -> Optional[Drift]:
    actual = skill.version or ""
    if not entry.version or actual == entry.version:
        return None
    fatal = _attested(entry)
    return Drift(
        skill=entry.name,
        kind=DRIFT_VERSION,
        expected=entry.version,
        actual=actual or "(unversioned)",
        fatal=fatal,
        summary=(
            f"{LOCK_FILENAME} records version {entry.version}, but the installed "
            f"{SKILL_FILENAME} declares {actual or '(unversioned)'}."
        ),
        remediation=_remediation(entry, fatal),
        path=str(directory),
    )


def _tier_drift(entry: LockEntry, skill: Skill, directory: Path) -> Optional[Drift]:
    """Compare the enforced tier against the one the manifest now claims.

    Hub entries only: ``installed_tier`` is the ``min(claimed, attested)``
    decision install made, so any disagreement means the manifest was rewritten
    afterwards. A local entry has no enforced tier to disagree with — its tier
    change shows up as content drift, which is what it is.
    """
    if entry.source != SOURCE_HUB or not entry.installed_tier:
        return None
    actual = skill.security_tier
    if actual == entry.installed_tier:
        return None

    locked_rank = (
        TIER_ORDER.index(entry.installed_tier)
        if entry.installed_tier in TIER_ORDER
        else -1
    )
    actual_rank = TIER_ORDER.index(actual) if actual in TIER_ORDER else -1
    escalated = actual_rank > locked_rank
    # An escalation is fatal from any baseline: it is a claim to trust that the
    # install path examined and refused to grant.
    fatal = escalated or _attested(entry)
    direction = "above" if escalated else "below"
    return Drift(
        skill=entry.name,
        kind=DRIFT_TIER,
        expected=entry.installed_tier,
        actual=actual,
        fatal=fatal,
        summary=(
            f"The installed {SKILL_FILENAME} now claims security tier "
            f"'{actual}' — {direction} the '{entry.installed_tier}' that install "
            "enforced from its signature."
        ),
        remediation=_remediation(entry, fatal),
        path=str(directory),
    )


def _content_drift(entry: LockEntry, directory: Path) -> Optional[Drift]:
    if not entry.content_digest:
        return Drift(
            skill=entry.name,
            kind=DRIFT_UNRECORDED,
            expected="",
            actual="",
            fatal=False,
            summary=(
                f"{LOCK_FILENAME} has no content digest for '{entry.name}', so its "
                "files cannot be checked for tampering."
            ),
            remediation=(
                (
                    "This entry predates digest recording. Reinstall it with "
                    f"'gaia skill install {entry.name} --force' to take the digest "
                    "from the hub's own verified bytes — 'gaia skill lock --relock' "
                    "refuses to stamp one over bytes no signature covered."
                )
                if _attested(entry)
                else (
                    "This entry predates digest recording. Record it with "
                    "'gaia skill lock --relock'."
                )
            ),
            path=str(directory),
        )

    actual = content_digest(directory)
    if actual == entry.content_digest:
        return None
    fatal = _attested(entry)
    return Drift(
        skill=entry.name,
        kind=DRIFT_CONTENT,
        expected=entry.content_digest,
        actual=actual,
        fatal=fatal,
        summary=(
            f"The files in {directory} no longer hash to the digest recorded at "
            "install — something was added, removed, or edited."
        ),
        remediation=_remediation(entry, fatal),
        path=str(directory),
    )


def _remediation(entry: LockEntry, fatal: bool) -> str:
    """What to do about a drift — never naming an action the entry cannot take.

    A ``local`` skill was never on the hub, so telling its author to reinstall
    from one is an instruction that fails.
    """
    if fatal:
        return (
            f"Reinstall the attested copy with 'gaia skill install {entry.name} "
            f"--force'. If you meant to edit it, 'gaia skill remove {entry.name}' "
            f"then 'gaia skill import <dir>' re-stamps it '{LOWEST_TIER}' — a "
            "modified skill cannot keep the tier its signature earned."
        )
    undo = (
        f"reinstall with 'gaia skill install {entry.name} --force' or remove it "
        f"with 'gaia skill remove {entry.name}'"
        if entry.source == SOURCE_HUB
        else f"inspect {entry.path or entry.name} and restore it from your own copy"
    )
    return (
        "If you made this change, record it with 'gaia skill lock --relock'. If "
        f"you did not, {undo}."
    )


def _would_launder(entry: LockEntry, drift: Drift) -> bool:
    """Whether re-recording *drift* would put an attestation over unverified bytes.

    Fatal drift is the obvious case. A **missing** digest is the subtle one: the
    lock never covered these bytes at all, so stamping the current ones asserts
    the publisher's signature over content nothing checked. That is the state
    every upgrade from a pre-digest build lands in, which makes it the likeliest
    way to launder an attestation, not the rarest.
    """
    if drift.fatal:
        return True
    return drift.kind == DRIFT_UNRECORDED and _attested(entry)


def _inspect(entry: LockEntry, directory: Path) -> list[Drift]:
    """Every drift for one locked skill."""
    if not (directory / SKILL_FILENAME).is_file():
        return [
            Drift(
                skill=entry.name,
                kind=DRIFT_MISSING,
                expected=str(directory),
                actual="(absent)",
                fatal=False,
                summary=(
                    f"{LOCK_FILENAME} tracks '{entry.name}' {entry.version} at "
                    f"{directory}, but nothing is installed there."
                ),
                remediation=(
                    (
                        f"Reinstall it with 'gaia skill install {entry.name}', or "
                        "drop the stale entry with 'gaia skill lock --relock'."
                    )
                    if entry.source == SOURCE_HUB
                    else (
                        "Restore the directory from your own copy, or drop the "
                        "stale entry with 'gaia skill lock --relock'."
                    )
                ),
                path=str(directory),
            )
        ]

    skill, parse_error = _read_metadata(directory)
    if skill is None:
        fatal = _attested(entry)
        return [
            Drift(
                skill=entry.name,
                kind=DRIFT_CONTENT,
                expected=entry.content_digest or entry.version,
                actual="(unparseable)",
                fatal=fatal,
                summary=(
                    f"The installed {SKILL_FILENAME} for '{entry.name}' no longer "
                    f"parses: {parse_error}"
                ),
                remediation=_remediation(entry, fatal),
                path=str(directory),
            )
        ]

    found = [
        _version_drift(entry, skill, directory),
        _tier_drift(entry, skill, directory),
        _content_drift(entry, directory),
    ]
    return [d for d in found if d is not None]


def check_skill(skills_root: Path | str, skill: Skill) -> tuple[Drift, ...]:
    """Drift for one already-parsed skill, or ``()`` when the lock skips it.

    Takes the parsed :class:`~gaia.skills.format.Skill` rather than a name so the
    load path does not re-read ``SKILL.md`` it has already read.
    """
    root = Path(skills_root)
    directory = skill.directory
    if directory is None:
        return ()
    entry = SkillLock.load(root).get(skill.name)
    if entry is None:
        return ()

    found = [
        _version_drift(entry, skill, directory),
        _tier_drift(entry, skill, directory),
        _content_drift(entry, directory),
    ]
    return tuple(d for d in found if d is not None)


def assert_no_fatal_drift(skills_root: Path | str, skill: Skill) -> None:
    """Refuse a signature-backed skill whose bytes changed after install.

    Raises:
        SkillDriftError: the lock records an attested install and the on-disk
            copy no longer matches it. Non-fatal drift is logged, not raised.
    """
    drifts = check_skill(skills_root, skill)
    if not drifts:
        return

    fatal = [d for d in drifts if d.fatal]
    for drift in drifts:
        if not drift.fatal:
            log.warning("Skill '%s' drifted from the lock: %s", skill.name, drift)
    if not fatal:
        return

    detail = " ".join(d.summary for d in fatal)
    raise SkillDriftError(
        f"Refusing to load skill '{skill.name}': it no longer matches "
        f"{LOCK_FILENAME}. {detail} It was installed at a signature-backed tier, "
        "so these are not the bytes that were verified, and the tier badge no "
        f"longer means anything. {fatal[0].remediation} Inspect the full picture "
        "with 'gaia skill lock --check'."
    )


def check_drift(skills_root: Path | str) -> DriftReport:
    """Compare every skill in *skills_root* against ``skill-lock.json``."""
    root = Path(skills_root)
    lock = SkillLock.load(root)
    drifts: list[Drift] = []

    for name in sorted(lock.entries):
        entry = lock.entries[name]
        drifts.extend(_inspect(entry, root / name))

    for directory in sorted(_skill_dirs(root)):
        name = directory.name
        if name in lock.entries:
            continue
        drifts.append(
            Drift(
                skill=name,
                kind=DRIFT_UNTRACKED,
                expected="(no lock entry)",
                actual=str(directory),
                fatal=False,
                summary=(
                    f"'{name}' is installed at {directory} but {LOCK_FILENAME} does "
                    "not track it, so nothing can tell whether it has changed."
                ),
                remediation=(
                    "Expected if you created, imported, or migrated it. Start "
                    "tracking it with 'gaia skill lock --relock'; if you did not "
                    f"put it there, remove it with 'gaia skill remove {name}'."
                ),
                path=str(directory),
            )
        )

    report = DriftReport(root=root, lock_file=lock_path(root), drifts=tuple(drifts))
    log.info(
        "Lock check over %s: %d drift(s) (%d fatal) across %d locked skill(s)",
        root,
        len(report.drifts),
        len(report.fatal),
        len(lock.entries),
    )
    return report


def _skill_dirs(root: Path) -> list[Path]:
    """Every directory under *root* that holds a ``SKILL.md``."""
    if not root.is_dir():
        return []
    return [
        entry
        for entry in root.iterdir()
        if entry.is_dir() and (entry / SKILL_FILENAME).is_file()
    ]


# ----------------------------------------------------------------------
# Relock
# ----------------------------------------------------------------------


@dataclass
class RelockResult:
    """What ``gaia skill lock --relock`` changed."""

    lock_file: Path
    #: Entries whose recorded state was updated to match disk.
    updated: list[str]
    #: Skills newly tracked as ``source: local``.
    added: list[str]
    #: Stale entries dropped because the skill is gone.
    removed: list[str]
    #: Entries left alone because relocking them would launder an attestation.
    refused: list[Drift]

    @property
    def changed(self) -> bool:
        return bool(self.updated or self.added or self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lock_file": str(self.lock_file),
            "updated": list(self.updated),
            "added": list(self.added),
            "removed": list(self.removed),
            "refused": [d.to_dict() for d in self.refused],
            "changed": self.changed,
        }


def relock(skills_root: Path | str) -> RelockResult:
    """Re-record the current state of *skills_root* into ``skill-lock.json``.

    This is the explicit "yes, I meant to change that" step. It updates each
    tracked skill's version, tier, and content digest to what is on disk, drops
    entries whose skill is gone, and starts tracking untracked directories as
    ``source: local``.

    It **refuses** to re-record a signature-backed entry whose content drifted,
    or one that has no recorded digest at all. Writing a digest in either case
    would leave the lock asserting a ``verified`` / ``community`` tier and a
    publisher signature over bytes that signature never covered — laundering an
    attestation is exactly what a lockfile exists to prevent. Those entries are
    returned in :attr:`RelockResult.refused` with the two honest ways out
    (reinstall, or remove and re-import at ``experimental``).

    Raises:
        SkillValidationError: the existing lock is corrupt (from
            :meth:`gaia.skills.lock.SkillLock.load`); relock will not start over
            from scratch and silently discard provenance.
    """
    root = Path(skills_root)
    lock = SkillLock.load(root)
    updated: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    refused: list[Drift] = []

    for name in sorted(lock.entries):
        entry = lock.entries[name]
        directory = root / name

        if not (directory / SKILL_FILENAME).is_file():
            lock.forget(name)
            removed.append(name)
            continue

        blocking = [d for d in _inspect(entry, directory) if _would_launder(entry, d)]
        if blocking:
            refused.extend(blocking)
            continue

        skill, parse_error = _read_metadata(directory)
        if skill is None:  # pragma: no cover - a fatal parse error is refused above
            raise SkillDriftError(
                f"Cannot relock '{name}': its {SKILL_FILENAME} does not parse "
                f"({parse_error}). Fix the manifest, or remove the skill with "
                f"'gaia skill remove {name}'."
            )

        before = (entry.version, entry.installed_tier, entry.content_digest)
        entry.version = skill.version or entry.version
        entry.content_digest = content_digest(directory)
        entry.permissions = list(skill.gaia.permissions)
        entry.path = str(directory)
        if entry.source != SOURCE_HUB:
            # A local entry's tier is a record of the author's claim, not an
            # enforcement decision, so it tracks the manifest. A hub entry's is
            # the enforced tier — never overwritten from the file it governs.
            entry.installed_tier = skill.security_tier
        if before != (entry.version, entry.installed_tier, entry.content_digest):
            updated.append(name)

    for directory in sorted(_skill_dirs(root)):
        name = directory.name
        if name in lock.entries:
            continue
        skill, parse_error = _read_metadata(directory)
        if skill is None:
            raise SkillDriftError(
                f"Cannot relock '{name}': its {SKILL_FILENAME} does not parse "
                f"({parse_error}). Fix the manifest at {directory}, or remove the "
                "directory."
            )
        lock.record(
            LockEntry(
                name=name,
                version=skill.version or "",
                requested="",
                source=SOURCE_LOCAL,
                content_digest=content_digest(directory),
                claimed_tier=skill.security_tier,
                installed_tier=skill.security_tier,
                permissions=list(skill.gaia.permissions),
                path=str(directory),
            )
        )
        added.append(name)

    lock.save(lock_path(root))
    log.info(
        "Relocked %s: %d updated, %d added, %d removed, %d refused",
        root,
        len(updated),
        len(added),
        len(removed),
        len(refused),
    )
    return RelockResult(
        lock_file=lock_path(root),
        updated=updated,
        added=added,
        removed=removed,
        refused=refused,
    )


__all__ = [
    "DRIFT_CONTENT",
    "DRIFT_KINDS",
    "DRIFT_MISSING",
    "DRIFT_TIER",
    "DRIFT_UNRECORDED",
    "DRIFT_UNTRACKED",
    "DRIFT_VERSION",
    "Drift",
    "DriftReport",
    "RelockResult",
    "assert_no_fatal_drift",
    "check_drift",
    "check_skill",
    "relock",
]
