# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Fail a CI lane when one of its test files skips wholesale without a reason.

A module-scope `pytest.importorskip("gaia_agent_chat")` turns a whole file into
one `SKIPPED` line in any lane that lacks the wheel. Because the lane still
*names* the file, `check_test_lane_coverage.py` counts it as covered, and #4206
found ~214 tests that no lane had actually run.

Run as a step inside the lane, after its install step:

    python util/check_module_skips.py --workflow test_unit.yml --job unit-tests

It reads the job's own pytest commands from the workflow, collects each one
with `--collect-only` in the lane's environment, and fails on any file (or
directory) that skipped at collection unless `util/test_lane_allowlist.yml`
lists it under `module_skips:`. An entry is not enough on its own: when its
`runs_in` names *this* workflow, this is the lane that is meant to run the file,
so a skip here fails too.

Only collection-time skips count. A `pytestmark = pytest.mark.skipif(...)` marks
each test skipped at run time and is not seen here.

The file is also the pytest plugin the collection runs with (`-p
check_module_skips`), which records the skipping node itself rather than the
line that raised — a test module that imports a skipping helper module is
reported under its own name.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

UTIL_DIR = Path(__file__).resolve().parent
if str(UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(UTIL_DIR))

import check_test_lane_coverage as lanes  # noqa: E402

REPORT_ENV = "GAIA_MODULE_SKIP_REPORT"
_OK_EXIT_CODES = (0, 5)  # 5: nothing collected, e.g. every file skipped

# ---------------------------------------------------------------------------
# pytest plugin half — runs inside the collecting pytest process
# ---------------------------------------------------------------------------

_SKIPPED: List[Dict[str, str]] = []


def pytest_collectreport(report) -> None:
    """Record a file or directory that skipped while being collected."""
    if not report.skipped or "::" in report.nodeid:
        return
    longrepr = report.longrepr
    reason = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
    _SKIPPED.append({"nodeid": report.nodeid, "reason": str(reason)})


def pytest_sessionfinish(session) -> None:
    """Write the recorded skips, as absolute paths, for the parent."""
    out = os.environ.get(REPORT_ENV)
    if not out:
        return
    root = Path(session.config.rootpath)
    records = [
        {"path": str((root / item["nodeid"]).resolve()), "reason": item["reason"]}
        for item in _SKIPPED
    ]
    Path(out).write_text(json.dumps(records), encoding="utf-8")


# ---------------------------------------------------------------------------
# Guard half — runs as the CI step
# ---------------------------------------------------------------------------


def job_pytest_commands(workflow: str, job: str) -> List[List[str]]:
    """The test paths of every pytest command the job runs."""
    path = lanes.WORKFLOW_DIR / workflow
    if not path.is_file():
        raise ValueError(f"workflow {path} not found")
    roots = lanes.test_roots(lanes.REPO_ROOT)
    commands: List[List[str]] = []
    for script, matrix in lanes.job_run_blocks(path, job):
        found, unresolved = lanes.pytest_commands_in_script(script, matrix, roots)
        if unresolved:
            raise ValueError(
                f"{workflow}/{job} names test paths this cannot resolve: "
                f"{sorted(unresolved)}"
            )
        commands.extend(c for c in found if c not in commands)
    if not commands:
        raise ValueError(f"{workflow}/{job} runs no pytest command")
    return commands


def collect_skips(paths: List[str]) -> List[Dict[str, str]]:
    """Collect `paths` in this interpreter's env; return what skipped wholesale."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "skips.json"
        env = dict(os.environ)
        env[REPORT_ENV] = str(report)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(UTIL_DIR), env.get("PYTHONPATH", "")) if p
        )
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "check_module_skips",
            "-p",
            "no:cacheprovider",
            *paths,
        ]
        proc = subprocess.run(
            cmd,
            cwd=lanes.REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if proc.returncode not in _OK_EXIT_CODES:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
            raise RuntimeError(
                f"collecting {' '.join(paths)} failed (pytest exit "
                f"{proc.returncode}), so its skips cannot be checked:\n{tail}"
            )
        if not report.is_file():
            raise RuntimeError(
                f"pytest collected {' '.join(paths)} but the check_module_skips "
                f"plugin wrote no report; was it loaded?"
            )
        records = json.loads(report.read_text(encoding="utf-8"))

    repo_root = lanes.REPO_ROOT.resolve()
    skips = []
    for record in records:
        path = Path(record["path"])
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            rel = path.as_posix()
        skips.append({"path": rel, "reason": record["reason"]})
    return skips


def judge(
    skips: List[Dict[str, str]],
    entries: List[lanes.ModuleSkip],
    workflow: str,
) -> Tuple[List[str], List[str]]:
    """Split skips into (violations, allowed) report lines."""
    violations: List[str] = []
    allowed: List[str] = []
    for skip in skips:
        path, why = skip["path"], skip["reason"]
        entry = lanes.find_module_skip(path, entries)
        if entry is None:
            violations.append(f"{path}: not in module_skips ({why})")
        elif entry.runs_in == workflow:
            violations.append(
                f"{path}: module_skips says {workflow} runs this file, but it "
                f"skipped here ({why})"
            )
        else:
            where = entry.runs_in or "no lane: " + entry.reason
            allowed.append(f"{path} -> {where}")
    return violations, allowed


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--workflow", required=True, help="e.g. test_unit.yml")
    parser.add_argument("--job", required=True, help="job id inside the workflow")
    args = parser.parse_args(argv)
    lane = f"{args.workflow}/{args.job}"

    try:
        entries = lanes.load_module_skips(lanes.ALLOWLIST_PATH)
        commands = job_pytest_commands(args.workflow, args.job)
        skips: List[Dict[str, str]] = []
        for paths in commands:
            for skip in collect_skips(paths):
                if skip not in skips:
                    skips.append(skip)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"[!] {lane}: {exc}", file=sys.stderr)
        return 1

    violations, allowed = judge(skips, entries, args.workflow)
    for line in allowed:
        print(f"    skips here, allowlisted: {line}")

    if violations:
        print(
            f"[!] {len(violations)} test file(s) skip wholesale in {lane}, so "
            f"none of their tests run here. Install what the file needs in this "
            f"lane, run it from a lane that has it, or list it under "
            f"module_skips in {lanes.ALLOWLIST_PATH.name} with a reason and the "
            f"lane that runs it:",
            file=sys.stderr,
        )
        for line in violations:
            print(f"    - {line}", file=sys.stderr)
        return 1

    print(
        f"[OK] {lane}: {len(commands)} pytest command(s) collected; "
        f"{len(allowed)} wholesale skip(s), all allowlisted."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
