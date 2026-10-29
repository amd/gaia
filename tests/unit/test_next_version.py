# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/next_version.py: after a final, main moves past it in one PR.

Each tree test runs on a copy of the real version files, docs.json and the
latest notes, so a format change breaks here rather than in a post-release run.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import next_version as nv  # noqa: E402
import release_versions as rv  # noqa: E402
from validate_release_notes import validate_release_notes  # noqa: E402

RELEASED = rv.released_version(REPO_ROOT)
WORKFLOW = REPO_ROOT / ".github/workflows/next_version.yml"


def _raw(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _put(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _set_core(root: Path, version: str) -> None:
    path = root / rv.CORE_PY
    _put(
        path,
        rv._PY_VERSION.sub(lambda m: f"{m.group(1)}{version}{m.group(3)}", _raw(path)),
    )


def _copy_tree(root: Path) -> None:
    paths = {t.path for t in rv.TARGETS} | set(rv.SOURCE_FILES.values())
    paths.add(f"docs/releases/v{RELEASED}.mdx")
    paths.add(nv.FLAGSHIP_CHANGELOG)
    for rel in paths:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, root / rel)


@pytest.fixture
def at_release(tmp_path: Path) -> Path:
    """Main as a final leaves it: version.py still names the release just shipped."""
    _copy_tree(tmp_path)
    _set_core(tmp_path, RELEASED)
    assert rv.print_report(rv.run(tmp_path, stamp=True), stamp=True) == 0
    return tmp_path


def _clean(root: Path) -> None:
    report = rv.run(root, stamp=False)
    assert report.errors == [] and report.drift == [], report


# -- Versions ------------------------------------------------------------------


@pytest.mark.parametrize(
    "final, nxt", [("0.25.2", "0.26.0"), ("0.26.0", "0.27.0"), ("1.9.3", "1.10.0")]
)
def test_next_minor(final, nxt):
    assert nv.next_minor(final) == nxt


@pytest.mark.parametrize(
    "tag, match",
    [
        ("v0.26.0-rc1", "release candidate"),
        ("v0.15.4.1", "four-part"),
        ("0.26.0", "not a release tag"),
        ("agent-pkg-gaia-v0.3.2", "not a release tag"),
    ],
)
def test_only_a_three_part_final_is_accepted(tag, match):
    with pytest.raises(nv.NextVersionError, match=match):
        nv.final_version(tag)


# -- Main after a final -----------------------------------------------------------


def test_a_final_bumps_main_to_the_next_minor_and_stamps_everything(at_release):
    nxt = nv.next_minor(RELEASED)
    p = nv.prepare_main(at_release, f"v{RELEASED}")

    assert (p.version_before, p.version_after, p.bumped) == (RELEASED, nxt, True)
    assert f'__version__ = "{nxt}"' in _raw(at_release / rv.CORE_PY)
    assert f'__version__ = "{nxt}"' in _raw(at_release / rv.FLAGSHIP_INIT)
    assert f'"version": "{nxt}"' in _raw(
        at_release / "src/gaia/apps/webui/package.json"
    )
    # The navbar keeps naming the release users can install.
    assert f'"v{RELEASED} \\u00b7 Lemonade' in _raw(at_release / rv.DOCS_JSON)
    assert rv.released_version(at_release) == RELEASED
    _clean(at_release)


def test_the_stub_is_unlisted_and_fails_release_validation(at_release):
    nxt = nv.next_minor(RELEASED)
    nv.prepare_main(at_release, f"v{RELEASED}")
    stub = at_release / f"docs/releases/v{nxt}.mdx"

    assert nv.STUB_MARKER in _raw(stub)
    assert f'"releases/v{nxt}"' not in _raw(at_release / rv.DOCS_JSON)
    errors = validate_release_notes(str(stub), f"v{nxt}")
    assert any("next-version stub" in e for e in errors)
    # Filled in, the same skeleton validates.
    _put(stub, _raw(stub).replace(nv.STUB_MARKER, "notes"))
    assert validate_release_notes(str(stub), f"v{nxt}") == []


def test_main_already_ahead_is_not_bumped_again(at_release):
    _set_core(at_release, "9.4.0")
    rv.run(at_release, stamp=True)

    p = nv.prepare_main(at_release, f"v{RELEASED}")

    assert not p.bumped and p.version_after == "9.4.0"
    assert p.changes == [
        "opened the flagship's 9.4.0 changelog entry",
        "opened the v9.4.0 notes stub",
    ]
    _clean(at_release)


def test_the_flagship_changelog_quotes_the_new_minimum_core(at_release):
    """test_npm_doc_min_version.py reads the first such line against the manifest."""
    nxt = nv.next_minor(RELEASED)
    nv.prepare_main(at_release, f"v{RELEASED}")
    changelog = _raw(at_release / nv.FLAGSHIP_CHANGELOG)
    first = re.search(r"Python CLI (\d+\.\d+\.\d+)\+ on `PATH`", changelog)
    manifest = yaml.safe_load(
        _raw(at_release / "hub/agents/gaia/python/gaia-agent.yaml")
    )
    assert first.group(1) == manifest["min_gaia_version"] == nxt
    assert changelog.index("## [Unreleased]") < changelog.index(f"## [{nxt}]")


def test_a_changelog_without_unreleased_is_an_error(at_release):
    path = at_release / nv.FLAGSHIP_CHANGELOG
    _put(path, _raw(path).replace("## [Unreleased]", "## Unreleased"))
    with pytest.raises(nv.NextVersionError, match=r"no '## \[Unreleased\]'"):
        nv.prepare_main(at_release, f"v{RELEASED}")


def test_a_second_run_changes_nothing(at_release):
    nv.prepare_main(at_release, f"v{RELEASED}")
    before = {p: p.read_bytes() for p in at_release.rglob("*") if p.is_file()}

    assert nv.prepare_main(at_release, f"v{RELEASED}").changes == []
    assert {p: p.read_bytes() for p in at_release.rglob("*") if p.is_file()} == before


def test_an_existing_stub_is_never_overwritten(at_release):
    nxt = nv.next_minor(RELEASED)
    notes = at_release / f"docs/releases/v{nxt}.mdx"
    _put(notes, "already drafted\n")
    nv.prepare_main(at_release, f"v{RELEASED}")
    assert _raw(notes) == "already drafted\n"


def test_a_release_from_a_branch_brings_its_notes_and_moves_the_navbar(at_release):
    """A patch shipped from release/vX.Y: main lacks its notes and its nav entry."""
    _set_core(at_release, "9.4.0")
    rv.run(at_release, stamp=True)
    final = f"{RELEASED.rsplit('.', 1)[0]}.{int(RELEASED.rsplit('.', 1)[1]) + 1}"
    notes = f'---\ntitle: "v{final}"\n---\n'

    p = nv.prepare_main(at_release, f"v{final}", final_notes=notes)

    assert _raw(at_release / f"docs/releases/v{final}.mdx") == notes
    docs = _raw(at_release / rv.DOCS_JSON)
    assert docs.index(f'"releases/v{final}"') < docs.index(f'"releases/v{RELEASED}"')
    assert docs.index('"releases/index"') < docs.index(f'"releases/v{final}"')
    assert rv.released_version(at_release) == final
    assert f'"v{final} \\u00b7 Lemonade' in docs
    assert not p.bumped
    _clean(at_release)


def test_missing_notes_without_a_tagged_copy_fail_and_write_nothing(at_release):
    before = {p: p.read_bytes() for p in at_release.rglob("*") if p.is_file()}
    with pytest.raises(nv.NextVersionError, match="git show v9.9.9:"):
        nv.prepare_main(at_release, "v9.9.9")
    assert {p: p.read_bytes() for p in at_release.rglob("*") if p.is_file()} == before


def test_an_rc_on_main_is_refused(at_release):
    _set_core(at_release, f"{RELEASED}rc1")
    with pytest.raises(nv.NextVersionError, match="only ever declares X.Y.Z"):
        nv.prepare_main(at_release, f"v{RELEASED}")


def test_an_older_page_is_listed_below_newer_ones():
    docs = (
        '{"group": "Release Notes",\n  "pages": [\n    "releases/index",\n'
        '    "releases/v0.26.0",\n    "releases/v0.25.2"\n  ]\n}'
    )
    out = nv.list_release_page(docs, "0.25.3")
    assert out.index("v0.26.0") < out.index("v0.25.3") < out.index("v0.25.2")
    assert nv.list_release_page(out, "0.25.3") == out
    oldest = nv.list_release_page(docs, "0.1.0")
    assert oldest.rstrip().endswith('"releases/v0.1.0"\n  ]\n}'.rstrip())


# -- The PR ------------------------------------------------------------------------


def _matches(cmd: list[str], prefix: tuple[str, ...]) -> bool:
    """Every given word starts the command's word in the same place."""
    return len(cmd) >= len(prefix) and all(c.startswith(p) for c, p in zip(cmd, prefix))


class FakeGit:
    """Records every command; answers each by its first matching prefix."""

    def __init__(self, answers: dict[tuple[str, ...], tuple[int, str]] | None = None):
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)
        for prefix, (code, out) in self.answers.items():
            if _matches(cmd, prefix):
                return subprocess.CompletedProcess(cmd, code, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if tuple(c[: len(prefix)]) == prefix]


