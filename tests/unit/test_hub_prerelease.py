# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A release-candidate core installs the flagship built from its own commit.

The hub has no pre-release channel, so its `gaia` agent can be many commits
older than the candidate (v0.25.0-rc2 shipped the hub's 0.2.0, which predates
tool approvals). These tests pin the seam end to end: publish.yml's packager
(util/flagship_rc_assets.py) writes the release file, gaia.hub.prerelease reads
it, and the real installer installs from it -- never from the hub.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.hub import installer, prerelease

REPO_ROOT = Path(__file__).resolve().parents[2]
TAG = "v0.25.0-rc3"
RC_CORE = "0.25.0rc3"
RELEASE_BASE = f"https://github.com/amd/gaia/releases/download/{TAG}"
PLATFORM = "linux-x64"
BINARY = b"RC-FROZEN-FLAGSHIP"
SHA = hashlib.sha256(BINARY).hexdigest()


def _load_packager():
    spec = importlib.util.spec_from_file_location(
        "flagship_rc_assets", REPO_ROOT / "util" / "flagship_rc_assets.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


packager = _load_packager()


def _freeze_artifacts(root: Path, platforms=("win32-x64", "darwin-arm64", PLATFORM)):
    """The layout download-artifact gives publish.yml: one dir per freeze leg."""
    for platform in platforms:
        exe = platform.startswith("win32")
        filename = f"gaia-agent-{platform}{'.exe' if exe else ''}"
        data = BINARY if platform == PLATFORM else f"bin-{platform}".encode()
        leg = root / f"gaia-agent-{platform}"
        leg.mkdir(parents=True)
        (leg / filename).write_bytes(data)
        meta = {
            "component": "sidecar",
            "platform": platform,
            "filename": filename,
            "executable": "gaia-agent.exe" if exe else "gaia-agent",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        (leg / f"sidecar-{platform}.meta.json").write_text(json.dumps([meta]))
    return root


@pytest.fixture
def release_assets(tmp_path):
    out = tmp_path / "release-assets"
    packager.build_release(
        _freeze_artifacts(tmp_path / "artifacts"), TAG, "abc123", out, "0.3.0"
    )
    return out


def _fetcher_for(assets: Path, seen: list):
    def fetch(url: str, timeout: float = 120) -> bytes:
        seen.append(url)
        assert url.startswith(RELEASE_BASE + "/"), f"fetched outside the RC: {url}"
        return (assets / url.rsplit("/", 1)[1]).read_bytes()

    return fetch


@pytest.mark.parametrize(
    "core, tag",
    [("0.25.0rc3", "v0.25.0-rc3"), ("1.2.10rc12", "v1.2.10-rc12"), ("0.25.0", None)],
)
def test_only_a_release_candidate_core_has_a_tag(core, tag):
    assert prerelease.release_candidate_tag(core) == tag


@pytest.mark.parametrize("core", ["0.25.0", "0.25.0.dev3", "0.25.0a1"])
def test_a_final_core_uses_the_hub(core):
    assert (
        prerelease.flagship_source("gaia", fetcher=pytest.fail, core_version=core)
        is None
    )


def test_other_agents_always_use_the_hub():
    assert (
        prerelease.flagship_source("email", fetcher=pytest.fail, core_version=RC_CORE)
        is None
    )


def test_an_rc_installs_its_own_flagship_not_the_hubs(release_assets, tmp_path):
    seen = []
    stale_hub_manifest = {"id": "gaia", "versions": {"0.2.0": {}}}
    with patch.object(prerelease, "CORE_VERSION", RC_CORE):
        result = installer.install(
            "gaia",
            manifest=stale_hub_manifest,  # what the daemon pre-fetches from the hub
            fetcher=_fetcher_for(release_assets, seen),
            install_root=tmp_path / "agents",
            skip_compatibility_check=True,
            platform_key=PLATFORM,
        )

    assert result.version == f"0.3.0+{TAG}"
    sentinel = installer.read_sentinel("gaia", tmp_path / "agents")
    assert sentinel.artifact_sha256 == SHA
    assert sentinel.artifact_kind == installer.ARTIFACT_KIND_BINARY
    assert (tmp_path / "agents" / "gaia" / "gaia-agent").read_bytes() == BINARY
    assert (tmp_path / "agents" / "gaia" / "gaia-agent.yaml").is_file()
    assert f"{RELEASE_BASE}/gaia-agent-{PLATFORM}" in seen


def test_an_rc_without_its_flagship_fails_instead_of_using_the_hub(tmp_path):
    def missing(url, timeout=120):
        raise OSError("404 Not Found")

    with (
        patch.object(prerelease, "CORE_VERSION", RC_CORE),
        pytest.raises(installer.InstallError, match="not used instead"),
    ):
        installer.install(
            "gaia",
            fetcher=missing,
            install_root=tmp_path,
            skip_compatibility_check=True,
            platform_key=PLATFORM,
        )
    assert installer.read_sentinel("gaia", tmp_path) is None


def test_an_explicit_version_is_still_the_hubs(tmp_path):
    # Asking for a version by number is a request for that hub release.
    with (
        patch.object(prerelease, "CORE_VERSION", RC_CORE),
        patch.object(
            installer.catalog_mod, "fetch_manifest", side_effect=RuntimeError("hub")
        ),
        pytest.raises(RuntimeError, match="hub"),
    ):
        installer.install("gaia", version="0.2.0", install_root=tmp_path)


@pytest.mark.parametrize(
    "mutate, why",
    [
        (lambda r: r.update(releaseTag="v0.25.0-rc2"), "belongs to"),
        (lambda r: r.update(schemaVersion=2), "schemaVersion"),
        (lambda r: r["platforms"][PLATFORM].update(sha256="PENDING"), "sha256"),
        (lambda r: r.update(platforms={}), "no platform"),
    ],
)
def test_a_malformed_release_file_is_refused(release_assets, mutate, why):
    release = json.loads(
        (release_assets / prerelease.RELEASE_MANIFEST_NAME).read_text()
    )
    mutate(release)
    with pytest.raises(prerelease.PrereleaseFlagshipError, match=why):
        prerelease.flagship_source(
            "gaia",
            fetcher=lambda url, timeout=120: json.dumps(release).encode(),
            core_version=RC_CORE,
        )


class TestInit:
    def _source(self, release_assets):
        return prerelease.flagship_source(
            "gaia", fetcher=_fetcher_for(release_assets, []), core_version=RC_CORE
        )

    def _run(self, source, sentinel, profile="gaia"):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile=profile, yes=True)
        with (
            patch.object(prerelease, "flagship_source", return_value=source),
            patch.object(installer, "read_sentinel", return_value=sentinel),
            patch("gaia.hub.compatibility.current_platform_key", return_value=PLATFORM),
            patch.object(installer, "install") as install,
            patch("gaia.hub.catalog.load_index") as load_index,
        ):
            cmd._ensure_hub_agent_installed()
        load_index.assert_not_called()  # the hub catalog is not consulted
        return install

    def test_replaces_an_agent_installed_from_the_hub(self, release_assets):
        stale = SimpleNamespace(version="0.2.0", artifact_sha256="0" * 64)
        install = self._run(self._source(release_assets), stale)
        install.assert_called_once_with("gaia", trusted=True)

    def test_npu_profile_installs_the_candidates_flagship(self, release_assets):
        """The NPU profile installs the same flagship as the default one (#2972)."""
        install = self._run(self._source(release_assets), None, profile="npu")
        install.assert_called_once_with("gaia", trusted=True)

    def test_keeps_the_candidates_own_build(self, release_assets):
        current = SimpleNamespace(version=f"0.3.0+{TAG}", artifact_sha256=SHA)
        self._run(self._source(release_assets), current).assert_not_called()

    def test_a_broken_pre_release_fails_init(self):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="gaia", yes=True)
        with (
            patch.object(
                prerelease,
                "flagship_source",
                side_effect=prerelease.PrereleaseFlagshipError("no release file"),
            ),
            pytest.raises(prerelease.PrereleaseFlagshipError),
        ):
            cmd._ensure_hub_agent_installed()


