# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A stalled installer download must not spend the smoke test's budget.

v0.26.0-rc33 was cancelled because one 478 MB linux-installer download filled
the AppImage structural smoke job's 10 minute timeout. Every full-installer
download now carries its own timeout and one retry, and the release gate names
a cancelled job instead of calling it a failed check.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-installers.yml"
DOWNLOAD = "actions/download-artifact@"
OUTCOME_GUARD = ".outcome == 'failure'"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _installer_downloads(workflow: dict):
    """Yield ``(job_id, index, step)`` for each first-attempt installer fetch.

    The validate job fetches every artifact with a pattern; the smoke jobs
    fetch one platform bundle by name. Wheel downloads and retry steps are not
    installer fetches.
    """
    for job_id, job in workflow["jobs"].items():
        for i, step in enumerate(job.get("steps", [])):
            if not str(step.get("uses", "")).startswith(DOWNLOAD):
                continue
            if OUTCOME_GUARD in str(step.get("if", "")):
                continue
            with_ = step.get("with", {})
            if with_.get("pattern") or str(with_.get("name", "")).endswith(
                "-installer"
            ):
                yield job_id, i, step


def test_the_scan_finds_every_installer_download():
    """The scan must not pass because it matched nothing or missed a job."""
    sites = {job_id for job_id, _, _ in _installer_downloads(_workflow())}
    assert sites == {
        "validate-artifacts",
        "appimage-structural-smoke",
        "dmg-structural-smoke",
        "appimage-distro-matrix",
        "appimage-userns-restricted",
        "publish-release-assets",
    }


def test_every_installer_download_has_a_timeout_and_one_retry():
    workflow = _workflow()
    downloads = list(_installer_downloads(workflow))
    assert downloads
    for job_id, index, first in downloads:
        steps = workflow["jobs"][job_id]["steps"]
        assert first.get("id"), f"{job_id}: the first attempt needs an id"
        assert first.get("timeout-minutes"), f"{job_id}: no download timeout"
        assert (
            first.get("continue-on-error") is True
        ), f"{job_id}: the first attempt must not fail the job before the retry"

        assert index + 1 < len(steps), f"{job_id}: no retry follows the download"
        retry = steps[index + 1]
        assert str(retry.get("uses", "")).startswith(
            DOWNLOAD
        ), f"{job_id}: no retry follows the download"
        assert (
            retry.get("if") == f"steps.{first['id']}{OUTCOME_GUARD}"
        ), f"{job_id}: the retry must run only after a failed first attempt"
        assert retry.get("timeout-minutes"), f"{job_id}: the retry has no timeout"
        assert not retry.get(
            "continue-on-error"
        ), f"{job_id}: a failed retry must fail the job"
        assert retry.get("with") == first.get(
            "with"
        ), f"{job_id}: the retry must fetch the same artifact"


def test_two_download_attempts_fit_beside_the_test_budget():
    workflow = _workflow()
    for job_id, _, first in _installer_downloads(workflow):
        job = workflow["jobs"][job_id]
        budget = job["timeout-minutes"]
        download = first["timeout-minutes"]
        assert budget >= 2 * download + 5, (
            f"{job_id}: two {download} minute attempts leave less than 5 minutes "
            f"of test time in a {budget} minute job"
        )


def _bash() -> str:
    """The bash that can run the gate script on this platform.

    On the Windows runners ``bash`` resolves to ``C:\\Windows\\System32\\bash.exe``,
    the WSL launcher, which has no distro and exits 1 before the script starts.
    Git for Windows is installed there, and its bash runs the script as the
    Linux lane does.
    """
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        git_bash = Path(program_files) / "Git" / "bin" / "bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    return "bash"