def test_opens_one_pr_against_main(at_release):
    git = FakeGit(
        {
            ("git", "fetch", "--no-tags", "origin", "+next-version"): (1, ""),
            ("gh", "pr", "create"): (0, "https://github.com/amd/gaia/pull/1\n"),
        }
    )
    out = nv.open_main_pr(f"v{RELEASED}", "amd/gaia", run=git, workdir=at_release)

    assert out == "opened https://github.com/amd/gaia/pull/1"
    branch = f"next-version/v{RELEASED}"
    assert git.ran("git", "checkout", "-B", branch, "origin/main")
    assert git.ran("git", "push", "--force", "origin", f"HEAD:refs/heads/{branch}")
    (create,) = git.ran("gh", "pr", "create")
    assert create[create.index("--base") + 1] == "main"
    assert create[create.index("--head") + 1] == branch
    assert create[create.index("--label") + 1] == "release"
    assert "kovtcharov-amd" in create[create.index("--reviewer") + 1]
    title = create[create.index("--title") + 1]
    assert title == f"chore(release): start {nv.next_minor(RELEASED)} after v{RELEASED}"


def test_an_open_pr_with_the_same_changes_is_left_alone(at_release):
    git = FakeGit(
        {
            ("gh", "pr", "list"): (0, "https://github.com/amd/gaia/pull/7\n"),
        }
    )
    out = nv.open_main_pr(f"v{RELEASED}", "amd/gaia", run=git, workdir=at_release)
    assert "pull/7 is already open" in out and "already carries" in out
    assert not git.ran("git", "push")
    assert not git.ran("gh", "pr", "create")


