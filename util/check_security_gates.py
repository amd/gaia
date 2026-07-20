# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Security suppression review gate.

Every ``# noqa: S<n>`` (flake8-bandit) and ``# nosec`` (bandit) comment under
``src/`` and ``hub/`` must be listed in ``.security-suppressions.json`` with a
justification. A suppression not listed there fails, forcing a human to review and
justify it in the PR. This is the gate that would have caught the GAIA hub
tar-slip (CWE-22): a ``# noqa: S202 - hub artifacts are trusted`` silenced an
unvalidated ``tarfile.extractall`` from both bandit and the weekly audit.

Entries key on ``(path, rule)`` — never a line number — so the allowlist survives
edits instead of re-firing on every line shift.

Runs as part of ``python util/lint.py --all`` (or ``--security``). Run directly
with ``python util/check_security_gates.py``.

NOTE: a companion "new bandit HIGH" gate (grandfathering today's findings via a
baseline, then failing on genuinely new HIGH findings) is a planned follow-up,
enabled once the pre-existing HIGH findings are fixed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Anchor to the repo root so the script works regardless of CWD — matches
# util/check_dependabot.py and util/check_doc_versions.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPRESSIONS_FILE = REPO_ROOT / ".security-suppressions.json"

# Directories scanned for suppressions. Security suppressions can hide a finding
# anywhere shippable code lives — core (src/) and the hub agents (hub/).
SCAN_DIRS = ("src", "hub")
EXCLUDE_PARTS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    "build",
    "dist",
    "site-packages",
    ".backup",
}

# A flake8-bandit security code is 'S' + exactly three digits (S101..S704).
# This deliberately excludes non-security 'S'-prefixed codes like SLF001
# (flake8-self) or SIM/… which are style, not security.
_BANDIT_NOQA_CODE = re.compile(r"S\d{3}")
_NOQA = re.compile(r"#\s*noqa:\s*([A-Za-z0-9,\s]+)")
_ALL_CODES = re.compile(r"[A-Za-z]+\d+")
_NOSEC = re.compile(r"#\s*nosec\b(.*)")
_NOSEC_BCODE = re.compile(r"B\d{3}")


def _iter_python_files(root: Path):
    for scan in SCAN_DIRS:
        base = root / scan
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if EXCLUDE_PARTS & set(path.parts):
                continue
            yield path


def find_suppressions(root: Path | None = None) -> List[Dict[str, Any]]:
    """Return every security suppression found under the scanned dirs.

    Each item is ``{"path": <repo-relative posix>, "rule": <code>, "line": <n>}``.
    ``rule`` is a flake8-bandit ``S<n>`` code, a bandit ``B<n>`` code, or the
    literal ``"nosec"`` for a bare ``# nosec`` with no explicit code.
    """
    root = root or REPO_ROOT
    found: List[Dict[str, Any]] = []
    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            noqa = _NOQA.search(line)
            if noqa:
                for code in _ALL_CODES.findall(noqa.group(1)):
                    if _BANDIT_NOQA_CODE.fullmatch(code):
                        found.append({"path": rel, "rule": code, "line": lineno})
            nosec = _NOSEC.search(line)
            if nosec:
                bcodes = _NOSEC_BCODE.findall(nosec.group(1))
                if bcodes:
                    for b in bcodes:
                        found.append({"path": rel, "rule": b, "line": lineno})
                else:
                    found.append({"path": rel, "rule": "nosec", "line": lineno})
    return found


def load_allowlist() -> set[Tuple[str, str]]:
    """Load the approved ``(path, rule)`` suppression pairs."""
    if not SUPPRESSIONS_FILE.exists():
        raise FileNotFoundError(
            f"{SUPPRESSIONS_FILE} is missing. It is the allowlist of approved "
            f"security suppressions; the security gate cannot run without it."
        )
    try:
        data = json.loads(SUPPRESSIONS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{SUPPRESSIONS_FILE} is not valid JSON: {exc}") from exc
    allow: set[Tuple[str, str]] = set()
    for entry in data.get("suppressions", []):
        path = entry.get("path")
        rule = entry.get("rule")
        if not path or not rule:
            raise ValueError(
                f"{SUPPRESSIONS_FILE}: every suppression needs a 'path' and 'rule' "
                f"(offending entry: {entry!r})."
            )
        if not entry.get("justification"):
            raise ValueError(
                f"{SUPPRESSIONS_FILE}: suppression {path}:{rule} has no "
                f"'justification' — every approved suppression must say why."
            )
        allow.add((path, rule))
    return allow


def suppression_violations(
    suppressions: List[Dict[str, Any]], allowlist: set[Tuple[str, str]]
) -> List[Dict[str, Any]]:
    """Return suppressions whose ``(path, rule)`` is not in the allowlist."""
    return [s for s in suppressions if (s["path"], s["rule"]) not in allowlist]


def check_suppressions() -> Tuple[bool, List[str]]:
    """Run the suppression gate. Returns ``(ok, messages)``."""
    suppressions = find_suppressions()
    allowlist = load_allowlist()
    violations = suppression_violations(suppressions, allowlist)
    msgs: List[str] = []
    if not violations:
        msgs.append(
            f"[OK] {len(suppressions)} security suppression(s) - all reviewed in "
            f"{SUPPRESSIONS_FILE.name}."
        )
        return True, msgs
    msgs.append(
        f"[FAIL] {len(violations)} security suppression(s) are NOT reviewed in "
        f"{SUPPRESSIONS_FILE.name}:"
    )
    for v in violations:
        msgs.append(f"    {v['path']}:{v['line']}  {v['rule']}")
    msgs.append(
        "  Every '# noqa: S<n>' / '# nosec' must be justified in "
        f"{SUPPRESSIONS_FILE.name} so a human reviews it (this is the gate that "
        "would have caught the hub tar-slip). Add an entry with a 'justification', "
        "or remove the suppression and fix the finding."
    )
    return False, msgs


def main() -> int:
    ok, msgs = check_suppressions()
    for m in msgs:
        print(m)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
