# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where a release-candidate core installs the flagship agent from.

The Agent Hub has no pre-release channel, so its ``gaia`` agent is whatever
the last flagship release published -- often many commits older than a core
release candidate. A release candidate therefore attaches a flagship frozen
from its own commit to its GitHub pre-release (publish.yml's ``rc-flagship``,
packaged by ``util/flagship_rc_assets.py``), and an RC core installs that one.

There is no fallback to the hub: if the pre-release has no flagship, the
install fails and says where it looked. A tester silently running a stale
agent is the failure this module exists to prevent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from gaia.version import __version__ as CORE_VERSION

FLAGSHIP_AGENT_ID = "gaia"
RELEASES_DOWNLOAD_URL = "https://github.com/amd/gaia/releases/download"
RELEASE_MANIFEST_NAME = "gaia-agent-release.json"
AGENT_YAML_ASSET_NAME = "gaia-agent.yaml"
SCHEMA_VERSION = 1

# What util/release_tag.py stamps into an RC wheel: 0.25.0rc3 for v0.25.0-rc3.
_PEP440_RC_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)rc(?P<rc>[1-9]\d*)$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class PrereleaseFlagshipError(RuntimeError):
    """The release candidate's own flagship could not be resolved."""


@dataclass(frozen=True)
class FlagshipSource:
    """A hub-manifest-shaped description of the RC's own flagship build."""

    tag: str
    base_url: str
    manifest: Dict[str, Any]
    agent_yaml_url: str

    @property
    def version(self) -> str:
        return self.manifest["latest_version"]

    def sha256_for(self, platform_key: str) -> Optional[str]:
        """The digest this release records for *platform_key*, if it built one."""
        for artifact in self.manifest["versions"][self.version]["artifacts"]:
            if artifact["filename"].endswith(
                (f"-{platform_key}", f"-{platform_key}.exe")
            ):
                return artifact["sha256"]
        return None


def release_candidate_tag(core_version: str) -> Optional[str]:
    """``0.25.0rc3`` -> ``v0.25.0-rc3``; ``None`` for a final or a dev build."""
    m = _PEP440_RC_RE.match(core_version)
    if not m:
        return None
    return f"v{m.group('base')}-rc{m.group('rc')}"


def flagship_source(
    agent_id: str,
    *,
    fetcher: Callable[[str], bytes],
    core_version: Optional[str] = None,
) -> Optional[FlagshipSource]:
    """The RC's own flagship, or ``None`` when the hub is the right source.

    ``None`` for any agent but the flagship and for a core that is not a
    release candidate.

    Raises:
        PrereleaseFlagshipError: this is a release candidate, and its
            pre-release does not carry a usable flagship build.
    """
    if agent_id != FLAGSHIP_AGENT_ID:
        return None
    core_version = core_version or CORE_VERSION
    tag = release_candidate_tag(core_version)
    if tag is None:
        return None

    base_url = f"{RELEASES_DOWNLOAD_URL}/{tag}"
    url = f"{base_url}/{RELEASE_MANIFEST_NAME}"
    page = f"https://github.com/amd/gaia/releases/tag/{tag}"
    try:
        raw = fetcher(url)
    except Exception as exc:  # boundary: any fetch failure is re-raised below
        raise PrereleaseFlagshipError(
            f"GAIA {core_version} is release candidate {tag}, which installs the "
            f"GAIA agent built from its own commit, not the Agent Hub's. Could not "
            f"read {url}: {exc}. Check your network; if {page} has no "
            f"{RELEASE_MANIFEST_NAME}, the pre-release is incomplete -- report it "
            f"at https://github.com/amd/gaia/issues. The hub's agent is not used "
            f"instead because it may predate this candidate."
        ) from exc
    try:
        release = json.loads(raw)
    except ValueError as exc:
        raise PrereleaseFlagshipError(
            f"{url} is not valid JSON ({exc}). Release candidate {tag} cannot "
            f"install its GAIA agent; report it at https://github.com/amd/gaia/issues."
        ) from exc
    return FlagshipSource(
        tag=tag,
        base_url=base_url,
        manifest=_hub_manifest(release, tag, url),
        agent_yaml_url=f"{base_url}/{AGENT_YAML_ASSET_NAME}",
    )


def _hub_manifest(release: Any, tag: str, url: str) -> Dict[str, Any]:
    """Validate the release file and reshape it as a one-version hub manifest."""

    def bad(why: str) -> PrereleaseFlagshipError:
        return PrereleaseFlagshipError(
            f"{url} {why}. Release candidate {tag} cannot install its GAIA agent; "
            f"report it at https://github.com/amd/gaia/issues."
        )

    if not isinstance(release, dict):
        raise bad("is not a JSON object")
    if release.get("schemaVersion") != SCHEMA_VERSION:
        raise bad(
            f"has schemaVersion {release.get('schemaVersion')!r}; this core reads "
            f"{SCHEMA_VERSION}"
        )
    if release.get("agent") != FLAGSHIP_AGENT_ID:
        raise bad(f"describes agent {release.get('agent')!r}, not 'gaia'")
    if release.get("releaseTag") != tag:
        raise bad(f"belongs to {release.get('releaseTag')!r}, not {tag}")
    agent_version = release.get("agentVersion")
    if not isinstance(agent_version, str) or not agent_version:
        raise bad("has no agentVersion")
    platforms = release.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise bad("lists no platform builds")

    artifacts = []
    for platform, entry in sorted(platforms.items()):
        if not isinstance(entry, dict):
            raise bad(f"has a malformed entry for {platform}")
        filename = entry.get("filename")
        sha = str(entry.get("sha256", ""))
        size = entry.get("size")
        if not isinstance(filename, str) or not filename.startswith("gaia-agent-"):
            raise bad(f"has no gaia-agent filename for {platform}")
        if not _SHA_RE.match(sha) or not isinstance(size, int) or size <= 0:
            raise bad(f"has no valid sha256/size for {platform}")
        artifacts.append(
            {"filename": filename, "path": filename, "sha256": sha, "size_bytes": size}
        )

    # Build metadata keeps SemVer precedence equal to the agent's own version,
    # so a later final's flagship still reads as an update.
    version = f"{agent_version}+{tag}"
    return {
        "id": FLAGSHIP_AGENT_ID,
        "name": "GAIA",
        "language": "python",
        "security_tier": "verified",
        "latest_version": version,
        "versions": {
            version: {
                "version": version,
                "artifact": artifacts[0],
                "artifacts": artifacts,
            }
        },
    }
