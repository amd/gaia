# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A release candidate ships the flagship built from its own commit.

Covers util/flagship_rc_assets.py (the pre-release packager) and the workflow
wiring that makes an RC unpublishable without that build.
"""

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load():
    spec = importlib.util.spec_from_file_location(
        "flagship_rc_assets", REPO_ROOT / "util" / "flagship_rc_assets.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


assets = _load()


def _leg(root: Path, platform: str, data: bytes = b"bin", sha: str | None = None):
    exe = platform.startswith("win32")
    filename = f"gaia-agent-{platform}{'.exe' if exe else ''}"
    leg = root / f"gaia-agent-{platform}"
    leg.mkdir(parents=True)
    (leg / filename).write_bytes(data)
    meta = {
        "component": "sidecar",
        "platform": platform,
        "filename": filename,
        "executable": "gaia-agent.exe" if exe else "gaia-agent",
        "sha256": sha or hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    (leg / f"sidecar-{platform}.meta.json").write_text(json.dumps([meta]))


@pytest.fixture
def artifacts(tmp_path):
    root = tmp_path / "artifacts"
    for p in assets.REQUIRED_PLATFORMS:
        _leg(root, p, data=p.encode())
    return root


def test_release_attaches_every_build_and_describes_it(artifacts, tmp_path):
    out = tmp_path / "out"
    manifest = assets.build_release(artifacts, "v0.25.0-rc3", "deadbeef", out, "0.3.0")

    assert manifest["releaseTag"] == "v0.25.0-rc3"
    assert manifest["agentVersion"] == "0.3.0"
    assert manifest["commit"] == "deadbeef"
    assert sorted(manifest["platforms"]) == sorted(assets.REQUIRED_PLATFORMS)
    for entry in manifest["platforms"].values():
        data = (out / entry["filename"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert (out / assets.AGENT_YAML_ASSET_NAME).is_file()
    on_disk = json.loads((out / assets.RELEASE_MANIFEST_NAME).read_text())
    assert on_disk == manifest


def test_a_missing_required_build_stops_the_release(tmp_path):
    root = tmp_path / "artifacts"
    _leg(root, "win32-x64")
    _leg(root, "linux-x64")
    with pytest.raises(SystemExit):
        assets.build_release(root, "v0.25.0-rc3", "x", tmp_path / "out", "0.3.0")


def test_a_binary_that_does_not_match_its_freeze_hash_is_refused(artifacts, tmp_path):
    _leg(artifacts, "darwin-x64", data=b"real", sha="0" * 64)
    with pytest.raises(SystemExit):
        assets.build_release(artifacts, "v0.25.0-rc3", "x", tmp_path / "out", "0.3.0")


def test_a_final_tag_is_refused(artifacts, tmp_path):
    with pytest.raises(SystemExit):
        assets.build_release(artifacts, "v0.25.0", "x", tmp_path / "out", "0.3.0")


def test_stage_writes_the_installed_name(artifacts, tmp_path):
    dest = assets.stage(artifacts, "win32-x64", tmp_path / "stage")
    assert dest.name == "gaia-agent.exe"
    assert dest.read_bytes() == b"win32-x64"


def test_packager_and_runtime_agree_on_the_contract():
    from gaia.hub import prerelease

    assert assets.RELEASE_MANIFEST_NAME == prerelease.RELEASE_MANIFEST_NAME
    assert assets.AGENT_YAML_ASSET_NAME == prerelease.AGENT_YAML_ASSET_NAME
    assert assets.SCHEMA_VERSION == prerelease.SCHEMA_VERSION


def _jobs(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]


def test_an_rc_cannot_be_approved_or_released_without_its_flagship():
    jobs = _jobs("publish.yml")
    flagship = jobs["rc-flagship"]
    assert flagship["uses"] == "./.github/workflows/build_flagship_sidecar.yml"
    assert "is_rc == 'true'" in flagship["if"]
    for name in ("approve-publish", "github-release"):
        assert "rc-flagship" in jobs[name]["needs"]
        assert "needs.rc-flagship.result == 'success'" in jobs[name]["if"]


def test_the_rc_windows_setup_bundles_this_runs_flagship():
    setup = _jobs("publish.yml")["rc-windows-setup"]
    assert "rc-flagship" in setup["needs"]
    assert setup["with"]["sidecar"] == "artifact"


def test_the_rc_release_attaches_the_flagship():
    steps = _jobs("publish.yml")["github-release"]["steps"]
    attach = next(
        s for s in steps if s.get("name", "").startswith("Attach the flagship")
    )
    assert "flagship_rc_assets.py release" in attach["run"]


def test_flagship_releases_freeze_through_the_same_workflow():
    assert (
        _jobs("release_agent_gaia.yml")["freeze"]["uses"]
        == "./.github/workflows/build_flagship_sidecar.yml"
    )
    freeze = _jobs("build_flagship_sidecar.yml")["freeze"]
    runs = "\n".join(s.get("run", "") for s in freeze["steps"])
    assert re.search(r"freeze\.py\"? --onefile", runs)
    assert "smoke_test.py" in runs
