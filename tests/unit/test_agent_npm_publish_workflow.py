# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Agent releases publish npm through util/npm_publish_once.py, like core (#3922).

`npm view` can answer from a stale cache, and it never compared bytes, so a
re-run could fail on an already-published version or silently skip a version
npm holds with different bytes. These tests pin the agent workflows to the
helper and run the publish step's script against a stub `python3`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
AGENT_WORKFLOWS = ["release_agent_gaia.yml", "release_agent_email.yml"]
PUBLISH_STEP = "Publish to npm (OIDC trusted publishing, provenance)"


def _all_steps(name: str):
    jobs = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]
    for job_id, job in jobs.items():
        for step in job.get("steps", []):
            yield job_id, step


def _publish_step(name: str) -> dict:
    (step,) = [s for _, s in _all_steps(name) if s.get("name") == PUBLISH_STEP]
    return step


@pytest.mark.parametrize("name", AGENT_WORKFLOWS)
def test_publishes_only_through_the_idempotent_helper(name):
    for job_id, step in _all_steps(name):
        run = step.get("run", "")
        assert "npm publish" not in run, (job_id, step.get("name"))
        assert "npm view" not in run, (job_id, step.get("name"))
    run = _publish_step(name)["run"]
    assert "util/npm_publish_once.py" in run
    assert "--dist-tag latest" in run
    for flag in ("--access public", "--provenance", "--ignore-scripts"):
        assert flag in run.split("npm_publish_once.py", 1)[1], flag


@pytest.mark.parametrize("name", AGENT_WORKFLOWS)
def test_version_reaches_the_script_through_env_not_inlined(name):
    step = _publish_step(name)
    assert "${{" not in step["run"]
    assert step["env"]["VERSION"] == "${{ steps.ver.outputs.version }}"


def _run_publish(name: str, version: str, tmp_path: Path, stub_exit: int = 0):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    stub = bindir / "python3"
    stub.write_text(
        f'#!/bin/sh\necho "$@" >> "{log.as_posix()}"\nexit {stub_exit}\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "VERSION": version,
        "GITHUB_WORKSPACE": "/ws",
    }
    res = subprocess.run(
        [bash, "-c", _publish_step(name)["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return res, calls


@pytest.mark.parametrize("name", AGENT_WORKFLOWS)
def test_final_version_calls_the_helper(name, tmp_path):
    res, calls = _run_publish(name, "1.4.0", tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert calls.strip() == (
        "/ws/util/npm_publish_once.py --dist-tag latest -- "
        "--access public --provenance --ignore-scripts"
    )


@pytest.mark.parametrize("name", AGENT_WORKFLOWS)
@pytest.mark.parametrize("version", ["1.4.0-rc.1", "1.4.0-beta", "1.4.0-0"])
def test_prerelease_never_reaches_latest(name, version, tmp_path):
    res, calls = _run_publish(name, version, tmp_path)
    assert res.returncode == 1
    assert "prerelease" in res.stdout
    assert calls == ""


@pytest.mark.parametrize("name", AGENT_WORKFLOWS)
def test_helper_failure_fails_the_step(name, tmp_path):
    res, calls = _run_publish(name, "1.4.0", tmp_path, stub_exit=1)
    assert calls
    assert res.returncode != 0
