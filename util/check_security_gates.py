# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Security gate helpers for GAIA's lint pipeline.

Two complementary gates live here, both run by ``python util/lint.py --all``:

1. Suppression review gate (``check_suppressions``). Every ``# noqa: S<n>``
   (flake8-bandit) and ``# nosec`` (bandit) comment under ``src/`` and ``hub/``
   must be listed in ``.security-suppressions.json`` with a justification. A
   suppression not listed there fails, forcing a human to review and justify it
   in the PR. This is the gate that would have caught the GAIA hub tar-slip
   (CWE-22): a ``# noqa: S202 - hub artifacts are trusted`` silenced an
   unvalidated ``tarfile.extractall`` from both bandit and the weekly audit.
   Entries key on ``(path, rule)`` — never a line number — so the allowlist
   survives edits instead of re-firing on every line shift.

2. Bandit HIGH gate (``check_bandit_high_gate``). Any HIGH-severity bandit
   finding that is not explicitly allow-listed in ``.bandit-baseline.json`` fails
   the build. Findings are keyed by ``(normalized path, test_id)`` — never the
   line number — so an unrelated edit that shifts a flagged line does not
   spuriously trip the gate.

Run directly with ``python util/check_security_gates.py`` to execute both gates.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

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

BanditKey = Tuple[str, str]


# ---------------------------------------------------------------------------
# Suppression review gate
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Bandit HIGH gate
# ---------------------------------------------------------------------------
def _normalize_path(path: str) -> str:
    """Normalize a file path for stable cross-platform comparison."""
    return str(path).replace("\\", "/")


def bandit_finding_key(result: Dict[str, Any]) -> BanditKey:
    """Stable key for a bandit result: (normalized path, test_id).

    Deliberately excludes the line number so cosmetic line shifts elsewhere in
    a file do not change a finding's identity.
    """
    return (_normalize_path(result["filename"]), result["test_id"])


def _baseline_keys(baseline: Iterable[Any]) -> set[BanditKey]:
    """Build the allow-listed key set from baseline entries.

    Each entry is either a ``{"path": ..., "test_id": ...}`` dict or a
    ``[path, test_id]`` pair.
    """
    keys: set[BanditKey] = set()
    for entry in baseline or []:
        if isinstance(entry, dict):
            keys.add((_normalize_path(entry["path"]), entry["test_id"]))
        else:
            path, test_id = entry
            keys.add((_normalize_path(path), test_id))
    return keys


def new_bandit_highs(
    results: Iterable[Dict[str, Any]], baseline: Iterable[Any]
) -> List[Dict[str, Any]]:
    """Return HIGH-severity bandit findings not present in the baseline allowlist.

    Args:
        results: the ``results`` array from ``bandit -f json``.
        baseline: allow-listed findings, each a ``{"path", "test_id"}`` dict or
            a ``[path, test_id]`` pair. An empty/omitted baseline means ANY HIGH
            finding is reported.

    Returns:
        The offending result dicts (HIGH severity, not allow-listed), in input
        order.
    """
    allow = _baseline_keys(baseline)
    offending: List[Dict[str, Any]] = []
    for result in results:
        if result.get("issue_severity") != "HIGH":
            continue
        if bandit_finding_key(result) in allow:
            continue
        offending.append(result)
    return offending


def parse_bandit_json(raw: str) -> Dict[str, Any]:
    """Parse bandit JSON output, tolerating a leading progress-bar prefix.

    Bandit writes a ``Working... 100%`` progress line to stdout before the JSON
    document; skip everything up to the first ``{``.
    """
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object found in bandit output")
    return json.loads(raw[start:])


DEFAULT_EXCLUDE = (
    ".git,__pycache__,venv,.venv,.mypy_cache,.tox,.eggs,_build,buck-out,node_modules"
)


def _bandit_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("bandit") is not None


def run_bandit(src_dir: str, exclude: str = DEFAULT_EXCLUDE) -> Dict[str, Any]:
    """Run bandit over ``src_dir`` at -ll and return the parsed JSON report."""
    import shutil

    base = ["-r", src_dir, "-ll", "-f", "json", "--exclude", exclude]
    # Prefer the locally installed module (fast, offline); fall back to uvx.
    if _bandit_available():
        cmd = [sys.executable, "-m", "bandit", *base]
    elif shutil.which("uvx"):
        cmd = ["uvx", "bandit", *base]
    else:
        cmd = ["bandit", *base]

    # bandit exits non-zero when it finds issues; we parse the JSON regardless.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        # Re-raise as RuntimeError so callers only handle one exception type.
        raise RuntimeError(
            f"bandit is not installed ({exc}). Install it with "
            "`uv pip install -e '.[dev]'`, or run lint via `uv run python util/lint.py`."
        ) from exc
    if not proc.stdout.strip():
        raise RuntimeError(
            f"bandit produced no output (exit {proc.returncode}):\n{proc.stderr}"
        )
    return parse_bandit_json(proc.stdout)


def load_baseline(path: str | Path) -> List[Any]:
    """Load the bandit HIGH allowlist; missing file means an empty allowlist."""
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    # Accept either a bare list or {"findings": [...]}.
    if isinstance(data, dict):
        return data.get("findings", [])
    return data


def format_finding(result: Dict[str, Any]) -> str:
    """One-line human-readable rendering of a bandit finding."""
    return (
        f"{result['test_id']} "
        f"{_normalize_path(result['filename'])}:{result.get('line_number', '?')}  "
        f"{result.get('issue_text', '').strip()}"
    )


def check_bandit_high_gate(
    src_dir: str = "src/gaia",
    baseline_path: str | Path = ".bandit-baseline.json",
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Run the bandit HIGH gate.

    Returns ``(passed, new_highs)`` where ``passed`` is True iff there are no
    HIGH findings outside the baseline allowlist.
    """
    report = run_bandit(src_dir)
    baseline = load_baseline(baseline_path)
    new_highs = new_bandit_highs(report.get("results", []), baseline)
    return (not new_highs, new_highs)


# ---------------------------------------------------------------------------
# Entry point — run both gates
# ---------------------------------------------------------------------------
def main(argv: List[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="GAIA security gates")
    parser.add_argument("--src", default="src/gaia", help="Source dir to scan")
    parser.add_argument(
        "--baseline",
        default=".bandit-baseline.json",
        help="Allowlist of accepted bandit HIGH findings",
    )
    args = parser.parse_args(argv)

    # Gate 1: suppression review.
    try:
        supp_ok, supp_msgs = check_suppressions()
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}")
        supp_ok = False
        supp_msgs = []
    for msg in supp_msgs:
        print(msg)

    # Gate 2: bandit HIGH.
    bandit_ok, new_highs = check_bandit_high_gate(args.src, args.baseline)
    if bandit_ok:
        print("[OK] Bandit HIGH gate: 0 new HIGH-severity findings.")
    else:
        print(
            f"[FAIL] Bandit HIGH gate: {len(new_highs)} HIGH finding(s) not baselined:"
        )
        for finding in new_highs:
            print(f"  - {format_finding(finding)}")
        print(
            "\nFix the finding, or (only if genuinely unavoidable) add an inline "
            "`# nosec <rule>` with a matching justified entry in "
            "`.security-suppressions.json`, and if truly accepted, allowlist it in "
            f"{args.baseline}."
        )

    return 0 if (supp_ok and bandit_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
