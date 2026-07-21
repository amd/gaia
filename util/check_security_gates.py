# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Security gate helpers for GAIA's lint pipeline.

Currently implements the bandit HIGH-severity blocking gate: any HIGH finding
that is not explicitly allow-listed in ``.bandit-baseline.json`` fails the build.
Findings are keyed by ``(normalized path, test_id)`` -- never the line number, so
an unrelated edit that shifts a flagged line does not spuriously trip the gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

BanditKey = Tuple[str, str]


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


def main(argv: List[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="GAIA bandit HIGH security gate")
    parser.add_argument("--src", default="src/gaia", help="Source dir to scan")
    parser.add_argument(
        "--baseline",
        default=".bandit-baseline.json",
        help="Allowlist of accepted HIGH findings",
    )
    args = parser.parse_args(argv)

    passed, new_highs = check_bandit_high_gate(args.src, args.baseline)
    if passed:
        print("[OK] Bandit HIGH gate: 0 new HIGH-severity findings.")
        return 0

    print(f"[FAIL] Bandit HIGH gate: {len(new_highs)} HIGH finding(s) not baselined:")
    for finding in new_highs:
        print(f"  - {format_finding(finding)}")
    print(
        "\nFix the finding, or (only if genuinely unavoidable) add an inline "
        "`# nosec <rule>` with a matching justified entry in "
        "`.security-suppressions.json`, and if truly accepted, allowlist it in "
        f"{args.baseline}."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