def test_nothing_to_do_opens_nothing(at_release):
    nv.prepare_main(at_release, f"v{RELEASED}")
    git = FakeGit()
    out = nv.open_main_pr(f"v{RELEASED}", "amd/gaia", run=git, workdir=at_release)
    assert out == f"main already follows v{RELEASED}; no PR needed."
    assert not git.ran("git", "commit") and not git.ran("gh", "pr", "create")


def test_a_dirty_checkout_is_refused(at_release):
    git = FakeGit({("git", "status", "--porcelain"): (0, " M src/gaia/version.py\n")})
    with pytest.raises(nv.NextVersionError, match="uncommitted changes"):
        nv.open_main_pr(f"v{RELEASED}", "amd/gaia", run=git, workdir=at_release)


def test_a_dry_run_pushes_nothing(at_release):
    git = FakeGit({("git", "status", "--porcelain"): (0, " M x\n")})
    out = nv.open_main_pr(
        f"v{RELEASED}", "amd/gaia", run=git, workdir=at_release, dry_run=True
    )
    assert out.startswith("DRY RUN: would open")
    assert not git.ran("git", "checkout") and not git.ran("git", "push")
    assert not git.ran("gh")


def test_an_open_pr_with_older_changes_gets_its_branch_refreshed(at_release):
    git = FakeGit(
        {
            ("git", "diff", "--quiet"): (1, ""),
            ("gh", "pr", "list"): (0, "https://github.com/amd/gaia/pull/7\n"),
        }
    )
    out = nv.open_main_pr(f"v{RELEASED}", "amd/gaia", run=git, workdir=at_release)
    assert out.endswith("its branch was refreshed.")
    assert git.ran("git", "push", "--force")
    assert not git.ran("gh", "pr", "create")


def test_a_permission_failure_names_the_setting(at_release):
    git = FakeGit(
        {
            ("git", "fetch", "--no-tags", "origin", "+next-version"): (1, ""),
            ("gh", "pr", "create"): (1, ""),
        }
    )

    def run(cmd):
        proc = git(cmd)
        if list(cmd[:3]) == ["gh", "pr", "create"]:
            proc.stderr = "GraphQL: Resource not accessible by integration"
        return proc

    with pytest.raises(nv.NextVersionError, match="Allow GitHub Actions to create"):
        nv.open_main_pr(f"v{RELEASED}", "amd/gaia", run=run, workdir=at_release)


# -- The patch half -------------------------------------------------------------------


def test_a_minor_final_needs_no_branch_pr():
    assert "not a patch" in nv.patch_branch_status("0.26.0", FakeGit())


def test_a_patch_with_no_release_branch_says_so():
    git = FakeGit({("git", "ls-remote"): (0, "")})
    msg = nv.patch_branch_status("0.25.2", git)
    assert "No release/v0.25 branch exists yet" in msg and "Phase 6" in msg
    assert git.ran("git", "ls-remote", "--heads", "origin", "release/v0.25")


def test_a_patch_with_a_release_branch_fails_until_it_is_built():
    git = FakeGit({("git", "ls-remote"): (0, "abc123\trefs/heads/release/v0.25\n")})
    with pytest.raises(nv.NextVersionError, match="release/v0.25 exists"):
        nv.patch_branch_status("0.25.2", git)


# -- The workflow -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_it_follows_the_publish_workflow_by_name(workflow):
    publish = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    )
    on = workflow[True]
    assert on["workflow_run"]["workflows"] == [publish["name"]]
    assert on["workflow_run"]["types"] == ["completed"]


def test_a_manual_run_is_a_dry_run_by_default(workflow):
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True
    assert inputs["tag"]["required"] is True


def test_only_the_job_can_write(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    (job,) = workflow["jobs"].values()
    assert job["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert "environment" not in job
    assert "success" in job["if"] and "push" in job["if"]
