# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
release_train_dryrun.yml must be unable to publish, and its edits to the shared
build workflows must leave every other caller exactly where it was.

The dry run builds every release artifact from a named commit, so it is one
`npm publish` or one `secrets.` away from shipping a release nobody approved.
These assertions are the guard rail: a dispatch-or-nightly trigger, a read-only token,
no environment, no secret, no publish step, and only the build workflows as
callees. The one write grant (the Agent UI installers) is pinned to the shape
that keeps it unreachable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DRYRUN = WORKFLOWS / "release_train_dryrun.yml"

# Reusable workflows the dry run may call: builds and tests, nothing that uploads.
ALLOWED_CALLEES = {
    "./.github/workflows/build-installers.yml",
    "./.github/workflows/build_flagship_sidecar.yml",
    "./.github/workflows/test_agent_gaia_npm.yml",
    "./.github/workflows/tui_installers.yml",
    "./.github/workflows/windows_setup.yml",
}

# build-installers.yml declares id-token: write (SignPath) and contents: write
# (its opt-in publish_to_release job), and GitHub won't start a reusable
# workflow whose jobs ask for more than the caller grants.
WRITE_GRANT_JOBS = {"ui-rc", "ui-final"}
WRITE_GRANT = {"contents": "write", "id-token": "write"}

# Anything that uploads, tags, signs for release, or dispatches another workflow.
FORBIDDEN = [
    r"npm\s+publish",
    r"npm_publish_once",
    r"npm\s+dist-tag",
    r"npm\s+deprecate",
    r"twine\s+upload",
    r"gh-action-pypi-publish",
    r"action-gh-release",
    r"github_release_retry",
    r"\bgh\s+release\b",
    r"\bgh\s+workflow\s+run\b",
    r"\bgh\s+api\b",
    r"publish_to_r2",
    r"wrangler",
    r"\baws\s+s3\b",
    r"rclone",
    r"\bgit\s+push\b",
    r"\bgit\s+tag\b",
    r"curl\s+[^\n]*-X\s*(POST|PUT|PATCH|DELETE)",
]


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare `on:` key as the boolean True (YAML 1.1).
    return workflow.get("on", workflow.get(True)) or {}


@pytest.fixture(scope="module")
def text() -> str:
    assert DRYRUN.is_file(), f"{DRYRUN} is missing"
    return DRYRUN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(text) -> dict:
    return yaml.safe_load(text)


