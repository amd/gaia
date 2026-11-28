# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dependabot's npm 11 lockfiles must install in Node 22 CI.

The standalone npm Dependabot entry covers eight directories. Their PR CI jobs
pin Node 22, whose bundled npm is 10, and used to run bare `npm ci`. npm 10
rejects lockfiles npm 11 writes for optional peers, so a grouped bump could not
pass without a hand-regenerated lockfile. These jobs keep Node 22 and install
with the npm version Dependabot uses.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
NPM_CI = re.compile(r"(?:^|\s)(?:npx\s+-y\s+)?npm(?:@[^\s]+)?\s+ci(?:\s|$)")

# workflow, job, existing npm cache path (None where the job has no cache)
CASES = [
    (
        ".github/workflows/admin_worker_ci.yml",
        "test",
        "workers/admin/package-lock.json",
    ),
    (
        ".github/workflows/admin_worker_ci.yml",
        "skill-template",
        ".claude/skills/oss-dashboard/template/package-lock.json",
    ),
    (
        ".github/workflows/agent_hub_worker_ci.yml",
        "test",
        "workers/agent-hub/package-lock.json",
    ),
    (
        ".github/workflows/agent_hub_worker_ci.yml",
        "deploy",
        "workers/agent-hub/package-lock.json",
    ),
    (".github/workflows/website_router_ci.yml", "test", None),
    (".github/workflows/website-ci.yml", "build", "website/package-lock.json"),
    (
        ".github/workflows/test_vscode.yml",
        "test-vscode",
        "src/vscode/gaia/package-lock.json",
    ),
    (
        ".github/workflows/test_agent_gaia_npm.yml",
        "test-agent-gaia-npm",
        "hub/agents/gaia/npm/package-lock.json",
    ),
    (
        ".github/workflows/release_components.yml",
        "worker-check",
        "workers/agent-hub/package-lock.json",
    ),
    (
        ".github/workflows/release_components.yml",
        "deploy-worker",
        "workers/agent-hub/package-lock.json",
    ),
    (
        ".github/workflows/test_publish_pipeline.yml",
        "verify",
        "workers/agent-hub/package-lock.json",
    ),
    (".github/workflows/release_train_dryrun.yml", "lock", None),
    (".github/workflows/release_agent_gaia.yml", "tests", None),
    (".github/workflows/release_agent_gaia.yml", "publish", None),
]


@pytest.fixture(scope="module")
def workflows():
    return {
        path: yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
        for path in {case[0] for case in CASES}
    }


def _setup_node(job):
    steps = [
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    ]
    assert len(steps) == 1
    return steps[0]


def _npm_ci_lines(step):
    run = step.get("run", "")
    if not isinstance(run, str):
        return []
    return [line.strip() for line in run.splitlines() if NPM_CI.search(line)]


@pytest.mark.parametrize(("workflow_path", "job_name", "cache_path"), CASES)
def test_install_uses_the_dependabot_npm_version(
    workflows, workflow_path, job_name, cache_path
):
    job = workflows[workflow_path]["jobs"][job_name]
    setup = _setup_node(job)
    assert setup["with"]["node-version"] == "22"

    if cache_path is None:
        assert "cache" not in setup["with"]
    else:
        assert setup["with"]["cache"] == "npm"
        assert setup["with"]["cache-dependency-path"] == cache_path

    installs = [lines for step in job["steps"] if (lines := _npm_ci_lines(step))]
    assert installs
    assert all(line == "npx -y npm@11 ci" for lines in installs for line in lines)
