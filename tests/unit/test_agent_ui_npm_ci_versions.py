# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent UI installs must match the npm version Dependabot uses.

Dependabot writes `src/gaia/apps/webui/package-lock.json` with npm 11. Most
workflows that install that lockfile pin Node 22, whose bundled npm is 10, so
those steps now install with npm 11 while keeping Node and npm caching as they
were. The one Node 24 install is left bare because Node 24 already ships npm 11.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = "src/gaia/apps/webui"
CACHE_PATH = f"{TARGET}/package-lock.json"
NPM_CI = re.compile(r"(?:^|\s)(?:npx\s+-y\s+)?npm(?:@[^\s]+)?\s+ci(?:\s|$)")

# workflow, job, node version, expected npm install command, npm cache path
CASES = [
    (
        ".github/workflows/build-installers.yml",
        "build",
        "22",
        "npx -y npm@11 ci",
        CACHE_PATH,
    ),
    (
        ".github/workflows/build_core.yml",
        "freeze",
        "22",
        "npx -y npm@11 ci",
        CACHE_PATH,
    ),
    (".github/workflows/publish.yml", "build-npm", "22", "npx -y npm@11 ci", None),
    (".github/workflows/publish.yml", "publish-npm", "24", "npm ci --omit=dev", None),
    (
        ".github/workflows/pypi.yml",
        "build",
        "22",
        "npx -y npm@11 ci --ignore-scripts",
        None,
    ),
    (
        ".github/workflows/release_train_dryrun.yml",
        "core-dist",
        "22",
        "npx -y npm@11 ci",
        None,
    ),
    (
        ".github/workflows/test_electron.yml",
        "test-webui-vitest",
        "22",
        "npx -y npm@11 ci",
        CACHE_PATH,
    ),
    (
        ".github/workflows/test_electron.yml",
        "test-sigterm-quit",
        "22",
        "npx -y npm@11 ci",
        CACHE_PATH,
    ),
    (
        ".github/workflows/test_setup_journey.yml",
        "journey",
        "22",
        "npx -y npm@11 ci",
        CACHE_PATH,
    ),
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


def _installing_jobs(workflow):
    jobs = set()
    for job_name, job in workflow["jobs"].items():
        default_wd = ((job.get("defaults") or {}).get("run") or {}).get(
            "working-directory", ""
        )
        for step in job.get("steps") or []:
            working_directory = step.get("working-directory") or default_wd
            if working_directory != TARGET:
                continue
            if _npm_ci_lines(step):
                jobs.add(job_name)
    return jobs


@pytest.mark.parametrize(
    ("workflow_path", "job_name", "node", "command", "cache_path"), CASES
)
def test_agent_ui_install_matches_dependabot_npm(
    workflows, workflow_path, job_name, node, command, cache_path
):
    job = workflows[workflow_path]["jobs"][job_name]
    setup = _setup_node(job)
    assert setup["with"]["node-version"] == node

    if cache_path is None:
        assert "cache" not in setup["with"]
        assert "cache-dependency-path" not in setup["with"]
    else:
        assert setup["with"]["cache"] == "npm"
        assert setup["with"]["cache-dependency-path"] == cache_path

    installs = [lines for step in job["steps"] if (lines := _npm_ci_lines(step))]
    assert installs == [[command]]


@pytest.mark.parametrize("workflow_path", sorted({case[0] for case in CASES}))
def test_every_agent_ui_install_job_is_pinned(workflows, workflow_path):
    expected = {job for path, job, *_ in CASES if path == workflow_path}
    assert _installing_jobs(workflows[workflow_path]) == expected
