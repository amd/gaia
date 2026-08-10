# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression: every place that states a Node.js floor for building
``src/gaia/apps/webui`` must agree with the package's own ``engines.node``
(issue #2879).

The locked ``vite``/``rolldown`` toolchain declares ``^20.19.0 || >=22.12.0``;
below that, the build fails at parse time (``node:util`` has no ``styleText``
export before Node 20.12) or the bundler itself refuses with ``EBADENGINE``.
``engines.node`` is the single source of truth for the floor -- every CI job
that actually builds the webui, and both installer scripts' hardcoded Node
gate, must be at or above it, or a contributor/packager on a too-old Node
gets a cryptic bundler crash instead of an actionable error.

Modeled on tests/unit/test_amd_gaia_urls.py -- structural scan + invariant,
fails CI on drift, never hardcodes either side of the comparison.
"""

import json
import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WEBUI_PKG = _ROOT / "src" / "gaia" / "apps" / "webui" / "package.json"
_WORKFLOWS_DIR = _ROOT / ".github" / "workflows"
_INSTALL_SH = _ROOT / "scripts" / "install-ui.sh"
_INSTALL_PS1 = _ROOT / "scripts" / "install-ui.ps1"

# The literal path a workflow step must reference for its job to be treated
# as "builds the webui" -- deliberately a plain substring check (not a glob)
# so a future job that touches this path is picked up automatically instead
# of requiring this file to be updated too.
_WEBUI_PATH_LITERAL = "src/gaia/apps/webui"

_FLOOR_RE = re.compile(r"^>=\s*(\d+)\.(\d+)\.(\d+)$")
_BARE_MAJOR_RE = re.compile(r"^(\d+)$")


def _read_floor() -> tuple:
    """Parse engines.node from the webui package.json into a (major, minor,
    patch) tuple. Raises rather than guessing if the declared range isn't a
    simple '>=X.Y.Z' floor -- a range shape this guard can't confidently
    reason about must fail loud, not pass silently."""
    data = json.loads(_WEBUI_PKG.read_text(encoding="utf-8"))
    node_range = data.get("engines", {}).get("node")
    assert node_range, f"{_WEBUI_PKG} declares no engines.node"
    match = _FLOOR_RE.match(node_range.strip())
    assert match, (
        f"{_WEBUI_PKG} engines.node={node_range!r} is not a simple '>=X.Y.Z' "
        "floor this guard knows how to parse"
    )
    return tuple(int(g) for g in match.groups())


def _bare_major_satisfies_floor(pin: str, floor: tuple) -> bool:
    """A bare major CI pin (e.g. '20', '24') is assumed to resolve to the
    latest patch of that major -- setup-node's documented behavior, verified
    once against a live green run (see PR body), not re-checked per run.
    Any other pin shape (an exact version, a range) is not evaluated here;
    the caller raises rather than silently passing it."""
    match = _BARE_MAJOR_RE.match(pin.strip())
    assert match, (
        f"CI node-version pin {pin!r} is not a bare major this guard knows "
        "how to evaluate against the engines.node floor"
    )
    return int(match.group(1)) >= floor[0]


def _find_webui_building_jobs() -> list:
    """Walk every workflow file (not a hardcoded list -- the file list is
    itself drift) and return (workflow_path, job_name, node_version_pin) for
    each job that both pins actions/setup-node's node-version AND references
    the literal webui path somewhere in the job (run/working-directory/
    cache-dependency-path)."""
    found = []
    for wf_path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        doc = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            if _WEBUI_PATH_LITERAL not in yaml.dump(job):
                continue
            node_version = None
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses", "")
                if isinstance(uses, str) and "actions/setup-node" in uses:
                    node_version = (step.get("with") or {}).get("node-version")
            if node_version is not None:
                found.append((wf_path, job_name, str(node_version)))
    return found


def _installer_gate_major(path: Path, pattern: str) -> int:
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    assert match, f"{path} does not contain the expected Node major-version gate"
    return int(match.group(1))


def test_webui_engines_node_floor_is_declared():
    floor = _read_floor()
    assert floor >= (1, 0, 0)


def test_ci_jobs_building_webui_satisfy_the_engines_floor():
    floor = _read_floor()
    webui_jobs = _find_webui_building_jobs()
    assert webui_jobs, (
        "selector found no job building src/gaia/apps/webui across "
        f"{_WORKFLOWS_DIR}/*.yml -- it broke, or the webui path moved"
    )

    offenders = []
    for wf_path, job_name, pin in webui_jobs:
        try:
            satisfied = _bare_major_satisfies_floor(pin, floor)
        except AssertionError as exc:
            offenders.append(f"{wf_path.name}:{job_name}: {exc}")
            continue
        if not satisfied:
            offenders.append(
                f"{wf_path.name}:{job_name}: node-version {pin!r} is below the "
                f"engines.node floor {'.'.join(map(str, floor))}"
            )

    assert not offenders, (
        "CI job(s) building the webui pin a Node version below engines.node "
        "(issue #2879):\n  " + "\n  ".join(offenders)
    )


def test_installer_scripts_gate_satisfies_the_engines_floor():
    floor = _read_floor()
    sh_major = _installer_gate_major(_INSTALL_SH, r'"\$NODE_VERSION"\s*-lt\s*(\d+)')
    ps1_major = _installer_gate_major(_INSTALL_PS1, r"\$nodeMajor\s*-lt\s*(\d+)")

    assert sh_major >= floor[0], (
        f"{_INSTALL_SH} gates on Node major {sh_major}, below the engines.node "
        f"floor major {floor[0]}"
    )
    assert ps1_major >= floor[0], (
        f"{_INSTALL_PS1} gates on Node major {ps1_major}, below the "
        f"engines.node floor major {floor[0]}"
    )