def _gate(job_id: str, result: str) -> str:
    """Run the gate script with one job's result replaced by ``result``."""
    workflow = _workflow()
    gate = workflow["jobs"]["build-complete"]
    step = next(
        step
        for step in gate["steps"]
        if step.get("name") == "Verify all platform builds and smoke tests succeeded"
    )

    results = {job: "success" for job in gate["needs"]}
    results[job_id] = result
    script = step["run"]
    for job, value in results.items():
        script = script.replace(f"${{{{ needs.{job}.result }}}}", value)
    assert "${{" not in script

    proc = subprocess.run(
        [_bash(), "-c", script], capture_output=True, text=True, check=False
    )
    expected = 0 if result in ("success", "skipped") else 1
    assert proc.returncode == expected, proc.stdout + proc.stderr
    return proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "job_id",
    ["appimage-structural-smoke", "validate-artifacts", "dmg-structural-smoke"],
)
def test_a_cancelled_job_is_named_and_a_rerun_is_called_safe(job_id):
    output = _gate(job_id, "cancelled")
    assert f"{job_id} was cancelled" in output
    assert "No check reported a failure" in output
    assert "gh run rerun --failed" in output


@pytest.mark.parametrize(
    "job_id",
    ["build", "appimage-distro-matrix", "custom-agent-mcp-harness"],
)
def test_a_real_failure_still_names_the_job(job_id):
    output = _gate(job_id, "failure")
    assert job_id in output
    assert "failed" in output or "did not succeed" in output
    assert "No check reported a failure" not in output


def test_a_skipped_job_does_not_fail_the_gate():
    output = _gate("dmg-structural-smoke", "skipped")
    assert "All platform installers built and installer smoke suite passed." in output


class TestWhichBash:
    def test_windows_prefers_the_git_for_windows_bash(self, tmp_path, monkeypatch):
        bash = tmp_path / "Git" / "bin" / "bash.exe"
        bash.parent.mkdir(parents=True)
        bash.write_text("", encoding="utf-8")
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.setattr(sys, "platform", "win32")
        assert _bash() == str(bash)

    def test_windows_without_git_for_windows_falls_back_to_bash(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.setattr(sys, "platform", "win32")
        assert _bash() == "bash"

    def test_posix_uses_bash(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert _bash() == "bash"


BUILD_SIDE = (
    "build_agents.yml",
    "build_core.yml",
    "build_gaiapy.yml",
    "tui_installers.yml",
    "windows_setup.yml",
)


def _build_side_downloads():
    for name in BUILD_SIDE:
        wf = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        )
        for job_id, job in wf["jobs"].items():
            steps = job.get("steps", [])
            for i, step in enumerate(steps):
                if str(step.get("uses", "")).startswith(DOWNLOAD):
                    yield f"{name}:{job_id}", job, steps, i, step


@pytest.mark.parametrize("name", BUILD_SIDE)
def test_the_build_side_scan_finds_downloads(name):
    assert any(site.startswith(f"{name}:") for site, *_ in _build_side_downloads())


def test_every_build_side_download_is_bounded_and_a_retry_still_fails_loudly():
    for site, job, steps, i, step in _build_side_downloads():
        assert step.get("timeout-minutes") or job.get(
            "timeout-minutes"
        ), f"{site}: download #{i} has neither a step nor a job timeout"
        if step.get("continue-on-error") is not True:
            continue
        # A tolerated first attempt must be followed by a retry of the same
        # artifact that is guarded on the first outcome and is NOT tolerated.
        assert i + 1 < len(steps), f"{site}: tolerated download with no retry"
        retry = steps[i + 1]
        assert str(retry.get("uses", "")).startswith(DOWNLOAD), site
        assert f"steps.{step['id']}{OUTCOME_GUARD}" in str(retry.get("if", "")), site
        assert retry.get("with") == step.get("with"), f"{site}: retry differs"
        assert (
            retry.get("continue-on-error") is not True
        ), f"{site}: the second attempt must fail the job"


def test_the_release_bundle_cannot_hang_a_tagged_release():
    wf = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "build_agents.yml").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(wf["jobs"]["release-bundle"].get("timeout-minutes"), int)