class TestFinalReleaseReplacesACandidatesBuild:
    """A final release's flagship carries the same version as its candidates'.

    v0.25.1 kept the rc11 build of agent 0.3.1, because init only asked whether
    *a* flagship was installed. Only the bytes tell the two apart.
    """

    def _run(self, sentinel, published=None, published_error=None):
        from gaia.hub import catalog
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="gaia", yes=True)
        lookup = (
            {"side_effect": published_error}
            if published_error
            else {"return_value": published}
        )
        with (
            patch.object(prerelease, "flagship_source", return_value=None),
            patch.object(InitCommand, "_is_hub_agent_available", return_value=True),
            patch.object(installer, "read_sentinel", return_value=sentinel),
            patch.object(installer, "published_artifact_sha256", **lookup),
            patch.object(installer, "install") as install,
            patch.object(
                catalog,
                "load_index",
                return_value=SimpleNamespace(agents=[{"id": "gaia"}]),
            ),
            patch.object(InitCommand, "_restart_replaced_agent") as restart,
            patch.object(InitCommand, "_print_warning") as warn,
        ):
            cmd._ensure_hub_agent_installed()
        return install, restart, warn

    def test_a_candidates_build_is_replaced_and_restarted(self):
        rc_build = SimpleNamespace(version=f"0.3.1+{TAG}", artifact_sha256="0" * 64)
        install, restart, _ = self._run(rc_build, published=SHA)
        install.assert_called_once_with("gaia", trusted=True)
        restart.assert_called_once()

    def test_the_published_build_is_kept(self):
        current = SimpleNamespace(version="0.3.1", artifact_sha256=SHA)
        install, restart, _ = self._run(current, published=SHA)
        install.assert_not_called()
        restart.assert_not_called()

    def test_an_unreachable_hub_keeps_the_agent_and_says_so(self):
        from gaia.hub import catalog

        rc_build = SimpleNamespace(version=f"0.3.1+{TAG}", artifact_sha256="0" * 64)
        install, _, warn = self._run(
            rc_build, published_error=catalog.CatalogError("hub down")
        )
        install.assert_not_called()
        [message] = [c.args[0] for c in warn.call_args_list]
        assert "hub down" in message and "keeping it" in message

    def test_an_install_with_no_recorded_hash_is_left_alone(self):
        install, _, _ = self._run(SimpleNamespace(version="0.3.1", artifact_sha256=""))
        install.assert_not_called()


def test_the_published_hash_is_what_an_install_would_fetch():
    manifest = {"id": "gaia", "versions": {"0.3.1": {}}}
    artifact = {"sha256": SHA, "path": "x"}
    with patch.object(
        installer, "_resolve_version", return_value=("0.3.1", artifact)
    ) as resolve:
        assert (
            installer.published_artifact_sha256(
                "gaia", manifest=manifest, platform_key=PLATFORM
            )
            == SHA
        )
    resolve.assert_called_once_with(manifest, None, platform_key=PLATFORM)
