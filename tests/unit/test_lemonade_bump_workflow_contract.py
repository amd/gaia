# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Lemonade bump's changes are checked against main before a PR is opened.

GitHub runs no CI on a PR opened with GITHUB_TOKEN, so the bump workflow checks
its own result. The agent job may only read the repo and hand over a diff, a
summary and up to two proposals. verify, with a read-only token, applies the
diff to a fresh main only if main's checker accepts it and runs main's pin
tests. proposals and publish hold write tokens, run only main's standard-library
scripts, and act only on the exact bump verify checked. report files the
tracking issue. These tests pin that structure and run every step script that
handles the bump, with a fake ``gh``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests.fixtures.lemonade_bump import (
    REPO_ROOT,
    TARGET,
    git,
    honest_bump,
    local_remote,
    make_main_repo,
    outside_fences,
    patch_from,
    read,
    run_step,
    workflow,
    write,
)

AGENT_FREE = ("verify", "proposals", "publish", "report")
ARTIFACT_ID = "${{ needs.bump.outputs.artifact_id }}"
READY = "needs.bump.outputs.ready == 'true'"


@pytest.fixture(scope="module")
def jobs() -> dict:
    return workflow()["jobs"]


def _step(job: dict, name: str) -> dict:
    found = [s for s in job["steps"] if s.get("name") == name]
    assert found, f"lemonade-version-bump.yml has no {name!r} step"
    return found[0]


def _names(job: dict) -> list:
    return [s.get("name") for s in job["steps"]]


# --- Structure -------------------------------------------------------------------