def _code(text: str) -> str:
    """The workflow without its comments, so prose can name what code may not do."""
    return "\n".join(
        line.split(" #")[0]
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


def test_only_a_maintainer_or_the_nightly_can_start_it(workflow):
    """Never a push or PR: those run a contributor's code with this cache scope."""
    assert set(_triggers(workflow)) == {"workflow_dispatch", "schedule"}


def test_the_nightly_runs_once_a_day_off_the_hour(workflow):
    (entry,) = _triggers(workflow)["schedule"]
    minute, hour, *rest = entry["cron"].split()
    assert rest == ["*", "*", "*"], entry
    assert minute.isdigit() and int(minute) != 0, "GitHub drops :00 runs first"
    assert hour.isdigit(), entry


def test_the_rc_tag_is_optional_so_the_nightly_can_derive_it(workflow):
    spec = _triggers(workflow)["workflow_dispatch"]["inputs"]["rc_tag"]
    assert spec["required"] is False and spec["default"] == ""
    run = "\n".join(s.get("run", "") for s in workflow["jobs"]["plan"]["steps"])
    assert "release_train.py next-tag" in run


def test_the_nightly_skips_on_forks(workflow):
    cond = workflow["jobs"]["plan"]["if"]
    assert "github.event_name != 'schedule'" in cond
    assert "github.repository == 'amd/gaia'" in cond


GATE = "needs.plan.outputs.build == 'true'"


def test_a_commit_that_already_passed_builds_nothing(workflow):
    """Every job but plan is gated on plan's build output, directly or through
    an ancestor whose success it requires; else a skipped nightly still builds."""
    jobs = workflow["jobs"]
    gated = {n for n, j in jobs.items() if GATE in str(j.get("if", ""))}
    assert gated, "no job reads the build output"
    for name, job in jobs.items():
        if name == "plan" or name in gated:
            continue
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        cond = str(job.get("if", ""))
        via = [n for n in needs if n in gated and (not cond or f"needs.{n}." in cond)]
        assert via, f"{name} can run when the nightly skipped the build"
        gated.add(name)
    # A failed plan still wrote build=true before failing; require its success.
    for name, job in jobs.items():
        if GATE in str(job.get("if", "")):
            assert "needs.plan.result == 'success'" in job["if"], name


def test_only_plan_holds_the_token_and_only_to_read_runs(workflow):
    plan = workflow["jobs"]["plan"]
    assert plan["permissions"] == {"contents": "read", "actions": "read"}
    users = [s["name"] for s in plan["steps"] if "github.token" in str(s.get("env"))]
    assert users == ["Skip a commit that already passed"]


def test_the_token_is_read_only_by_default(workflow):
    assert workflow.get("permissions") == {"contents": "read"}


def test_no_job_has_an_environment(workflow):
    with_env = [name for name, job in workflow["jobs"].items() if "environment" in job]
    assert not with_env, f"jobs with an environment (and its secrets): {with_env}"


def test_no_secret_is_referenced_or_passed(text, workflow):
    code = _code(text)
    assert not re.search(
        r"\bsecrets\s*[.:\[]", code
    ), "the dry run must not read a secret"
    assert "GITHUB_TOKEN" not in code
    assert code.count("github.token") == 1, "only plan may use the run's token"
    for name, job in workflow["jobs"].items():
        assert "secrets" not in job, f"{name} passes secrets to {job.get('uses')}"


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_no_publish_step(text, pattern):
    assert not re.search(
        pattern, _code(text)
    ), f"publish-like command matches {pattern!r}"


def test_only_build_workflows_are_called(workflow):
    for name, job in workflow["jobs"].items():
        uses = job.get("uses")
        if uses:
            assert uses in ALLOWED_CALLEES, f"{name} calls {uses}"


def test_job_tokens_are_read_only_except_the_installer_build(workflow):
    for name, job in workflow["jobs"].items():
        perms = job.get("permissions")
        if perms is None:
            continue
        if name in WRITE_GRANT_JOBS:
            assert perms == WRITE_GRANT, f"{name} must grant exactly {WRITE_GRANT}"
            continue
        writes = {k: v for k, v in perms.items() if v not in ("read", "none")}
        assert not writes, f"{name} grants {writes}"


def test_the_installer_write_grant_cannot_be_used(workflow):
    for name in WRITE_GRANT_JOBS:
        job = workflow["jobs"][name]
        assert job["uses"] == "./.github/workflows/build-installers.yml"
        assert job["with"]["publish_to_release"] is False
        assert "secrets" not in job
    callee = _load(WORKFLOWS / "build-installers.yml")
    for job_name, job in callee["jobs"].items():
        perms = job.get("permissions") or {}
        if perms.get("contents") == "write":
            assert "inputs.publish_to_release == true" in job.get("if", ""), (
                f"build-installers.yml job {job_name} can write contents without "
                "publish_to_release; the dry run's grant would reach it"
            )


def test_it_only_builds_its_own_commit(workflow):
    """A foreign checkout would run that code with this branch's cache scope.

    Later builds on the branch, releases included, restore those caches, so the
    dry run takes no ref to build: it is dispatched on the commit it builds.
    """
    assert set(_triggers(workflow)["workflow_dispatch"]["inputs"]) == {"rc_tag"}
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                opts = step.get("with") or {}
                assert not {"ref", "repository"} & set(
                    opts
                ), f"{name} checks out {opts}"
        if job.get("uses") == "./.github/workflows/build-installers.yml":
            assert job["with"]["ref"] == "${{ github.sha }}", name


@pytest.mark.parametrize("flavor", ["rc", "final"])
def test_the_setup_waits_on_the_binaries_not_the_packages(workflow, flavor):
    jobs = workflow["jobs"]
    binaries = jobs[f"tui-{flavor}-binaries"]
    assert binaries["needs"] == "plan", "the binaries need no sidecar"
    assert binaries["with"]["installers"] is False
    packages = jobs[f"tui-{flavor}"]
    assert packages["with"]["build_binaries"] is False
    assert f"tui-{flavor}-binaries" in packages["needs"]
    setup = jobs[f"setup-{flavor}"]
    assert f"tui-{flavor}-binaries" in setup["needs"]
    assert f"tui-{flavor}" not in setup["needs"]
    for name in (f"tui-{flavor}-binaries", f"tui-{flavor}"):
        assert name in jobs["verify"]["needs"], f"verify does not gate on {name}"


def test_a_windows_setup_from_this_run_skips_the_fetch_hop():
    jobs = _load(WORKFLOWS / "windows_setup.yml")["jobs"]
    assert jobs["agent-ui-setup"]["if"] == "inputs.agent_ui_artifact == ''"
    build = jobs["build"]
    assert "!cancelled()" in build["if"]
    assert "needs.agent-ui-setup.result == 'success'" in build["if"]
    assert (
        "(inputs.agent_ui_artifact != '' && needs.agent-ui-setup.result == 'skipped')"
        in build["if"]
    )
    (check,) = [
        s
        for s in build["steps"]
        if s.get("name") == "Check the desktop app setup from this run"
    ]
    assert check["if"] == "inputs.agent_ui_artifact != ''"
    assert (
        "verify(" in check["run"] and "exactly one desktop app source" in check["run"]
    )
    # A skipped fetch must not skip these through the implicit success().
    assert jobs["smoke"]["if"] == (
        "${{ !cancelled() && needs.build.result == 'success' }}"
    )
    assert jobs["ok"]["if"] == "${{ !cancelled() && needs.smoke.result == 'success' }}"


def test_job_conditions_survive_a_skipped_ancestor(workflow):
    for name, job in workflow["jobs"].items():
        cond = str(job.get("if", ""))
        if "needs." in cond:
            assert "!cancelled()" in cond or "always()" in cond, name


# ── The shared build workflows: new inputs only, defaulting to today ─────────

NEW_INPUTS = {
    "build-installers.yml": ["ref", "artifact_prefix"],
    "tui_installers.yml": ["artifact_prefix"],
    "windows_setup.yml": ["artifact_prefix", "agent_ui_artifact"],
}


@pytest.mark.parametrize("filename,names", sorted(NEW_INPUTS.items()))
def test_new_callee_inputs_are_optional_and_empty_by_default(filename, names):
    inputs = _triggers(_load(WORKFLOWS / filename))["workflow_call"]["inputs"]
    for name in names:
        spec = inputs[name]
        assert spec.get("required") is False, f"{filename}: {name} must be optional"
        assert spec.get("default") == "", f"{filename}: {name} must default to ''"


def test_tui_sidecar_still_defaults_to_pinned():
    inputs = _triggers(_load(WORKFLOWS / "tui_installers.yml"))["workflow_call"][
        "inputs"
    ]
    assert inputs["sidecar"]["default"] == "pinned"


@pytest.mark.parametrize("filename", sorted(NEW_INPUTS))
def test_prefixed_artifact_names_reduce_to_todays(filename):
    """With an empty prefix every artifact name is the one live callers read."""
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    for line in text.splitlines():
        if "inputs.artifact_prefix" in line and re.match(r"\s+name:", line):
            today = line.replace("${{ inputs.artifact_prefix }}", "")
            assert "artifact_prefix" not in today, line


def test_a_nightly_and_a_dispatch_never_overlap(workflow):
    assert workflow["concurrency"] == {
        "group": "release-train-dryrun-${{ github.ref }}",
        "cancel-in-progress": False,
    }


def test_the_skip_requires_a_build_that_is_still_downloadable(workflow):
    (step,) = [
        s
        for s in workflow["jobs"]["plan"]["steps"]
        if s.get("name") == "Skip a commit that already passed"
    ]
    assert "release_train.py verified-run" in step["run"]
    assert "gh run list" not in step["run"], "a plain pass may have expired artifacts"
