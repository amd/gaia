# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Release candidates (#4845): tag classification, version stamping, and the
workflow wiring that keeps a ``vX.Y.Z-rcN`` tag off every stable channel.

The workflow assertions are configuration checks, not behaviour tests: the
failure they guard against is an RC publishing as ``latest`` (npm), as the
newest catalog entry (Agent Hub R2), or as a normal GitHub release, and none
of that is visible until a tag is pushed.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import release_tag  # noqa: E402
import validate_release_notes  # noqa: E402

WORKFLOWS = REPO_ROOT / ".github" / "workflows"


# ── Classification ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tag, is_rc, pep440, npm, notes_tag",
    [
        ("v0.25.0", False, "0.25.0", "0.25.0", "v0.25.0"),
        ("v0.15.4.1", False, "0.15.4.1", "0.15.4.1", "v0.15.4.1"),
        ("v0.25.0-rc1", True, "0.25.0rc1", "0.25.0-rc.1", "v0.25.0"),
        ("v0.25.0-rc12", True, "0.25.0rc12", "0.25.0-rc.12", "v0.25.0"),
        ("v1.0.0-rc2", True, "1.0.0rc2", "1.0.0-rc.2", "v1.0.0"),
    ],
)
def test_classify(tag, is_rc, pep440, npm, notes_tag):
    rel = release_tag.parse_tag(tag)
    assert rel.is_rc is is_rc
    assert rel.pep440_version == pep440
    assert rel.npm_version == npm
    assert rel.notes_tag == notes_tag
    assert rel.npm_dist_tag == ("next" if is_rc else "latest")


@pytest.mark.parametrize(
    "tag",
    ["v0.25.0-rc0", "v0.25.0-rc.1", "v0.25.0rc1", "v0.25.0-beta1", "0.25.0", "v"],
)
def test_malformed_tags_are_rejected(tag):
    with pytest.raises(ValueError, match="vX.Y.Z-rcN"):
        release_tag.parse_tag(tag)


def test_rc_versions_are_valid_and_order_before_the_final():
    from packaging.version import Version

    rel = release_tag.parse_tag("v0.25.0-rc1")
    assert Version(rel.pep440_version).is_prerelease
    assert Version(rel.pep440_version) < Version("0.25.0")
    # npm's spelling normalizes to the same PEP 440 version pip installs.
    assert Version(rel.npm_version) == Version(rel.pep440_version)


def test_classify_cli_prints_github_outputs(capsys):
    assert release_tag.main(["classify", "v0.25.0-rc1"]) == 0
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert lines == {
        "IS_RC": "true",
        "BASE_VERSION": "0.25.0",
        "PEP440_VERSION": "0.25.0rc1",
        "NPM_VERSION": "0.25.0-rc.1",
        "NPM_DIST_TAG": "next",
        "NOTES_TAG": "v0.25.0",
    }


def test_classify_cli_fails_on_a_malformed_tag(capsys):
    assert release_tag.main(["classify", "v0.25.0-rc.1"]) == 1
    assert "not a release tag" in capsys.readouterr().err


# ── Stamping ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tree(tmp_path) -> Path:
    """A copy of the real files stamp() rewrites, so the patterns are proven
    against what the repo actually contains, with version.py set to 0.25.0."""
    for rel in (
        release_tag.VERSION_PY,
        release_tag.WEBUI_PACKAGE,
        release_tag.WEBUI_LOCK,
    ):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, dst)
    _set_versions(tmp_path, "0.25.0")
    return tmp_path


