# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The client must order versions the way the hub that published them does.

``workers/agent-hub/src/manifest.ts:compareSemver`` computes the
``latest_version`` the hub serves, and ``test/manifest.test.ts`` pins its rules:
numeric pre-release identifiers compare as numbers (``rc.2 < rc.10``) and build
metadata is not part of precedence. ``gaia.hub.catalog.compare_versions`` is the
client's side of that same ordering, so a version set the hub calls newest must
come back newest here too.

Driven through the real entry points — ``fetch_skill_manifest`` (what
``gaia skill install`` resolves with) and ``merge_with_registry`` (what
``gaia hub list`` and the Agent UI show) — because both failures are silent:
nothing errors, the wrong version is simply chosen.
"""

import json

import pytest

from gaia.hub.catalog import STATUS_UPDATE_AVAILABLE, merge_with_registry
from gaia.skills.hub import fetch_skill_manifest


def _version_entry(version):
    return {
        "version": version,
        "published_at": "2026-06-03T00:00:00Z",
        "publisher": "amd",
        "deprecated": False,
        "artifact": {
            "filename": f"web-research-{version}.zip",
            "path": f"skills/web-research/{version}/web-research-{version}.zip",
            "size_bytes": 1024,
            "sha256": "0" * 64,
            "content_type": "application/zip",
        },
    }


def _skill_manifest(versions, latest_version):
    """A ``skills/<name>/manifest.json`` as the hub worker writes it."""
    return json.dumps(
        {
            "name": "web-research",
            "description": "Web research skill",
            "author": "amd",
            "license": "MIT",
            "security_tier": "community",
            "permissions": [],
            "audit": {},
            "latest_version": latest_version,
            "versions": {v: _version_entry(v) for v in versions},
        }
    ).encode()


def _agent_entry(latest_version):
    return {
        "id": "demo",
        "name": "Demo",
        "description": "demo agent",
        "category": "general",
        "latest_version": latest_version,
        "icon": "",
        "language": "python",
        "author": "AMD",
        "security_tier": "community",
        "download_size_bytes": 1000,
        "requirements": {"platforms": []},
        "deprecated": False,
    }


class _NoRegistry:
    def list(self):
        return []


@pytest.mark.parametrize(
    "published, latest",
    [
        # Numeric pre-release identifiers compare as numbers, not as text.
        (["1.0.0-rc.2", "1.0.0-rc.10"], "1.0.0-rc.10"),
        # Build metadata is not part of precedence: 2.0.1+build.5 is still 2.0.1.
        (["2.0.0", "2.0.1+build.5"], "2.0.1+build.5"),
    ],
)
def test_skill_install_resolves_the_version_the_hub_calls_latest(published, latest):
    """``gaia skill install <name>`` (no pin) must pick the hub's latest_version.

    An unpinned install resolves "highest published", so disagreeing with the
    manifest's own ``latest_version`` silently installs an older skill.
    """
    raw = _skill_manifest(published, latest)
    skill = fetch_skill_manifest("web-research", fetcher=lambda url: raw)

    assert skill.latest_version == latest
    assert skill.resolve(None) == latest


def test_a_newer_prerelease_in_the_catalog_is_offered_as_an_update():
    """``gaia hub list`` must not report an outdated install as up to date."""
    merged = merge_with_registry(
        [_agent_entry("1.0.0-rc.10")],
        _NoRegistry(),
        {"demo": "1.0.0-rc.2"},
    )

    assert merged[0]["status"] == STATUS_UPDATE_AVAILABLE
