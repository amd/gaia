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
AGENT_WORKFLOWS = sorted(p.name for p in WORKFLOWS.glob("release_agent_*.yml"))
PUBLISH_STEP = "Publish to npm (OIDC trusted publishing, provenance)"
VERSION_STEP = "Resolve + validate release version"


def _all_steps(name: str):
    jobs = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]
    for job_id, job in jobs.items():
        for step in job.get("steps", []):
            yield job_id, step


def _publish_step(name: str) -> dict:
    (step,) = [s for _, s in _all_steps(name) if s.get("name") == PUBLISH_STEP]
    return step


def test_glob_finds_every_known_agent_workflow():
    assert {"release_agent_gaia.yml", "release_agent_email.yml"} <= set(AGENT_WORKFLOWS)


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


def _run_version_check(name: str, tag: str, tmp_path: Path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    (step,) = [
        (job_id, s) for job_id, s in _all_steps(name) if s.get("name") == VERSION_STEP
    ]
    job_id, step = step
    workflow = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    npm_pkg = workflow["env"].get("NPM_PKG", "unused")
    version = tag.rsplit("-v", 1)[1]
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # node/python stubs answer as if package.json and the manifest agree with the tag.
    for tool in ("node", "python"):
        stub = bindir / tool
        stub.write_text(
            '#!/bin/sh\ncase "$*" in *.name*) echo "$STUB_NAME";; '
            '*) echo "$STUB_VER";; esac\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
    out = tmp_path / "out"
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "EVENT_NAME": "push",
        "GITHUB_REF_NAME": tag,
        "GITHUB_OUTPUT": str(out),
        "PKG_DIR": str(pkg_dir.relative_to(tmp_path)),
        "MANIFEST": "gaia-agent.yaml",
        "NPM_PKG": npm_pkg,
        "HUB_PREFIX": "agents/x",
        "STUB_VER": version,
        "STUB_NAME": npm_pkg,
    }
    res = subprocess.run(
        [bash, "-c", step["run"]],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    written = out.read_text(encoding="utf-8") if out.exists() else ""
    return job_id, res, written


def _tag(name: str, version: str) -> str:
    agent = name[len("release_agent_") : -len(".yml")]
    return f"agent-pkg-{agent}-v{version}"


@pytest.mark.parametrize("name", AGENT_WORKFLOWS)
def test_version_job_accepts_a_final_version(name, tmp_path):
    _, res, written = _run_version_check(name, _tag(name, "1.4.0"), tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "version=1.4.0" in written


@pytest.mark.parametrize("name", AGENT_WORKFLOWS)
@pytest.mark.parametrize("version", ["1.4.0-rc.1", "1.4.0-beta", "1.4.0-0"])
def test_version_job_rejects_prerelease_before_anything_publishes(
    name, version, tmp_path
):
    job_id, res, written = _run_version_check(name, _tag(name, version), tmp_path)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "prerelease" in res.stdout
    assert "version=" not in written


@pytest.mark.parametrize("name", AGENT_WORKFLOWS)
def test_version_check_runs_before_any_publish_step(name):
    jobs = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]
    (ver_job,) = [
        j
        for j, job in jobs.items()
        if any(s.get("name") == VERSION_STEP for s in job.get("steps", []))
    ]
    for job_id, job in jobs.items():
        names = [s.get("name", "") for s in job.get("steps", [])]
        publishes = [i for i, n in enumerate(names) if n.lower().startswith("publish")]
        if not publishes:
            continue
        if job_id == ver_job:
            assert names.index(VERSION_STEP) < min(publishes), names
        else:
            # The version job must be a root that gates the publishing job.
            assert jobs[ver_job].get("needs") in (None, [])
            needs = job.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            assert ver_job in needs or any(
                ver_job
                in (
                    [jobs[n]["needs"]]
                    if isinstance(jobs[n].get("needs"), str)
                    else jobs[n].get("needs", [])
                )
                for n in needs
            ), (job_id, needs)
