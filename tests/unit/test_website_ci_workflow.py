# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The website build opts into retries; the deploy build does not.

``website/src/data/github.ts`` retries 403/429 only when
``GITHUB_FETCH_RETRIES`` is set, so the opt-in lives here rather than in the
source. Website CI sets it on the build step alone, keeping ``npm test`` at the
default of no retries. Deploy builds set nothing, so a rate limit still fails
the production build loudly instead of hiding behind a wait.
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WEBSITE_CI = REPO / ".github" / "workflows" / "website-ci.yml"
DEPLOY = REPO / ".github" / "workflows" / "deploy_website.yml"


def _steps(workflow: Path, job: str):
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"][job]["steps"]


def test_astro_build_step_opts_into_retries():
    build = next(
        s for s in _steps(WEBSITE_CI, "build") if s.get("name") == "Build (astro build)"
    )
    assert build["env"]["GITHUB_FETCH_RETRIES"] == "3"


def test_retries_are_not_set_at_the_job_level():
    wf = yaml.safe_load(WEBSITE_CI.read_text(encoding="utf-8"))
    assert "GITHUB_FETCH_RETRIES" not in (wf["jobs"]["build"].get("env") or {})
    assert "GITHUB_FETCH_RETRIES" not in (wf.get("env") or {})


def test_deploy_build_has_no_retry_opt_in():
    wf = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))
    assert "GITHUB_FETCH_RETRIES" not in (wf.get("env") or {})
    for job in wf["jobs"].values():
        assert "GITHUB_FETCH_RETRIES" not in (job.get("env") or {})
        for step in job.get("steps", []):
            assert "GITHUB_FETCH_RETRIES" not in (step.get("env") or {})
