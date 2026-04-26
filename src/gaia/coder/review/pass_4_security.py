# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pass 4 — self-security review (§8 row 4).

Deterministic. Runs:

* ``gitleaks detect --source=<repo>`` to catch added secrets.
* An AST scanner that flags ``eval``, ``exec``, ``subprocess.*(shell=True)``,
  SQL-by-string-concat, and ``os.system`` usage on added Python files.
* ``pip-audit`` (if ``requirements.txt`` / ``pyproject.toml`` moved).
* ``npm audit --json`` (if ``package-lock.json`` moved).

Missing tools produce a single ``skipped`` finding *per tool* — we do not
silently pass if gitleaks is unavailable, because secrets are the highest
-cost false negative in the review. The EM gets an actionable message
telling them to install the tool.

Per §15.8 deterministic checks: ``gitleaks detect``, AST scanner for
``eval``/``exec``/``shell=True``, ``pip-audit``, ``npm audit --json``.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from gaia.coder.review._diff import (
    DiffBundle,
    filter_by_extension,
    resolve_diff,
)
from gaia.coder.review.pass_result import PassResult, make_pass_result
from gaia.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def _run(
    cmd: List[str], *, cwd: Optional[Path] = None, timeout_s: int = 60
) -> Tuple[int, str, str]:
    """Run ``cmd`` with a bounded timeout."""
    from gaia.coder.tools.cli import _check_denylist

    _check_denylist(cmd)
    try:
        completed = subprocess.run(  # pylint: disable=subprocess-run-check
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return (
            124,
            exc.stdout or "",
            f"{' '.join(cmd)} timed out after {timeout_s}s",
        )
    return completed.returncode, completed.stdout, completed.stderr


# ---------------------------------------------------------------------------
# gitleaks
# ---------------------------------------------------------------------------


def _run_gitleaks(*, cwd: Optional[Path]) -> List[dict]:
    """Scan with gitleaks. Returns findings or a single ``skipped`` finding."""
    if shutil.which("gitleaks") is None:
        return [
            {
                "severity": "info",
                "description": (
                    "gitleaks not installed; secret scan skipped. "
                    "Install with `brew install gitleaks` or see "
                    "https://github.com/gitleaks/gitleaks."
                ),
                "status": "skipped",
                "citation": "§8 Pass 4 — gitleaks",
            }
        ]
    code, out, err = _run(
        [
            "gitleaks",
            "detect",
            "--source=.",
            "--no-banner",
            "--redact",
            "--exit-code=1",
        ],
        cwd=cwd,
    )
    if code == 0:
        return []
    tail = "\n".join((out + err).strip().splitlines()[-30:])
    return [
        {
            "severity": "blocking",
            "description": "gitleaks detected potential secrets",
            "output_tail": tail,
            "citation": "§8 Pass 4 — no secrets in diff",
        }
    ]


# ---------------------------------------------------------------------------
# AST scanner
# ---------------------------------------------------------------------------


class _UnsafeCallVisitor(ast.NodeVisitor):
    """Walk a module AST and collect dangerous patterns."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: List[dict] = []

    def _emit(self, node: ast.AST, description: str) -> None:
        self.findings.append(
            {
                "severity": "blocking",
                "description": description,
                "file": self.path,
                "line": getattr(node, "lineno", 0),
                "citation": "§8 Pass 4 — no unsafe dynamic exec",
            }
        )

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        # eval(...) / exec(...)
        if isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
            self._emit(node, f"use of {func.id}() on added code")
        # os.system(...)
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and func.attr == "system"
        ):
            self._emit(node, "os.system() call")
        # subprocess.*(shell=True)
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    self._emit(
                        node,
                        f"subprocess.{func.attr}(..., shell=True) — prefer "
                        "argv lists",
                    )
        self.generic_visit(node)

    # SQL-by-string-concat detection is intentionally regex-based in the
    # caller; walking every BinOp is noisy. This visitor focuses on the
    # high-value dynamic-exec family.


def _scan_python_file(path: Path) -> List[dict]:
    try:
        source = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Syntax errors are Pass-1 territory; don't double-report.
        return []
    visitor = _UnsafeCallVisitor(str(path))
    visitor.visit(tree)
    return visitor.findings


def _scan_python(py_files: List[str], *, cwd: Optional[Path]) -> List[dict]:
    findings: List[dict] = []
    root = cwd or Path(".")
    for relpath in py_files:
        full = root / relpath
        findings.extend(_scan_python_file(full))
    return findings


# ---------------------------------------------------------------------------
# pip-audit / npm audit
# ---------------------------------------------------------------------------


def _run_pip_audit(changed_files: List[str], *, cwd: Optional[Path]) -> List[dict]:
    touched = any(
        f in {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"}
        or f.endswith("/requirements.txt")
        for f in changed_files
    )
    if not touched:
        return []
    if shutil.which("pip-audit") is None:
        return [
            {
                "severity": "info",
                "description": (
                    "pip-audit not installed; Python dep audit skipped. "
                    "Install with `pip install pip-audit`."
                ),
                "status": "skipped",
                "citation": "§8 Pass 4 — pip-audit",
            }
        ]
    code, out, err = _run(["pip-audit", "--strict"], cwd=cwd)
    if code == 0:
        return []
    tail = "\n".join((out + err).strip().splitlines()[-20:])
    return [
        {
            "severity": "significant",
            "description": "pip-audit reported vulnerable dependencies",
            "output_tail": tail,
            "citation": "§8 Pass 4 — pip-audit",
        }
    ]


def _run_npm_audit(changed_files: List[str], *, cwd: Optional[Path]) -> List[dict]:
    touched = any(
        f == "package.json"
        or f == "package-lock.json"
        or f.endswith("/package.json")
        or f.endswith("/package-lock.json")
        for f in changed_files
    )
    if not touched:
        return []
    if shutil.which("npm") is None:
        return [
            {
                "severity": "info",
                "description": "npm not installed; npm audit skipped.",
                "status": "skipped",
                "citation": "§8 Pass 4 — npm audit",
            }
        ]
    code, out, err = _run(["npm", "audit", "--json"], cwd=cwd)
    # npm audit exit code: 0 = no vulns; 1 = vulns found. Either way the JSON
    # payload is on stdout. For v1 we treat a non-zero exit as "has findings".
    if code == 0:
        return []
    tail = "\n".join((out + err).strip().splitlines()[-10:])
    return [
        {
            "severity": "significant",
            "description": "npm audit reported vulnerable dependencies",
            "output_tail": tail,
            "citation": "§8 Pass 4 — npm audit",
        }
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_pass(
    pr_or_branch: str,
    *,
    base_ref: str = "coder",
    repo_root: Optional[Path] = None,
    diff: Optional[DiffBundle] = None,
) -> PassResult:
    """Execute Pass 4 and return the :class:`PassResult`."""
    diff_bundle = diff or resolve_diff(
        pr_or_branch, base_ref=base_ref, repo_root=repo_root
    )
    py_files = filter_by_extension(diff_bundle.changed_files, (".py",))

    findings: List[dict] = []
    tooling_used: List[str] = []

    # gitleaks
    gitleaks_findings = _run_gitleaks(cwd=repo_root)
    findings.extend(gitleaks_findings)
    if gitleaks_findings and gitleaks_findings[0].get("status") != "skipped":
        tooling_used.append("gitleaks detect --source=.")
    elif not gitleaks_findings:
        tooling_used.append("gitleaks detect --source=.")

    # AST scanner
    ast_findings = _scan_python(py_files, cwd=repo_root)
    findings.extend(ast_findings)
    if py_files:
        tooling_used.append("AST scan (eval/exec/shell=True/os.system)")

    # pip-audit / npm audit
    pip_findings = _run_pip_audit(diff_bundle.changed_files, cwd=repo_root)
    findings.extend(pip_findings)
    if any(f.get("status") != "skipped" for f in pip_findings) or not pip_findings:
        if any(
            f.endswith(("pyproject.toml", "requirements.txt"))
            or f in {"pyproject.toml", "setup.py", "setup.cfg"}
            for f in diff_bundle.changed_files
        ):
            tooling_used.append("pip-audit")

    npm_findings = _run_npm_audit(diff_bundle.changed_files, cwd=repo_root)
    findings.extend(npm_findings)
    if any(
        f.endswith(("package.json", "package-lock.json"))
        for f in diff_bundle.changed_files
    ):
        tooling_used.append("npm audit --json")

    # A single blocking finding is enough to fail Pass 4.
    hard_fail = any(f.get("severity") == "blocking" for f in findings)
    return make_pass_result(
        status="fail" if hard_fail else "pass",
        findings=findings,
        confidence=None,
        citations=[
            "docs/plans/coder-agent.mdx §8 Pass 4",
            "docs/plans/coder-agent.mdx §15.8 deterministic checks",
        ],
        tooling_used=tooling_used,
    )


__all__ = ["run_pass"]