def _set_versions(root: Path, version: str) -> None:
    py = root / release_tag.VERSION_PY
    py.write_text(
        release_tag.VERSION_PY_RE.sub(
            lambda m: f"{m.group(1)}{version}{m.group(3)}",
            py.read_text(encoding="utf-8"),
            count=1,
        ),
        encoding="utf-8",
    )
    pkg_path = root / release_tag.WEBUI_PACKAGE
    pkg_text = pkg_path.read_text(encoding="utf-8")
    name = json.loads(pkg_text)["name"]
    pkg_path.write_text(
        re.sub(
            r'^(  "version":\s*")[^"]+(")',
            lambda m: f"{m.group(1)}{version}{m.group(2)}",
            pkg_text,
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    lock_path = root / release_tag.WEBUI_LOCK
    lock_path.write_text(
        release_tag._lock_root_re(name).sub(
            lambda m: f"{m.group(1)}{version}{m.group(3)}",
            lock_path.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )


def _versions(root: Path) -> tuple[str, str, list[str]]:
    py = release_tag._read_version_py(root)
    pkg = json.loads((root / release_tag.WEBUI_PACKAGE).read_text(encoding="utf-8"))
    lock = json.loads((root / release_tag.WEBUI_LOCK).read_text(encoding="utf-8"))
    return py, pkg["version"], [lock["version"], lock["packages"][""]["version"]]


def test_stamp_writes_every_spelling(tree):
    release_tag.stamp("v0.25.0-rc1", tree)
    assert _versions(tree) == ("0.25.0rc1", "0.25.0-rc.1", ["0.25.0-rc.1"] * 2)


def test_stamp_is_idempotent(tree):
    release_tag.stamp("v0.25.0-rc1", tree)
    release_tag.stamp("v0.25.0-rc1", tree)
    assert _versions(tree) == ("0.25.0rc1", "0.25.0-rc.1", ["0.25.0-rc.1"] * 2)


def test_stamp_refuses_a_final(tree):
    with pytest.raises(ValueError, match="never stamped"):
        release_tag.stamp("v0.25.0", tree)
    assert _versions(tree) == ("0.25.0", "0.25.0", ["0.25.0"] * 2)


def test_stamp_refuses_a_tree_without_the_release_bump(tree):
    _set_versions(tree, "0.24.1")
    with pytest.raises(ValueError, match="release bump"):
        release_tag.stamp("v0.25.0-rc1", tree)
    assert _versions(tree)[0] == "0.24.1"


def test_stamp_keeps_crlf_line_endings(tree):
    py = tree / release_tag.VERSION_PY
    py.write_bytes(py.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    release_tag.stamp("v0.25.0-rc1", tree)
    data = py.read_bytes()
    assert b'__version__ = "0.25.0rc1"\r\n' in data
    assert b"\n" not in data.replace(b"\r\n", b"")


# ── Release-notes validator ──────────────────────────────────────────────────

NOTES = """---
title: "{title}"
description: "Release notes"
---

## What's New

Things.

## Full Changelog

https://github.com/amd/gaia/compare/v0.24.1...v0.25.0
"""


@pytest.fixture
def notes(tmp_path) -> Path:
    path = tmp_path / "v0.25.0.mdx"
    path.write_text(NOTES.format(title="v0.25.0"), encoding="utf-8")
    return path


def test_an_rc_validates_against_its_finals_notes(notes):
    assert (
        validate_release_notes.validate_release_notes(str(notes), "v0.25.0-rc2") == []
    )


def test_finals_stay_strict(notes):
    assert validate_release_notes.validate_release_notes(str(notes), "v0.25.0") == []
    errors = validate_release_notes.validate_release_notes(str(notes), "v0.26.0")
    assert errors == ["Title should contain 'v0.26.0'"]


def test_an_rc_for_another_version_fails(notes):
    errors = validate_release_notes.validate_release_notes(str(notes), "v0.26.0-rc1")
    assert errors == ["Title should contain 'v0.26.0'"]


# ── Workflow wiring ──────────────────────────────────────────────────────────


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _tag_filters(workflow: dict) -> list[str]:
    on = workflow.get("on", workflow.get(True))
    return on["push"]["tags"]


def _triggers(patterns: list[str], tag: str) -> bool:
    """GitHub's tag filter: later patterns win, `!` excludes."""
    matched = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        if fnmatch.fnmatchcase(tag, pattern.lstrip("!")):
            matched = not negated
    return matched


@pytest.mark.parametrize(
    "workflow, final_runs, rc_runs",
    [
        ("publish.yml", True, True),
        ("release_components.yml", True, False),
        ("update-release-branch.yml", True, False),
    ],
)
def test_which_workflows_an_rc_tag_starts(workflow, final_runs, rc_runs):
    patterns = _tag_filters(_workflow(workflow))
    assert _triggers(patterns, "v0.25.0") is final_runs
    assert _triggers(patterns, "v0.25.0-rc1") is rc_runs


def _steps(job: dict, needle: str) -> list[dict]:
    return [s for s in job["steps"] if needle in s.get("name", "")]


IS_RC = "needs.validate.outputs.is_rc == 'true'"
NOT_RC = "needs.validate.outputs.is_rc != 'true'"


@pytest.fixture(scope="module")
def publish() -> dict:
    return _workflow("publish.yml")["jobs"]


def test_publish_stamps_only_release_candidates(publish):
    stamped = {
        job_id: _steps(job, "Stamp the release-candidate version")
        for job_id, job in publish.items()
        if "steps" in job
    }
    jobs = {job_id for job_id, steps in stamped.items() if steps}
    # Every job that builds or publishes a versioned artifact.
    assert jobs == {"build-pypi", "build-npm", "publish-npm"}
    for job_id in jobs:
        assert stamped[job_id][0]["if"] == IS_RC, job_id

    installers = _workflow("build-installers.yml")["jobs"]["build"]
    (step,) = _steps(installers, "Stamp the release-candidate version")
    assert step["if"] == "contains(inputs.tag, '-rc')"


def test_npm_latest_never_moves_for_an_rc(publish):
    steps = publish["publish-npm"]["steps"]
    publishes = [s for s in steps if "npm publish" in s.get("run", "")]
    assert len(publishes) == 2
    final = next(s for s in publishes if "--tag" not in s["run"])
    rc = next(s for s in publishes if "--tag next" in s["run"])
    assert final["if"] == NOT_RC
    assert rc["if"] == IS_RC


def test_an_rc_github_release_is_a_prerelease(publish):
    steps = [
        s
        for s in publish["github-release"]["steps"]
        if s.get("uses", "").startswith("softprops/action-gh-release")
    ]
    final = next(s for s in steps if s["if"] == NOT_RC)
    rc = next(s for s in steps if s["if"] == IS_RC)
    assert "prerelease" not in final["with"]
    assert rc["with"]["prerelease"] is True
    assert rc["with"]["make_latest"] is False
    assert rc["with"]["files"] == final["with"]["files"]


def test_context7_is_not_refreshed_for_an_rc(publish):
    job = publish["refresh-context7"]
    assert NOT_RC in job["if"]
    assert "validate" in job["needs"]


def test_only_an_rc_requests_the_website_redeploy_from_publish(publish):
    # A final's redeploy comes from release_components.yml; a second one here
    # would change the final path.
    job = publish["redeploy-website-rc"]
    assert IS_RC in job["if"]
    assert "!cancelled()" in job["if"]
