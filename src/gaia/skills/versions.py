# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
SemVer range matching for skill version pins.

One grammar serves both places a version is pinned:

* ``gaia skill install web-research@^1.2`` — the CLI spec
* the ``version:`` field of a ``gaia-agent.yaml`` ``skills:`` entry (``">=1.0.0"``)

so an agent manifest and a hand-typed install resolve identically. Comparison
reuses :func:`gaia.hub.catalog.compare_versions`, the hub's existing SemVer
precedence — a second comparator would eventually disagree with the catalog about
which version is newest.

Supported: ``*`` / ``latest`` / "" (any), a bare version (exact), ``==``, ``!=``,
``>=``, ``>``, ``<=``, ``<``, ``^`` (compatible-with, no major bump), ``~``
(approximately, no minor bump), and comma-separated conjunctions
(``">=1.2.0, <2.0.0"``).

Deliberately unsupported: ``||`` disjunction and hyphen ranges. They are not used
anywhere in GAIA and a half-implementation would silently mis-resolve, so they
raise instead.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from gaia.hub.catalog import compare_versions
from gaia.skills.errors import SkillValidationError

#: Range specs meaning "any version" — resolve to the highest available.
ANY_SPECS = frozenset({"", "*", "latest", "any"})

_OPERATOR_RE = re.compile(r"^\s*(==|!=|>=|<=|>|<|\^|~=|~)?\s*(.+?)\s*$")
_PARTIAL_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")

_DOCS = "https://amd-gaia.ai/docs/plans/skill-format"


def _core(version: str) -> tuple[int, int, int]:
    """Numeric ``(major, minor, patch)`` of *version*, zero-filling omitted parts."""
    match = _PARTIAL_RE.match(version.strip())
    if not match:
        raise SkillValidationError(
            f"{version!r} is not a version number. Use MAJOR[.MINOR[.PATCH]], "
            f"e.g. '1', '1.2', or '1.2.3'. See {_DOCS}"
        )
    major, minor, patch = match.groups()
    return int(major), int(minor or 0), int(patch or 0)


def _matches_clause(version: str, clause: str) -> bool:
    """Whether *version* satisfies a single comparator clause."""
    if "||" in clause:
        raise SkillValidationError(
            f"Version range {clause!r} uses '||' (disjunction), which GAIA does not "
            f"support. Use a single comparator or a comma-separated conjunction such "
            f"as '>=1.2.0, <2.0.0'. See {_DOCS}"
        )
    if " - " in clause:
        raise SkillValidationError(
            f"Version range {clause!r} uses a hyphen range, which GAIA does not "
            f"support. Express it as '>=<low>, <=<high>'. See {_DOCS}"
        )

    match = _OPERATOR_RE.match(clause)
    if match is None:  # pragma: no cover - the regex accepts any non-empty text
        raise SkillValidationError(f"Could not parse version range {clause!r}. See {_DOCS}")
    operator, target = match.group(1), match.group(2)

    if operator is None or operator == "==":
        # A bare partial version is a prefix match: '1.2' accepts any 1.2.x,
        # '1' any 1.x.y. A fully-qualified target is an exact match.
        if operator is None and target.count(".") < 2:
            major, minor, _ = _core(target)
            got = _core(version)
            return got[0] == major and (target.count(".") == 0 or got[1] == minor)
        return compare_versions(version, target) == 0
    if operator == "!=":
        return compare_versions(version, target) != 0
    if operator == ">=":
        return compare_versions(version, target) >= 0
    if operator == ">":
        return compare_versions(version, target) > 0
    if operator == "<=":
        return compare_versions(version, target) <= 0
    if operator == "<":
        return compare_versions(version, target) < 0

    # '^' and '~' are both "at least this, below some upper bound".
    major, minor, patch = _core(target)
    if compare_versions(version, f"{major}.{minor}.{patch}") < 0:
        return False
    got_major, got_minor, _ = _core(version)

    if operator == "^":
        # Compatible-with: no major bump. For 0.x, no minor bump either — 0.x is
        # unstable by SemVer convention, so 0.2.0 may break a 0.1.0 consumer.
        if major > 0:
            return got_major == major
        return got_major == 0 and got_minor == minor

    # '~' / '~=' — approximately: no minor bump, unless the target named only a
    # major ('~1' means 1.x.y), which pins the major instead.
    if target.count(".") == 0:
        return got_major == major
    return (got_major, got_minor) == (major, minor)


def matches(version: str, spec: Optional[str]) -> bool:
    """Whether *version* satisfies the range *spec*.

    Raises:
        SkillValidationError: for an unparseable spec (fail loudly — a spec GAIA
            cannot read must never be treated as "any").
    """
    normalized = (spec or "").strip()
    if normalized.lower() in ANY_SPECS:
        return True
    return all(_matches_clause(version, clause) for clause in normalized.split(",") if clause.strip())


def highest(versions: Iterable[str]) -> Optional[str]:
    """The highest version by SemVer precedence, or None for an empty iterable."""
    best: Optional[str] = None
    for candidate in versions:
        if best is None or compare_versions(candidate, best) > 0:
            best = candidate
    return best


def resolve(versions: Iterable[str], spec: Optional[str]) -> Optional[str]:
    """Highest version in *versions* satisfying *spec*, or None when none does.

    "Highest satisfying" matches how the hub resolves agent ``dependencies:``, so
    a skill pin and an agent pin behave the same way.
    """
    return highest(v for v in versions if matches(v, spec))
