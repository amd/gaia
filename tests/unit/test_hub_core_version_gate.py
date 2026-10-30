# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The min_gaia_version gate: a core installs and is offered only an agent
version it can run.

The manifests mirror what hub.amd-gaia.ai serves: a stored manifest from
before per-version records carries the floor only at the top level (where it
belongs to the latest version), and a newer one carries it per version.
"""

import hashlib

import pytest

from gaia.hub import catalog, installer
from gaia.hub.catalog import core_gate, core_satisfies, merge_with_registry
from gaia.hub.installer import CoreTooOldError, InstallError


@pytest.mark.parametrize(
    "floor,core,ok",
    [
        ("0.25.0", "0.24.1", False),
        ("0.25.0", "0.25.0", True),
        ("0.25.0", "0.25.1", True),
        ("0.25.0", "0.26.0", True),
        ("0.25.0", "1.0.0", True),
        # A candidate or dev build ranks below its release (PEP 440).
        ("0.25.0", "0.25.0rc3", False),
        ("0.25.0", "0.25.0.dev1", False),
        ("0.24.1", "0.25.0rc3", True),
        ("0.25.0", "0.25.0.post1", True),
        ("0.25.0", "0.25.0+local", True),
        # A prerelease floor is met by every build of that release.
        ("0.25.0-rc.1", "0.25.0rc1", True),
        ("0.25.0-rc.1", "0.24.9", False),
        ("", "0.1.0", True),
        (None, "0.1.0", True),
    ],
)
def test_core_satisfies(floor, core, ok):
    assert core_satisfies(floor, core) is ok


@pytest.mark.parametrize("floor", ["0.25", "latest", ">=0.25.0"])
def test_a_malformed_floor_is_refused(floor):
    with pytest.raises(ValueError, match="not a SemVer"):
        core_satisfies(floor, "0.25.0")


def test_hub_fetches_name_this_core_version(monkeypatch):
    """Without it the hub cannot tell this client from a 0.24.1 one."""
    import requests

    sent = {}

    class _Resp:
        content = b"{}"

        def raise_for_status(self):
            pass

    def fake_get(url, timeout, headers):
        sent.update(headers)
        return _Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(catalog, "_running_core_version", lambda: "0.25.0")
    catalog.fetch_bytes("https://hub.test/index.json")
    assert sent == {"X-GAIA-Core-Version": "0.25.0"}


def test_a_malformed_core_version_is_refused():
    with pytest.raises(ValueError, match="core version"):
        core_satisfies("0.25.0", "unknown")


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------


def _artifact(agent_id, version):
    data = f"{agent_id}-{version}".encode()
    return data, {
        "filename": f"{agent_id}-{version}-py3-none-any.whl",
        "path": f"agents/{agent_id}/{version}/{agent_id}-{version}-py3-none-any.whl",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": "application/octet-stream",
    }


def _manifest(floors, *, top_level=None, agent_id="gaia"):
    """``floors`` maps version -> its per-version floor (None = not recorded)."""
    versions = {}
    for version, floor in floors.items():
        _, art = _artifact(agent_id, version)
        entry = {"version": version, "artifact": art, "artifacts": [art]}
        if floor is not None:
            entry["min_gaia_version"] = floor
        versions[version] = entry
    latest = max(floors, key=catalog._parse_version)
    manifest = {
        "id": agent_id,
        "language": "python",
        "security_tier": "verified",
        "latest_version": latest,
        "requirements": {"platforms": []},
        "versions": versions,
    }
    if top_level is not None:
        manifest["min_gaia_version"] = top_level
    return manifest


# What the hub serves after the new worker publishes 0.3.0.
RECORDED = _manifest({"0.1.1": None, "0.2.0": "0.24.1", "0.3.0": "0.25.0"})
# What the hub serves if 0.3.0 is published by the worker that predates
# per-version records: only the top-level floor, which is 0.3.0's own.
LEGACY = _manifest({"0.1.1": None, "0.2.0": None, "0.3.0": None}, top_level="0.25.0")


@pytest.mark.parametrize("manifest", [RECORDED, LEGACY], ids=["recorded", "legacy"])
def test_an_older_core_resolves_the_newest_version_it_runs(manifest):
    gate = core_gate(manifest, "0.24.1")
    assert gate.installable_version == "0.2.0"
    assert gate.requires_gaia_version == "0.25.0"


@pytest.mark.parametrize("manifest", [RECORDED, LEGACY], ids=["recorded", "legacy"])
def test_a_current_core_resolves_the_latest(manifest):
    gate = core_gate(manifest, "0.25.0")
    assert gate.installable_version == "0.3.0"
    assert gate.requires_gaia_version is None


def test_a_release_candidate_does_not_take_a_final_only_version():
    gate = core_gate(RECORDED, "0.25.0rc3")
    assert gate.installable_version == "0.2.0"
    assert gate.requires_gaia_version == "0.25.0"


def test_index_entry_map_drives_the_gate():
    entry = {
        "id": "gaia",
        "latest_version": "0.3.0",
        "min_gaia_version": "0.25.0",
        "min_gaia_versions": {"0.1.1": "", "0.2.0": "0.24.1", "0.3.0": "0.25.0"},
    }
    assert core_gate(entry, "0.24.1").installable_version == "0.2.0"


def test_index_entry_without_the_map_knows_only_the_latest():
    """An index written before the map existed: an older core gets no update
    offered rather than one it cannot run."""
    entry = {"id": "gaia", "latest_version": "0.3.0", "min_gaia_version": "0.25.0"}
    gate = core_gate(entry, "0.24.1")
    assert gate.installable_version is None
    assert gate.requires_gaia_version == "0.25.0"


def test_a_malformed_published_floor_fails_loudly():
    bad = _manifest({"0.1.0": "latest"})
    with pytest.raises(catalog.CatalogError, match="'gaia' version 0.1.0"):
        core_gate(bad, "0.25.0")


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


def test_installer_resolves_the_newest_runnable_version():
    version, artifact = installer._resolve_version(  # noqa: SLF001
        RECORDED, None, core_version="0.24.1"
    )
    assert version == "0.2.0"
    assert artifact["path"].startswith("agents/gaia/0.2.0/")


def test_installer_refuses_a_pinned_version_the_core_cannot_run():
    with pytest.raises(CoreTooOldError) as exc:
        installer._resolve_version(  # noqa: SLF001
            RECORDED, "0.3.0", core_version="0.24.1"
        )
    message = str(exc.value)
    assert "needs GAIA 0.25.0" in message
    assert "this is GAIA 0.24.1" in message
    assert catalog.CORE_UPGRADE_COMMAND in message
    assert "Version 0.2.0 runs on this core" in message
    assert exc.value.required == "0.25.0"


def test_installer_fails_when_no_version_runs_on_this_core():
    only_new = _manifest({"0.3.0": "0.25.0", "0.4.0": "0.26.0"})
    with pytest.raises(CoreTooOldError) as exc:
        installer._resolve_version(  # noqa: SLF001
            only_new, None, core_version="0.24.1"
        )
    assert "No published version of 'gaia' runs on GAIA 0.24.1" in str(exc.value)
    assert "needs GAIA 0.25.0" in str(exc.value)
    assert isinstance(exc.value, InstallError)


def test_install_downloads_the_runnable_version_not_the_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "_running_core_version", lambda: "0.24.1")
    data, art = _artifact("demo", "0.2.0")
    manifest = _manifest({"0.2.0": "0.24.1", "0.3.0": "0.25.0"}, agent_id="demo")
    fetched = []

    def fetcher(url):
        fetched.append(url)
        if url.endswith("/gaia-agent.yaml"):
            return b"id: demo\nname: Demo\n"
        if url.endswith(art["path"]):
            return data
        raise AssertionError(f"unexpected fetch {url}")

    result = installer.install(
        "demo",
        manifest=manifest,
        base_url="https://hub.test",
        fetcher=fetcher,
        run_pip=lambda args: None,
        install_root=tmp_path / "agents",
        active_env_site_packages=tmp_path / "site-packages",
    )
    assert result.version == "0.2.0"
    assert not any("/0.3.0/" in url for url in fetched)


def test_ensure_core_can_install_refuses_before_anything_else(monkeypatch):
    monkeypatch.setattr(catalog, "_running_core_version", lambda: "0.24.1")
    only_new = _manifest({"0.3.0": "0.25.0"}, agent_id="email")
    with pytest.raises(CoreTooOldError):
        installer.ensure_core_can_install("email", only_new, None)
    # A runnable version is fine; a manifest with nothing published is left to
    # install() to report.
    installer.ensure_core_can_install("email", RECORDED, None)
    installer.ensure_core_can_install("email", {"id": "email"}, None)


# ---------------------------------------------------------------------------
# Catalog merge (Agent UI + `gaia agent list`)
# ---------------------------------------------------------------------------

INDEX_ENTRY = {
    "id": "gaia",
    "latest_version": "0.3.0",
    "min_gaia_version": "0.25.0",
    "min_gaia_versions": {"0.1.1": "", "0.2.0": "0.24.1", "0.3.0": "0.25.0"},
    "security_tier": "verified",
}


def test_merge_does_not_offer_an_update_the_core_cannot_run():
    [row] = merge_with_registry(
        [INDEX_ENTRY], None, {"gaia": "0.2.0"}, core_version="0.24.1"
    )
    assert row["status"] == catalog.STATUS_INSTALLED
    assert row["installable_version"] == "0.2.0"
    assert row["requires_gaia_version"] == "0.25.0"


def test_merge_offers_the_newest_runnable_update():
    [row] = merge_with_registry(
        [INDEX_ENTRY], None, {"gaia": "0.1.1"}, core_version="0.24.1"
    )
    assert row["status"] == catalog.STATUS_UPDATE_AVAILABLE
    assert row["installable_version"] == "0.2.0"


def test_one_unreadable_floor_does_not_break_the_listing():
    bad = {"id": "broken", "latest_version": "1.0.0", "min_gaia_version": "soon"}
    rows = merge_with_registry(
        [bad, INDEX_ENTRY], None, {"broken": "0.9.0"}, core_version="0.25.0"
    )
    by_id = {r["id"]: r for r in rows}
    assert "not a SemVer" in by_id["broken"]["core_gate_error"]
    assert by_id["broken"]["status"] == catalog.STATUS_INSTALLED
    assert by_id["broken"]["installable_version"] is None
    assert by_id["gaia"]["installable_version"] == "0.3.0"
    assert "core_gate_error" not in by_id["gaia"]


def test_a_release_candidate_is_not_told_to_upgrade_for_its_own_flagship():
    """An RC installs the flagship from its pre-release, not the hub."""
    [row] = merge_with_registry([INDEX_ENTRY], None, {}, core_version="0.25.0rc3")
    assert row["requires_gaia_version"] is None
    # Any other agent is still gated on an RC.
    other = dict(INDEX_ENTRY, id="email")
    [row] = merge_with_registry([other], None, {}, core_version="0.25.0rc3")
    assert row["requires_gaia_version"] == "0.25.0"


def test_merge_offers_the_latest_to_a_current_core():
    [row] = merge_with_registry(
        [INDEX_ENTRY], None, {"gaia": "0.2.0"}, core_version="0.25.0"
    )
    assert row["status"] == catalog.STATUS_UPDATE_AVAILABLE
    assert row["installable_version"] == "0.3.0"
    assert row["requires_gaia_version"] is None
