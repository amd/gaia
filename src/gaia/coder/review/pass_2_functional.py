# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pass 2 — self-functional review (§8 row 2).

Deterministic. Runs the test suite for every changed Python module,
``npm test`` if the diff touches a ``package.json``-rooted tree, and
optionally a mutation-testing sample (``mutmut run``) that confirms new
tests actually fail without the change.

The pass emits ``confidence`` = 0-100 coverage percentage when
``coverage`` is available; otherwise ``None``.

Per §15.8 deterministic checks: ``pytest``, ``npm test``, ``mutmut run``.
"""

from __future__ import annotations

import logging
import re
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: List[str], *, cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run ``cmd`` and return ``(returncode, stdout, stderr)``."""
    from gaia.coder.tools.cli import _check_denylist

    _check_denylist(cmd)
    completed = subprocess.run(  # pylint: disable=subprocess-run-check
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _extract_coverage_pct(pytest_output: str) -> Optional[int]:
    """Parse coverage % out of ``pytest --cov`` output, if present.

    The ``pytest-cov`` plugin prints a ``TOTAL`` footer line like::

        TOTAL                   123    45  63%

    We return that last percentage as an int. Absence returns ``None``
    (we pass but do not report a coverage figure).
    """
    match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", pytest_output)
    if not match:
        return None
    return int(match.group(1))


def _infer_test_paths(
    py_files: List[str], *, repo_root: Optional[Path] = None
) -> List[str]:
    """Given changed ``.py`` paths, return *existing* test paths to feed pytest.

    Heuristic (good-enough for v1):

    1. Anything already under ``tests/`` is itself a test path.
    2. ``src/gaia/foo/bar.py`` → look for ``tests/**/test_bar.py`` and
       ``tests/**/test_foo.py``, expanded via :meth:`pathlib.Path.rglob`.

    pytest does **not** glob ``**`` itself — it treats unmatched paths as
    literals and exits with collection errors. We must therefore expand the
    glob ourselves and only hand pytest concrete files that exist on disk.
    When no candidate test file is found, return an empty list and let
    :func:`_run_pytest` fall through to its whole-suite fallback.
    """
    root = Path(repo_root) if repo_root is not None else Path(".")
    paths: List[str] = []
    for fn in py_files:
        if fn.startswith("tests/"):
            # Already a test path; keep as-is so pytest sees it literally.
            # Only emit it if it exists — a deleted-and-renamed test would
            # otherwise cause a collection error and short-circuit Pass 2.
            if (root / fn).exists():
                paths.append(fn)
            continue
        stem = Path(fn).stem
        parent = Path(fn).parent.name
        candidates = [f"test_{stem}.py"]
        if parent:
            candidates.append(f"test_{parent}.py")
        for pattern in candidates:
            for hit in (root / "tests").rglob(pattern):
                if not hit.is_file():
                    continue
                rel = hit.relative_to(root).as_posix()
                paths.append(rel)
    # De-dup preserving order
    deduped = list(dict.fromkeys(paths))
    if not deduped:
        logger.info(
            "pass 2: no per-file tests matched %d changed .py file(s); "
            "falling back to whole-suite pytest run.",
            len(py_files),
        )
    return deduped


def _run_pytest(
    test_paths: List[str], *, cwd: Optional[Path]
) -> Tuple[bool, str, Optional[int]]:
    """Run pytest. Return ``(passed, tail, coverage_pct)``.

    Missing pytest is a hard fail (we cannot verify functional correctness
    without it), not a skip.
    """
    if shutil.which("pytest") is None:
        return (
            False,
            "pytest not on PATH. Install dev deps: `uv pip install -e .[dev]`.",
            None,
        )
    # Try with coverage first; fall back to plain pytest if pytest-cov
    # is missing.
    cmd = ["pytest", "-x", "--tb=short", "--cov", *test_paths]
    code, out, err = _run(cmd, cwd=cwd)
    # If pytest-cov missing, the --cov flag errors. Retry without.
    combined = out + err
    if "unrecognized arguments: --cov" in combined:
        code, out, err = _run(["pytest", "-x", "--tb=short", *test_paths], cwd=cwd)
        combined = out + err
    tail = "\n".join(combined.strip().splitlines()[-30:])
    coverage = _extract_coverage_pct(combined)
    return (code == 0, tail, coverage)


def _run_npm_test(*, cwd: Optional[Path]) -> Tuple[str, bool, str]:
    """Run ``npm test`` if ``package.json`` exists.

    Returns ``(status, ok, tail)`` where ``status`` is one of
    ``"run"`` / ``"skipped"``.
    """
    package_json = (cwd or Path(".")) / "package.json"
    if not package_json.exists():
        return ("skipped", True, "no package.json at repo root; skipped")
    if shutil.which("npm") is None:
        return ("skipped", True, "npm not on PATH; skipped")
    code, out, err = _run(["npm", "test", "--silent"], cwd=cwd)
    tail = "\n".join((out + err).strip().splitlines()[-30:])
    return ("run", code == 0, tail)


def _run_mutmut(
    py_files: List[str], *, cwd: Optional[Path]
) -> Tuple[str, Optional[int]]:
    """Run ``mutmut run`` on the changed Python files if installed.

    Returns ``(status, surviving_mutants)``. ``surviving_mutants`` is
    ``None`` when the run is skipped or mutmut returned an unparseable
    result.

    Per the task spec: "Optional ``mutmut run --paths-to-mutate <files>``
    — skip if not installed, log a WARN." We follow that literally.
    """
    if not py_files:
        return ("skipped", None)
    if shutil.which("mutmut") is None:
        logger.warning(
            "pass 2: mutmut not installed; mutation sample skipped. "
            "Install with `pip install mutmut` to raise coverage rigor."
        )
        return ("skipped", None)
    joined = ",".join(py_files)
    code, out, _err = _run(["mutmut", "run", "--paths-to-mutate", joined], cwd=cwd)
    # mutmut exit code is non-zero when surviving mutants exist; that is
    # informational for us, not a hard failure in v1.
    match = re.search(r"(\d+)\s+surviv", out, re.IGNORECASE)
    surviving = int(match.group(1)) if match else None
    return ("run" if code in (0, 2) else "error", surviving)


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
    """Execute Pass 2 and return the :class:`PassResult`."""
    diff_bundle = diff or resolve_diff(
        pr_or_branch, base_ref=base_ref, repo_root=repo_root
    )
    py_files = filter_by_extension(diff_bundle.changed_files, (".py",))

    findings: List[dict] = []
    tooling_used: List[str] = []

    # --- pytest ---
    test_paths = _infer_test_paths(py_files, repo_root=repo_root)
    if py_files:
        tooling_used.append("pytest")
    pytest_ok, pytest_tail, coverage = _run_pytest(test_paths, cwd=repo_root)
    if py_files and not pytest_ok:
        findings.append(
            {
                "severity": "blocking",
                "description": "pytest failed",
                "output_tail": pytest_tail,
                "citation": "§8 Pass 2 — tests must pass",
            }
        )

    # --- npm test ---
    npm_status, npm_ok, npm_tail = _run_npm_test(cwd=repo_root)
    if npm_status == "run":
        tooling_used.append("npm test")
    if npm_status == "run" and not npm_ok:
        findings.append(
            {
                "severity": "blocking",
                "description": "npm test failed",
                "output_tail": npm_tail,
                "citation": "§8 Pass 2 — tests must pass",
            }
        )

    # --- mutmut (advisory) ---
    mutmut_status, surviving = _run_mutmut(py_files, cwd=repo_root)
    if mutmut_status == "run":
        tooling_used.append("mutmut run")
        if surviving and surviving > 0:
            findings.append(
                {
                    "severity": "significant",
                    "description": (
                        f"mutmut found {surviving} surviving mutant(s); "
                        f"your tests may not be exercising the change"
                    ),
                    "citation": "§8 Pass 2 — mutation sample",
                }
            )
    elif mutmut_status == "error":
        logger.warning("pass 2: mutmut exited abnormally; treating as advisory skip.")

    hard_fail = any(f.get("severity") == "blocking" for f in findings)
    return make_pass_result(
        status="fail" if hard_fail else "pass",
        findings=findings,
        confidence=coverage,
        citations=[
            "docs/plans/coder-agent.mdx §8 Pass 2",
            "docs/plans/coder-agent.mdx §15.8 deterministic checks",
        ],
        tooling_used=tooling_used,
    )


__all__ = ["run_pass"]