def test_each_job_holds_only_the_token_it_needs(jobs):
    assert workflow()["permissions"] == {}, "every job must declare its own token"
    assert jobs["bump"]["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert jobs["verify"]["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert jobs["proposals"]["permissions"] == {"issues": "write"}
    assert jobs["publish"]["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert jobs["report"]["permissions"] == {"issues": "write"}


def test_every_job_has_a_timeout(jobs):
    for job_id, job in jobs.items():
        assert isinstance(job.get("timeout-minutes"), int), job_id
        assert 0 < job["timeout-minutes"] <= 60, job_id


def test_no_checkout_leaves_the_token_in_git_config(jobs):
    for job_id in ("bump", "verify", "proposals", "publish"):
        checkouts = [
            s
            for s in jobs[job_id]["steps"]
            if (s.get("uses") or "").startswith("actions/checkout@")
        ]
        assert len(checkouts) == 1, job_id
        assert checkouts[0]["with"]["persist-credentials"] is False, job_id
    assert jobs["report"]["steps"][0].get("uses") is None, "report checks out nothing"
    assert _step(jobs["bump"], "Checkout repository")["with"]["ref"] == "main"
    assert _step(jobs["verify"], "Checkout main")["with"]["ref"] == "main"
    for job_id in ("proposals", "publish"):
        checkout = _step(jobs[job_id], "Checkout the main commit verify checked")
        assert checkout["with"]["ref"] == "${{ needs.verify.outputs.main_sha }}"
    assert jobs["verify"]["outputs"]["main_sha"] == "${{ steps.main.outputs.sha }}"


def test_the_agent_runs_only_in_the_read_only_job(jobs):
    def agents(job):
        return [
            s for s in job["steps"] if "claude-code-action" in (s.get("uses") or "")
        ]

    assert len(agents(jobs["bump"])) == 1
    for job_id in AGENT_FREE:
        assert agents(jobs[job_id]) == [], job_id
    prompt = agents(jobs["bump"])[0]["with"]["prompt"]
    for command in ("gh pr create", "gh pr edit", "git push", "gh issue create"):
        assert command not in prompt, f"the agent's prompt still runs `{command}`"


def test_no_expression_is_expanded_inside_a_script(jobs):
    """Every value reaches agent-free scripts through env, never pasted into the code."""
    for job_id in AGENT_FREE:
        for step in jobs[job_id]["steps"]:
            assert "${{" not in (step.get("run") or ""), (job_id, step.get("name"))


def test_the_write_jobs_run_no_code_from_the_bump(jobs):
    for job_id in ("proposals", "publish", "report"):
        for step in jobs[job_id]["steps"]:
            run = step.get("run") or ""
            assert "pip install" not in run and "pytest" not in run, step["name"]
            for line in run.splitlines():
                if re.match(r"\s*python\b", line):
                    assert re.match(r"\s*python -I util/[a-z_]+\.py\b", line), line


def test_the_jobs_run_in_order_and_only_on_a_packaged_bump(jobs):
    bump = jobs["bump"]
    assert bump["outputs"]["ready"] == "${{ steps.package.outputs.ready }}"
    assert bump["outputs"]["stopped"] == "${{ steps.package.outputs.stopped }}"
    assert bump["outputs"]["artifact_id"] == "${{ steps.upload.outputs.artifact-id }}"
    upload = _step(bump, "Upload the bump")
    assert upload["id"] == "upload"
    assert upload["if"] == "steps.package.outputs.ready == 'true'"
    assert upload["with"]["if-no-files-found"] == "error"
    assert jobs["verify"]["needs"] == "bump"
    assert jobs["verify"]["if"] == f"success() && {READY}"
    assert sorted(jobs["proposals"]["needs"]) == ["bump", "verify"]
    assert jobs["proposals"]["if"] == f"success() && {READY}"
    assert sorted(jobs["publish"]["needs"]) == ["bump", "proposals", "verify"]
    assert jobs["publish"]["if"] == f"success() && {READY}"
    assert sorted(jobs["report"]["needs"]) == ["bump", "proposals", "publish", "verify"]
    condition = " ".join(jobs["report"]["if"].split())
    assert condition.startswith("always() && (")
    for clause in (
        "needs.bump.result == 'failure'",
        "needs.bump.outputs.stopped == 'true'",
        "needs.verify.result == 'failure'",
        "needs.proposals.result == 'failure'",
        "needs.publish.result == 'failure'",
        "contains(needs.*.result, 'cancelled')",
    ):
        assert clause in condition, clause


def test_later_jobs_trust_only_verifys_target(jobs):
    """The agent's runner reports the bump job's outputs, so they are only claims."""
    assert set(jobs["bump"]["outputs"]) == {
        "ready",
        "stopped",
        "should_bump",
        "artifact_id",
        "target_version",
    }
    verify = jobs["verify"]
    claimed = _step(verify, "Work out the target independently of the agent job")
    assert claimed["env"]["CLAIMED"] == "${{ needs.bump.outputs.target_version }}"
    assert claimed["env"]["FORCE_VERSION"] == "${{ github.event.inputs.force_version }}"
    assert (
        verify["outputs"]["target_version"]
        == "${{ steps.target.outputs.target_version }}"
    )
    checker = _step(verify, "Check and apply the bump")
    assert checker["env"]["TARGET"] == "${{ steps.target.outputs.target_version }}"
    for job_id in ("proposals", "publish"):
        for step in jobs[job_id]["steps"]:
            for key, value in (step.get("env") or {}).items():
                if "needs.bump.outputs" in str(value):
                    pytest.fail(
                        f"{job_id}/{step['name']} reads {key} from the bump job"
                    )
            if "TARGET" in (step.get("env") or {}):
                assert (
                    step["env"]["TARGET"]
                    == "${{ needs.verify.outputs.target_version }}"
                )
    assert "BRANCH" not in (_step(jobs["publish"], "Open the bump PR").get("env") or {})
    report = _step(jobs["report"], "File a tracking issue")["env"]
    assert report["TARGET"] == "${{ needs.verify.outputs.target_version }}"
    assert "BRANCH" not in report


@pytest.mark.parametrize("job_id", ["verify", "proposals", "publish"])
def test_each_job_downloads_the_artifact_by_id_with_its_digest_checked(jobs, job_id):
    download = _step(jobs[job_id], "Download the bump")
    assert download["uses"].startswith("actions/download-artifact@")
    assert download["with"]["artifact-ids"] == ARTIFACT_ID
    assert "name" not in download["with"]
    assert download["with"]["digest-mismatch"] == "error"
    assert download["with"]["merge-multiple"] is True


@pytest.mark.parametrize(
    "job_id,order",
    [
        (
            "verify",
            [
                "Checkout main",
                "Work out the target independently of the agent job",
                "Download the bump",
                "Record the digest of the bump being checked",
                "Check and apply the bump",
                "Install GAIA and test tooling",
                "Check the pins with main's tests",
            ],
        ),
        (
            "proposals",
            [
                "Checkout the main commit verify checked",
                "Download the bump",
                "Confirm this is the bump verify checked",
                "File the feature proposals",
            ],
        ),
        (
            "publish",
            [
                "Checkout the main commit verify checked",
                "Download the bump",
                "Confirm this is the bump verify checked",
                "Check and apply the bump",
                "Write the PR body",
                "Open the bump PR",
            ],
        ),
    ],
)
def test_every_check_must_pass_before_the_next_step(jobs, job_id, order):
    job = jobs[job_id]
    assert [n for n in _names(job) if n in order] == order
    for step in job["steps"]:
        assert (
            "if" not in step
        ), f"{step['name']!r} must run only if all before it passed"
        assert not step.get("continue-on-error"), step["name"]
        run = step.get("run")
        if run:
            lines = run.strip().splitlines()
            assert lines[0] == "set -euo pipefail", step["name"]
            # Only the first line may set shell options, and nothing may swallow a failure.
            later = "\n".join(lines[1:])
            assert not re.search(r"\bset\s+[-+]|\|\||\btrap\b", later), step["name"]


def test_the_pins_are_checked_strictly(jobs):
    step = _step(jobs["verify"], "Check the pins with main's tests")
    assert step["env"]["GAIA_REQUIRE_RELEASE_API"] == "1"
    assert step["env"]["GITHUB_TOKEN"]
    commands = [line.strip().rstrip("\\").strip() for line in step["run"].splitlines()]
    for target in (
        "tests/unit/test_lemonade_download_urls.py::TestEveryReleaseIsPinned",
        "tests/unit/test_lemonade_download_urls.py::TestDownloadIntegrity",
        "tests/integration/test_lemonade_embeddable_assets.py",
        "python util/check_doc_versions.py",
    ):
        assert target in commands, target


def test_the_write_jobs_compare_against_verifys_digest(jobs):
    assert (
        jobs["verify"]["outputs"]["bump_sha256"] == "${{ steps.digest.outputs.sha256 }}"
    )
    assert _step(jobs["verify"], "Record the digest of the bump being checked")[
        "id"
    ] == ("digest")
    for job_id in ("proposals", "publish"):
        confirm = _step(jobs[job_id], "Confirm this is the bump verify checked")
        assert confirm["env"]["EXPECTED"] == "${{ needs.verify.outputs.bump_sha256 }}"


def test_the_pr_body_comes_from_the_template(jobs):
    publish = jobs["publish"]
    write_step = _step(publish, "Write the PR body")
    assert "python -I util/render_lemonade_bump_pr.py pr" in write_step["run"]
    assert write_step["env"]["FOLLOW_UPS"] == "${{ needs.proposals.outputs.numbers }}"
    assert '--out "$RUNNER_TEMP/pr-body.md"' in write_step["run"]
    opener = _step(publish, "Open the bump PR")
    assert '--body-file "$RUNNER_TEMP/pr-body.md"' in opener["run"]


# --- The step scripts, run for real ----------------------------------------------


@pytest.fixture
def main_repo(tmp_path) -> Path:
    return make_main_repo(tmp_path)


def _package(repo, base, tmp_path, body, proposals=None):
    pr_body = tmp_path / "pr-body.md"
    if body is not None:
        write(pr_body, body)
    proposals_dir = tmp_path / "proposals"
    for name, text in (proposals or {}).items():
        write(proposals_dir / name, text)
    out = tmp_path / "out"
    env = {
        "BRANCH": f"deps/lemonade-{TARGET}",
        "BASE": base,
        "PR_BODY": pr_body.as_posix(),
        "PROPOSALS": proposals_dir.as_posix(),
        "OUT": out.as_posix(),
    }
    result, outputs, _ = run_step("bump", "Package the bump", repo, env, tmp_path)
    return result, outputs, out


def _base(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def _commit_bump(repo: Path) -> str:
    base = _base(repo)
    git(repo, "checkout", "-q", "-b", f"deps/lemonade-{TARGET}")
    honest_bump(repo)
    git(repo, "commit", "-qam", "bump")
    return base


def test_a_stopped_agent_packages_nothing_and_says_so(main_repo, tmp_path):
    result, outputs, out = _package(main_repo, _base(main_repo), tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert "ready=false" in outputs and "ready=true" not in outputs
    assert "stopped=true" in outputs
    assert not out.exists()


def test_a_commit_without_a_summary_fails(main_repo, tmp_path):
    base = _commit_bump(main_repo)
    result, outputs, _ = _package(main_repo, base, tmp_path, None)
    assert result.returncode != 0
    assert "ready=true" not in outputs


def test_a_branch_without_a_commit_fails(main_repo, tmp_path):
    git(main_repo, "branch", f"deps/lemonade-{TARGET}")
    result, outputs, _ = _package(main_repo, _base(main_repo), tmp_path, "summary")
    assert result.returncode != 0
    assert "ready=true" not in outputs


def test_a_finished_bump_is_packaged_as_a_diff(main_repo, tmp_path):
    base = _commit_bump(main_repo)
    write(main_repo / "pytest.ini", "[pytest]\n")
    result, outputs, out = _package(
        main_repo, base, tmp_path, "Bumps it.", {"1.md": "Idea\nBody", "2.md": "Two\nB"}
    )
    assert result.returncode == 0, result.stderr
    assert "ready=true" in outputs
    assert (out / "pr-body.md").read_bytes() == b"Bumps it."
    assert sorted(p.name for p in (out / "proposals").iterdir()) == ["1.md", "2.md"]
    patch = read(out / "bump.patch")
    assert f'+LEMONADE_VERSION = "{TARGET}"' in patch
    assert "pytest.ini" not in patch, "an untracked file must not travel"


@pytest.mark.parametrize("extra", ["3.md", "notes.txt", ".hidden"])
def test_more_than_two_proposals_fail(main_repo, tmp_path, extra):
    base = _commit_bump(main_repo)
    proposals = {"1.md": "A\nb", "2.md": "B\nb", extra: "C\nb"}
    result, outputs, _ = _package(main_repo, base, tmp_path, "summary", proposals)
    assert result.returncode != 0
    assert "may hold only 1.md and 2.md" in result.stdout
    assert "ready=true" not in outputs


def _apply_step(job_id, repo, patch: Path, tmp_path):
    env = {"BUMP": patch.parent.as_posix(), "TARGET": TARGET}
    return run_step(job_id, "Check and apply the bump", repo, env, tmp_path)[0]


@pytest.mark.parametrize("job_id", ["verify", "publish"])
def test_the_apply_step_refuses_an_edited_test(main_repo, tmp_path, job_id):
    honest_bump(main_repo)
    write(main_repo / "pytest.ini", "[pytest]\naddopts = -p evil\n")
    patch = patch_from(main_repo, tmp_path, untracked_too=True)
    result = _apply_step(job_id, main_repo, patch, tmp_path)
    assert result.returncode != 0
    assert "Rejected the Lemonade bump patch" in result.stderr
    assert not (main_repo / "pytest.ini").exists()


@pytest.mark.parametrize("job_id", ["verify", "publish"])
def test_the_apply_step_applies_an_honest_bump(main_repo, tmp_path, job_id):
    honest_bump(main_repo)
    patch = patch_from(main_repo, tmp_path)
    result = _apply_step(job_id, main_repo, patch, tmp_path)
    assert result.returncode == 0, result.stderr
    assert f'"{TARGET}"' in read(main_repo / "src/gaia/version.py")


@pytest.fixture
def bump_dir(tmp_path) -> Path:
    bump = tmp_path / "lemonade-bump"
    write(bump / "bump.patch", "diff\n")
    write(bump / "pr-body.md", "summary\n")
    write(bump / "proposals" / "1.md", "Idea\nbody\n")
    return bump


def _verify_digest(bump: Path, tmp_path: Path) -> str:
    result, outputs, _ = run_step(
        "verify",
        "Record the digest of the bump being checked",
        REPO_ROOT,
        {"BUMP": bump.as_posix()},
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    return re.search(r"^sha256=([0-9a-f]{64})$", outputs, re.M).group(1)


@pytest.mark.parametrize("job_id", ["proposals", "publish"])
@pytest.mark.parametrize(
    "tamper,expected",
    [
        (None, "verified"),
        ("patch", "verified"),
        ("new file", "verified"),
        (None, ""),
    ],
    ids=["same", "changed-patch", "added-file", "no-digest"],
)
def test_a_write_job_acts_only_on_the_bump_verify_checked(
    bump_dir, tmp_path, job_id, tamper, expected
):
    digest = _verify_digest(bump_dir, tmp_path)
    if tamper == "patch":
        write(bump_dir / "bump.patch", "diff\nmore\n")
    elif tamper == "new file":
        write(bump_dir / "proposals" / "2.md", "Another\nbody\n")
    env = {"BUMP": bump_dir.as_posix(), "EXPECTED": digest if expected else ""}
    result, _, _ = run_step(
        job_id, "Confirm this is the bump verify checked", REPO_ROOT, env, tmp_path
    )
    if tamper is None and expected:
        assert result.returncode == 0, result.stderr
    elif not expected:
        assert result.returncode != 0
        assert "verify recorded no digest" in result.stdout
    else:
        assert result.returncode != 0
        assert "is not the one verify checked" in result.stdout


def _file_proposals(bump: Path, tmp_path: Path, existing: str = "", search=None):
    env = {"BUMP": bump.as_posix(), "TARGET": TARGET, "FAKE_ISSUE_LIST": existing}
    if search is not None:
        if not shutil.which("jq"):
            pytest.skip("answering the dedupe search needs jq")
        env["FAKE_ISSUE_JSON"] = json.dumps(search)
    return run_step("proposals", "File the feature proposals", REPO_ROOT, env, tmp_path)


@pytest.mark.parametrize("extra", ["3.md", "notes.txt", ".hidden"])
def test_the_proposals_job_refuses_more_than_two(bump_dir, tmp_path, extra):
    """The privileged job enforces the cap; the agent's runner only asked for it."""
    write(bump_dir / "proposals" / "2.md", "Two\nbody\n")
    write(bump_dir / "proposals" / extra, "Three\nbody\n")
    result, outputs, calls = _file_proposals(bump_dir, tmp_path)
    assert result.returncode != 0
    assert f"at most 1.md and 2.md, not {extra}" in result.stdout
    assert calls == [], "nothing may be filed"
    assert "numbers=" not in outputs


def test_a_proposal_is_skipped_only_for_an_exact_title_match(bump_dir, tmp_path):
    title = f"feat: Idea (unlocked by Lemonade v{TARGET})"
    near_misses = [
        {"number": 7, "title": f"feat: Idea and more (unlocked by Lemonade v{TARGET})"},
        {"number": 8, "title": title.upper()},
    ]
    result, outputs, calls = _file_proposals(bump_dir, tmp_path, search=near_misses)
    assert result.returncode == 0, result.stderr
    assert "numbers=101" in outputs
    exact = near_misses + [{"number": 9, "title": title}]
    result, outputs, calls = _file_proposals(bump_dir, tmp_path, search=exact)
    assert result.returncode == 0, result.stderr
    assert "Proposal already open as #9" in result.stdout
    assert not [c for c in calls if c[:2] == ["issue", "create"]]


def test_a_proposal_title_is_capped(bump_dir, tmp_path):
    write(bump_dir / "proposals" / "1.md", "x" * 300 + "\nbody\n")
    result, _, calls = _file_proposals(bump_dir, tmp_path)
    assert result.returncode == 0, result.stderr
    create = next(c for c in calls if c[:2] == ["issue", "create"])
    title = create[create.index("--title") + 1]
    assert title == f"feat: {'x' * 100} (unlocked by Lemonade v{TARGET})"


def test_proposals_are_filed_with_a_fixed_title_and_label(bump_dir, tmp_path):
    write(bump_dir / "proposals" / "2.md", "Second idea @someone\nbody\n")
    result, outputs, calls = _file_proposals(bump_dir, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "numbers=101 102" in outputs
    creates = [c for c in calls if c[:2] == ["issue", "create"]]
    assert len(creates) == 2
    for call in creates:
        title = call[call.index("--title") + 1]
        assert title.startswith("feat: ") and title.endswith(
            f"(unlocked by Lemonade v{TARGET})"
        )
        assert "@" not in title
        assert call[call.index("--label") + 1] == "enhancement"
        body = read(Path(call[call.index("--body-file") + 1]))
        assert "Auto-proposed by the Lemonade release scan" in outside_fences(body)


def test_a_proposal_already_open_is_not_filed_again(bump_dir, tmp_path):
    result, outputs, calls = _file_proposals(bump_dir, tmp_path, existing="55")
    assert result.returncode == 0, result.stderr
    assert "numbers=\n" in outputs or outputs.rstrip().endswith("numbers=")
    assert not [c for c in calls if c[:2] == ["issue", "create"]]


def test_no_proposals_file_nothing(bump_dir, tmp_path):
    shutil.rmtree(bump_dir / "proposals")
    result, _, calls = _file_proposals(bump_dir, tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls == []


def _detect(tmp_path, force: str = "", latest: str = f"v{TARGET}"):
    if not shutil.which("jq"):
        pytest.skip("the detect step needs jq")
    env = {
        "GH_TOKEN": "x",
        "FORCE_VERSION": force,
        "FAKE_API_JSON": json.dumps({"tag_name": latest, "assets": []}),
    }
    return run_step("bump", "Detect Lemonade versions", REPO_ROOT, env, tmp_path)


@pytest.mark.parametrize(
    "force,latest",
    [
        ("2026.43.1; rm -rf /", f"v{TARGET}"),
        ("2026.43", f"v{TARGET}"),
        ("", "v2026.43.1$(id)"),
        ("", "v2026.43.1-rc1"),
    ],
    ids=["forced-injection", "forced-short", "latest-injection", "latest-rc"],
)
def test_detect_refuses_a_version_that_is_not_x_y_z(tmp_path, force, latest):
    result, outputs, calls = _detect(tmp_path, force, latest)
    assert result.returncode != 0
    assert "is not an X.Y.Z Lemonade version" in result.stdout
    assert "target_version=" not in outputs
    if force:
        assert all("tags/" not in " ".join(call) for call in calls)


def _report(tmp_path: Path, **env):
    base = {
        "GH_TOKEN": "x",
        "TARGET": TARGET,
        "BRANCH": f"deps/lemonade-{TARGET}",
        "SHOULD_BUMP": "true",
        "STOPPED": "",
        "BUMP_RESULT": "success",
        "FAKE_ISSUE_LIST": "",
    }
    return run_step(
        "report", "File a tracking issue", REPO_ROOT, {**base, **env}, tmp_path
    )


@pytest.mark.parametrize(
    "env,phrase",
    [
        ({"STOPPED": "true"}, "the agent stopped without committing a bump"),
        ({"BUMP_RESULT": "failure"}, "the agent job failed"),
        ({"BUMP_RESULT": "failure", "SHOULD_BUMP": ""}, "versions undetermined"),
        ({}, "did not pass the checks run before a PR is opened"),
    ],
    ids=["agent-stopped", "agent-failed", "detect-failed", "checks-failed"],
)
def test_report_files_one_rolling_issue_from_this_workflow(tmp_path, env, phrase):
    result, _, calls = _report(tmp_path, **env)
    assert result.returncode == 0, result.stderr
    search = next(c for c in calls if c[:2] == ["issue", "list"])
    assert search[search.index("--author") + 1] == "app/github-actions"
    assert search[search.index("--label") + 1] == "devops"
    create = next(c for c in calls if c[:2] == ["issue", "create"])
    assert phrase in " ".join(create)
    assert ["--label", "bug"] == create[
        create.index("--label") : create.index("--label") + 2
    ]


def test_report_comments_on_the_open_tracking_issue(tmp_path):
    result, _, calls = _report(tmp_path, FAKE_ISSUE_LIST="77")
    assert result.returncode == 0, result.stderr
    assert any(c[:3] == ["issue", "comment", "77"] for c in calls)
    assert not any(c[:2] == ["issue", "create"] for c in calls)


def _verify_target(tmp_path, claimed, latest, force="", open_pr=""):
    if not shutil.which("jq"):
        pytest.skip("the target step needs jq")
    env = {
        "GH_TOKEN": "x",
        "FORCE_VERSION": force,
        "CLAIMED": claimed,
        "FAKE_API_JSON": json.dumps({"tag_name": latest}),
        "FAKE_PR_LIST": open_pr,
    }
    step = "Work out the target independently of the agent job"
    return run_step("verify", step, REPO_ROOT, env, tmp_path)


def test_verify_accepts_the_target_it_works_out_itself(tmp_path):
    result, outputs, _ = _verify_target(tmp_path, TARGET, f"v{TARGET}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"target_version={TARGET}" in outputs
    assert "current_version=2026.42.1" in outputs


def test_verify_accepts_a_forced_older_target(tmp_path):
    result, outputs, calls = _verify_target(
        tmp_path, "2026.40.0", "v9.9.9", force="2026.40.0"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "target_version=2026.40.0" in outputs
    assert not [c for c in calls if c[0] == "api"], "a forced target needs no lookup"


def test_verify_refuses_a_target_that_already_has_a_pr(tmp_path):
    """The agent job's open-PR guard, which a forged `ready` would skip."""
    result, outputs, calls = _verify_target(
        tmp_path, TARGET, f"v{TARGET}", open_pr="41"
    )
    assert result.returncode != 0
    assert f"PR #41 already exists for Lemonade {TARGET}" in result.stdout
    search = next(c for c in calls if c[:2] == ["pr", "list"])
    assert search[search.index("--head") + 1] == f"deps/lemonade-{TARGET}"
    assert search[search.index("--state") + 1] == "all"
    assert "target_version=" not in outputs


@pytest.mark.parametrize(
    "claimed,latest,force,reason",
    [
        ("2026.43.2", f"v{TARGET}", "", "but the target is 2026.43.1"),
        ("2026.40.0", f"v{TARGET}", "2026.43.1", "but the target is 2026.43.1"),
        ("2026.42.1", "v2026.42.1", "", "is already pinned"),
        ("2026.40.0", "v2026.40.0", "", "is older than the pinned"),
        (TARGET, "v2026.43.1;x", "", "is not an X.Y.Z Lemonade version"),
    ],
    ids=["claims-another", "claims-not-forced", "not-newer", "older", "malformed"],
)
def test_verify_refuses_a_target_it_did_not_work_out(
    tmp_path, claimed, latest, force, reason
):
    result, outputs, _ = _verify_target(tmp_path, claimed, latest, force)
    assert result.returncode != 0
    assert reason in result.stdout
    assert "target_version=" not in outputs


def _open_pr(main_repo, tmp_path, target):
    honest_bump(main_repo)
    git(main_repo, "add", "-A")
    write(tmp_path / "pr-body.md", "body\n")
    # The step pushes to github.com; route that URL to a local bare repo.
    remote = local_remote(
        tmp_path, "https://x-access-token:t0ken@github.com/amd/gaia.git"
    )
    env = {"GH_TOKEN": "t0ken", "TARGET": target, **remote}
    result, _, calls = run_step("publish", "Open the bump PR", main_repo, env, tmp_path)
    refs = git(tmp_path / "remote.git", "for-each-ref", "--format=%(refname)").split()
    return result, calls, refs


def test_publish_pushes_only_to_the_bump_branch_of_the_verified_target(
    main_repo, tmp_path
):
    result, calls, refs = _open_pr(main_repo, tmp_path, TARGET)
    assert result.returncode == 0, result.stdout + result.stderr
    assert refs == [f"refs/heads/deps/lemonade-{TARGET}"]
    create = next(c for c in calls if c[:2] == ["pr", "create"])
    assert create[create.index("--head") + 1] == f"deps/lemonade-{TARGET}"
    assert create[create.index("--base") + 1] == "main"
    message = git(main_repo, "log", "-1", "--format=%s").strip()
    assert message == f"chore(deps): bump Lemonade Server to v{TARGET}"


@pytest.mark.parametrize("target", ["main", "2026.43.1:refs/heads/main", ""])
def test_publish_refuses_a_target_that_is_not_x_y_z(main_repo, tmp_path, target):
    result, calls, refs = _open_pr(main_repo, tmp_path, target)
    assert result.returncode != 0
    assert "is not an X.Y.Z Lemonade version" in result.stdout
    assert refs == [], "nothing may be pushed"
    assert not [c for c in calls if c[:2] == ["pr", "create"]]


def test_report_says_unknown_when_verify_set_no_target(tmp_path):
    result, _, calls = _report(tmp_path, TARGET="", STOPPED="true")
    assert result.returncode == 0, result.stderr
    create = next(c for c in calls if c[:2] == ["issue", "create"])
    assert "(target vunknown)" in " ".join(create)


def test_report_prints_no_unchecked_target(tmp_path):
    result, _, calls = _report(tmp_path, TARGET="2026.43.1](https://x.test) @someone")
    assert result.returncode == 0, result.stderr
    create = next(c for c in calls if c[:2] == ["issue", "create"])
    text = " ".join(create)
    assert "(target vunknown)" in text
    assert "@someone" not in text and "x.test" not in text


def test_the_proposal_dedupe_searches_the_title_as_one_phrase(bump_dir, tmp_path):
    result, _, calls = _file_proposals(bump_dir, tmp_path)
    assert result.returncode == 0, result.stderr
    search = next(c for c in calls if c[:2] == ["issue", "list"])
    query = search[search.index("--search") + 1]
    assert query == f'"feat: Idea (unlocked by Lemonade v{TARGET})" in:title'
