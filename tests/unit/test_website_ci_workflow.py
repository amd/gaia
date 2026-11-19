# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""PR builds use the checked-in release fixture; live builds fetch GitHub.

Website CI sets ``GAIA_GITHUB_FIXTURE`` only for pull request events, so the
build reads ``tests/fixtures/github/releases.json`` instead of spending the
shared hourly GitHub API quota. Main builds leave it empty and fetch live. The
deploy workflow sets neither the fixture nor retries.

``website/src/data/github.ts`` still retries 403/429 only when
``GITHUB_FETCH_RETRIES`` is set. Website CI sets it on live build steps alone,
keeping ``npm test`` and PR fixture builds at the default of no retries.
"""

import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WEBSITE_CI = REPO / ".github" / "workflows" / "website-ci.yml"
INSTALLER_CI = REPO / ".github" / "workflows" / "test_installers_headless.yml"
DEPLOY = REPO / ".github" / "workflows" / "deploy_website.yml"
FIXTURE = REPO / "tests" / "fixtures" / "github" / "releases.json"


def _workflow(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow: Path, job: str):
    return _workflow(workflow)["jobs"][job]["steps"]


def _astro_build():
    return next(
        step
        for step in _steps(WEBSITE_CI, "build")
        if step.get("name") == "Build (astro build)"
    )


def test_astro_build_step_opts_into_retries_for_live_events():
    value = _astro_build()["env"]["GITHUB_FETCH_RETRIES"]
    assert "github.event_name != 'pull_request'" in value
    assert "'3'" in value


def test_pr_build_uses_the_checked_in_release_fixture():
    value = _astro_build()["env"]["GAIA_GITHUB_FIXTURE"]
    assert "github.event_name == 'pull_request'" in value
    assert "tests/fixtures/github/releases.json" in value


def test_retries_and_fixture_are_not_set_at_the_job_level():
    wf = _workflow(WEBSITE_CI)
    for key in ("GITHUB_FETCH_RETRIES", "GAIA_GITHUB_FIXTURE"):
        assert key not in (wf["jobs"]["build"].get("env") or {})
        assert key not in (wf.get("env") or {})


def test_deploy_build_has_no_retry_or_fixture_opt_in():
    wf = _workflow(DEPLOY)
    for key in ("GITHUB_FETCH_RETRIES", "GAIA_GITHUB_FIXTURE"):
        assert key not in (wf.get("env") or {})
        for job in wf["jobs"].values():
            assert key not in (job.get("env") or {})
            for step in job.get("steps", []):
                assert key not in (step.get("env") or {})


def test_fixture_is_checked_in_with_a_stable_and_a_candidate():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["repo"]["stargazers_count"] > 0
    assert data["repo"]["forks_count"] > 0
    releases = data["releases"]
    assert any(release["prerelease"] for release in releases)
    assert any(not release["prerelease"] for release in releases)
    for release in releases:
        assert release["tag_name"].startswith("v")
        assert release["assets"]
    stable = next(release for release in releases if not release["prerelease"])
    names = {asset["name"] for asset in stable["assets"]}
    assert any(name.endswith(".dmg") for name in names)
    assert any("linux" in name for name in names)


def test_fixture_changes_rerun_both_workflows():
    fixture = "tests/fixtures/github/releases.json"
    for workflow in (WEBSITE_CI, INSTALLER_CI):
        paths = _workflow(workflow)[True]["pull_request"]["paths"]
        assert fixture in paths
