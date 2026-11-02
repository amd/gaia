# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
publish.yml must not create a GitHub release with a promised installer missing
(issue #3520).

The three desktop-installer downloads in ``github-release`` were
``continue-on-error: true``, so an artifact-retrieval failure let the release go
out green with whatever happened to be on disk. Its notes still named the
Windows setup, the macOS DMG and the Linux DEB/AppImage, which became dead
download links. The downloads are now strict, and a step asserts every promised
desktop file is attached before either "Create GitHub ..." step runs.

The behaviour tests run that step's real ``run:`` script under bash against a
temporary ``release-assets`` directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"
CHECK_STEP = "Check every desktop installer is attached"
DESKTOP_DOWNLOADS = (
    "Download Desktop Installer (Windows NSIS)",
    "Download Desktop Installer (macOS DMG)",
    "Download Desktop Installer (Linux DEB + AppImage)",
)
VERSION = "0.26.0-rc.1"
# The names electron-builder produced for the last releases (e.g. v0.25.3).
PROMISED = [
    f"gaia-agent-ui-{VERSION}-x64-setup.exe",
    f"gaia-agent-ui-{VERSION}-x64-setup.exe.blockmap",
    "latest.yml",
    f"gaia-agent-ui-{VERSION}-arm64.dmg",
    f"gaia-agent-ui-{VERSION}-arm64.dmg.blockmap",
    "latest-mac.yml",
    f"gaia-agent-ui-{VERSION}-amd64.deb",
    f"gaia-agent-ui-{VERSION}-x86_64.AppImage",
    "latest-linux.yml",
]


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(workflow) -> list[dict]:
    return workflow["jobs"]["github-release"]["steps"]


def _index(steps: list[dict], name: str) -> int:
    names = [s.get("name") for s in steps]
    assert name in names, f"github-release no longer has a {name!r} step"
    return names.index(name)


def test_no_release_step_tolerates_failure(steps, workflow):
    tolerant = [
        s.get("name") or s.get("uses") for s in steps if s.get("continue-on-error")
    ]
    assert not tolerant, (
        f"github-release steps with continue-on-error can ship a release with "
        f"assets missing (#3520): {tolerant}"
    )
    assert workflow["jobs"]["github-release"].get("continue-on-error", False) is False


def test_asset_check_runs_after_every_download_and_before_release(steps):
    check = _index(steps, CHECK_STEP)
    for name in DESKTOP_DOWNLOADS:
        assert _index(steps, name) < check, f"{name!r} must run before the check"
    for i, step in enumerate(steps):
        if str(step.get("uses", "")).startswith("softprops/action-gh-release"):
            assert i > check, f"{step.get('name')!r} runs before the asset check"


def test_asset_check_is_unconditional(steps):
    """Gated on anything, a final or an RC could skip it."""
    step = steps[_index(steps, CHECK_STEP)]
    assert "if" not in step
    assert step["env"]["NPM_VERSION"] == "${{ needs.validate.outputs.npm_version }}"
    assert "${{" not in step["run"]


def test_build_tui_legs_all_report(workflow):
    strategy = workflow["jobs"]["build-tui"]["strategy"]
    assert strategy.get("fail-fast") is False


def test_release_still_requires_build_tui(workflow):
    job = workflow["jobs"]["github-release"]
    assert "build-tui" in job["needs"]
    assert "needs.build-tui.result == 'success'" in job["if"]


# --- behaviour: run the real script ----------------------------------------


def _run_check(steps, tmp_path, present, *, version=VERSION, empty=()):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    assets = tmp_path / "release-assets"
    assets.mkdir()
    for name in present:
        (assets / name).write_bytes(b"" if name in empty else b"x")
    res = subprocess.run(
        [bash, "-c", steps[_index(steps, CHECK_STEP)]["run"]],
        cwd=tmp_path,
        env={**os.environ, "NPM_VERSION": version},
        capture_output=True,
        text=True,
        check=False,
    )
    return res


def test_all_assets_present_passes(steps, tmp_path):
    res = _run_check(steps, tmp_path, PROMISED)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("missing", PROMISED)
def test_each_missing_asset_fails_and_is_named(steps, tmp_path, missing):
    res = _run_check(steps, tmp_path, [n for n in PROMISED if n != missing])
    assert res.returncode == 1
    assert "::error::" in res.stdout and missing in res.stdout


def test_empty_asset_counts_as_missing(steps, tmp_path):
    dmg = f"gaia-agent-ui-{VERSION}-arm64.dmg"
    res = _run_check(steps, tmp_path, PROMISED, empty=(dmg,))
    assert res.returncode == 1
    assert dmg in res.stdout


def test_another_versions_installers_do_not_count(steps, tmp_path):
    """A stale artifact from another build must not satisfy the check."""
    res = _run_check(steps, tmp_path, PROMISED, version="0.26.0")
    assert res.returncode == 1
    assert "gaia-agent-ui-0.26.0-x64-setup.exe" in res.stdout


def test_no_assets_at_all_fails(steps, tmp_path):
    res = _run_check(steps, tmp_path, [])
    assert res.returncode == 1
