# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent Hub catalog: fetch, cache, and merge with the local registry.

The catalog is served by the Cloudflare R2 Worker (``workers/agent-hub/``, see
#1095) as two JSON documents:

* ``GET {hub}/index.json`` — the lightweight catalog: one entry per agent
  summarising its latest published version (schema
  ``workers/agent-hub/schemas/index.schema.json``).
* ``GET {hub}/agents/<id>/manifest.json`` — the per-agent aggregate manifest
  with every published version + artifact (sha256, R2 path, size).

This module fetches ``index.json`` with a short in-memory TTL cache, persists a
copy to ``~/.gaia/catalog-cache.json`` so the UI still renders when offline,
and merges the remote catalog with the live :class:`AgentRegistry` to produce a
unified per-agent view with a ``status`` of ``installed`` / ``available`` /
``update_available``.

Fail-loudly (CLAUDE.md): :func:`load_index` raises :class:`CatalogError` naming
what to try when it can produce no remote catalog at all. The unified
:func:`build_catalog` then degrades to the *local registry alone* so the UI
stays usable offline — and every offline path is flagged (`offline=True`)
rather than hidden. Installing from the hub still fails loudly (you cannot pull
an artifact from an unreachable hub).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

# Default hub origin. Overridable via GAIA_HUB_URL so dev/CI can point at a
# local Worker (`wrangler dev`) or a file:// fixture host. No trailing slash.
DEFAULT_HUB_URL = "https://hub.amd-gaia.ai"

# In-memory cache TTL for index.json. The UI polls the catalog whenever the
# discover panel opens; 5 minutes keeps it fresh without hammering R2.
CACHE_TTL_SECONDS = 300

# HTTP timeout for catalog fetches (seconds). Short — the catalog is small and
# the offline cache covers a slow/absent network.
_HTTP_TIMEOUT = 10

# Fetcher signature: a callable taking a URL and returning the raw response
# bytes, or raising on any transport/HTTP error. Injected in tests.
Fetcher = Callable[[str], bytes]


class CatalogError(RuntimeError):
    """Raised when the catalog cannot be produced (no network and no cache).

    The message names what failed, what to do, and where to look, per the
    project's fail-loudly rule.
    """


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def get_hub_base_url() -> str:
    """Return the hub origin, honouring ``GAIA_HUB_URL`` (no trailing slash)."""
    return os.environ.get("GAIA_HUB_URL", DEFAULT_HUB_URL).rstrip("/")


def default_cache_path() -> Path:
    """Path of the on-disk offline catalog cache."""
    return Path.home() / ".gaia" / "catalog-cache.json"


def index_url(base_url: Optional[str] = None) -> str:
    return f"{base_url or get_hub_base_url()}/index.json"


def manifest_url(agent_id: str, base_url: Optional[str] = None) -> str:
    return f"{base_url or get_hub_base_url()}/agents/{agent_id}/manifest.json"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def fetch_bytes(url: str, timeout: int = _HTTP_TIMEOUT) -> bytes:
    """Default fetcher: GET *url* and return the raw body bytes.

    Supports ``file://`` URLs so tests and offline mirrors can point
    ``GAIA_HUB_URL`` at a local directory. Raises on any error (fail loudly).
    """
    if url.startswith("file://"):
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        local = Path(url2pathname(urlparse(url).path))
        return local.read_bytes()

    import requests

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def _fetch_json(url: str, fetcher: Fetcher) -> Any:
    raw = fetcher(url)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Index validation
# ---------------------------------------------------------------------------


def _validate_index(data: Any) -> List[Dict[str, Any]]:
    """Validate the parsed ``index.json`` and return its ``agents`` list."""
    if not isinstance(data, dict):
        raise CatalogError(
            "Hub index.json is malformed (expected a JSON object). The hub may "
            "be misconfigured; check GAIA_HUB_URL or try again later."
        )
    agents = data.get("agents")
    if not isinstance(agents, list):
        raise CatalogError(
            "Hub index.json is missing the 'agents' array. The hub may be "
            "misconfigured; check GAIA_HUB_URL."
        )
    return [a for a in agents if isinstance(a, dict) and a.get("id")]


# ---------------------------------------------------------------------------
# In-memory TTL cache
# ---------------------------------------------------------------------------


@dataclass
class _MemCache:
    base_url: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    fetched_at: float = 0.0


_MEM = _MemCache()


def clear_cache() -> None:
    """Drop the in-memory catalog cache (test/maintenance hook)."""
    global _MEM
    _MEM = _MemCache()


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def _write_disk_cache(cache_path: Path, data: Dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        # Cache write failure must not break a successful live fetch; log it.
        logger.warning("catalog: could not write cache %s: %s", cache_path, exc)


def _read_disk_cache(cache_path: Path) -> Optional[Dict[str, Any]]:
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("catalog: could not read cache %s: %s", cache_path, exc)
        return None


# ---------------------------------------------------------------------------
# Catalog fetch
# ---------------------------------------------------------------------------


@dataclass
class CatalogResult:
    """The fetched catalog plus provenance flags."""

    agents: List[Dict[str, Any]]
    offline: bool
    source: str  # "memory" | "network" | "cache"
    generated_at: Optional[str] = None


def load_index(
    *,
    base_url: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
    cache_path: Optional[Path] = None,
    force: bool = False,
) -> CatalogResult:
    """Fetch ``index.json`` with TTL + offline-cache fallback.

    Order of resolution:

    1. Fresh in-memory cache (within :data:`CACHE_TTL_SECONDS`) unless *force*.
    2. Live network fetch → refreshes both caches, ``offline=False``.
    3. On network/parse failure, the on-disk cache → ``offline=True``.
    4. If none of the above yield data, raises :class:`CatalogError`.
    """
    base_url = (base_url or get_hub_base_url()).rstrip("/")
    fetcher = fetcher or fetch_bytes
    cache_path = Path(cache_path) if cache_path else default_cache_path()

    now = time.monotonic()
    if (
        not force
        and _MEM.raw is not None
        and _MEM.base_url == base_url
        and (now - _MEM.fetched_at) < CACHE_TTL_SECONDS
    ):
        data = _MEM.raw
        return CatalogResult(
            agents=_validate_index(data),
            offline=False,
            source="memory",
            generated_at=data.get("generated_at"),
        )

    try:
        data = _fetch_json(index_url(base_url), fetcher)
        agents = _validate_index(data)
    except CatalogError:
        raise
    except Exception as exc:  # noqa: BLE001 - any transport/parse error → fallback
        logger.warning("catalog: live fetch failed (%s); trying offline cache", exc)
        cached = _read_disk_cache(cache_path)
        if cached is None:
            raise CatalogError(
                "Could not reach the GAIA Agent Hub and no offline cache is "
                f"available. Check your internet connection or GAIA_HUB_URL "
                f"(currently {base_url}). Original error: {exc}"
            ) from exc
        return CatalogResult(
            agents=_validate_index(cached),
            offline=True,
            source="cache",
            generated_at=cached.get("generated_at"),
        )

    # Live fetch succeeded — refresh both caches.
    _MEM.base_url = base_url
    _MEM.raw = data
    _MEM.fetched_at = now
    _write_disk_cache(cache_path, data)
    return CatalogResult(
        agents=agents,
        offline=False,
        source="network",
        generated_at=data.get("generated_at"),
    )


def cached_index_agents(cache_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return the ``agents`` list from the on-disk catalog cache, or ``[]``.

    Offline-only: never touches the network. Used to enrich locally-installed
    agents (that aren't in the live registry) with the name/description/icon the
    hub last published, so the agent picker renders a real card even when the
    hub is unreachable. A missing or malformed cache yields ``[]`` — the caller
    falls back to a minimal entry rather than failing.
    """
    cache_path = Path(cache_path) if cache_path else default_cache_path()
    cached = _read_disk_cache(cache_path)
    if cached is None:
        return []
    try:
        return _validate_index(cached)
    except CatalogError as exc:
        logger.warning("catalog: cached index is malformed (%s); ignoring", exc)
        return []


def fetch_manifest(
    agent_id: str,
    *,
    base_url: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
) -> Dict[str, Any]:
    """Fetch the per-agent aggregate manifest (``agents/<id>/manifest.json``)."""
    fetcher = fetcher or fetch_bytes
    data = _fetch_json(manifest_url(agent_id, base_url), fetcher)
    if not isinstance(data, dict) or not data.get("versions"):
        raise CatalogError(
            f"Hub manifest for '{agent_id}' is malformed or has no published "
            f"versions. Try again later or check GAIA_HUB_URL."
        )
    return data


# ---------------------------------------------------------------------------
# SemVer comparison
# ---------------------------------------------------------------------------


def _parse_version(version: str):
    """Parse ``MAJOR.MINOR.PATCH[-prerelease]`` into a sortable key.

    A release sorts above its prereleases (1.0.0 > 1.0.0-rc.1), matching SemVer
    precedence well enough for "is the catalog newer than what's installed".
    """
    core, _, pre = version.partition("-")
    parts = []
    for piece in core.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    # Release (no prerelease) ranks higher: use 1 for release, 0 for prerelease.
    return (parts[0], parts[1], parts[2], 1 if not pre else 0, pre)


def compare_versions(a: str, b: str) -> int:
    """Return -1/0/1 for *a* <, ==, > *b* by SemVer precedence."""
    ka, kb = _parse_version(a), _parse_version(b)
    if ka < kb:
        return -1
    if ka > kb:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Merge with local registry
# ---------------------------------------------------------------------------

STATUS_INSTALLED = "installed"
STATUS_AVAILABLE = "available"
STATUS_UPDATE_AVAILABLE = "update_available"

# Package-kind discriminator (#1716). Kept in sync with
# ``gaia.hub.manifest.DEFAULT_TYPE`` — the merge defaults to it when the catalog
# entry predates the field and for registry-only agents.
DEFAULT_PACKAGE_TYPE = "agent"


def _requires_trust(language: str, security_tier: str) -> bool:
    """Whether a catalog entry needs an explicit native-trust opt-in to install.

    Native (``cpp``) agents outside the ``verified`` tier ship an unsandboxed
    binary from a non-AMD-audited publisher; the UI prompts before installing.
    """
    return language == "cpp" and security_tier != "verified"


def merge_with_registry(
    index_agents: List[Dict[str, Any]],
    registry: Any,
    installed_versions: Optional[Dict[str, str]] = None,
    *,
    include_deprecated: bool = False,
) -> List[Dict[str, Any]]:
    """Merge the remote catalog with the live registry into a unified list.

    Args:
        index_agents: The ``agents`` list from ``index.json``.
        registry: The live :class:`AgentRegistry` (provides ``list()``).
        installed_versions: Map of ``agent_id -> version`` for hub-installed
            agents, read from install sentinels (see
            :func:`gaia.hub.installer.list_installed`). Builtin/custom agents
            present in the registry but absent here are treated as installed
            with an unknown version.
        include_deprecated: When False (default), deprecated agents that are not
            already installed are excluded from the listing. Installed agents are
            always shown so a user can still see/manage what they have.

    Returns:
        One dict per agent (union of registry + catalog), each carrying a
        ``status``, ``installed_version`` / ``latest_version``, and a
        ``requires_trust`` flag.
    """
    installed_versions = installed_versions or {}

    registered = {}
    if registry is not None:
        for reg in registry.list():
            registered[reg.id] = reg

    by_id: Dict[str, Dict[str, Any]] = {}

    # 1. Catalog entries (remote source of truth for latest_version).
    for entry in index_agents:
        agent_id = entry["id"]
        latest = entry.get("latest_version")
        reg = registered.get(agent_id)
        installed_ver = installed_versions.get(agent_id)

        if installed_ver:
            if latest and compare_versions(latest, installed_ver) > 0:
                status = STATUS_UPDATE_AVAILABLE
            else:
                status = STATUS_INSTALLED
        elif reg is not None:
            status = STATUS_INSTALLED
        else:
            status = STATUS_AVAILABLE

        language = entry.get("language", "python")
        security_tier = entry.get("security_tier", "experimental")
        merged: Dict[str, Any] = {
            "id": agent_id,
            "name": entry.get("name", agent_id),
            "description": entry.get("description", ""),
            "category": entry.get("category", "general"),
            # Package kind (#1716): agent | app | component. Drives the Hub
            # page's Apps · Components · Agents lanes; defaults to "agent" for
            # older catalog entries that predate the discriminator.
            "type": entry.get("type", DEFAULT_PACKAGE_TYPE),
            "icon": entry.get("icon", ""),
            "language": language,
            "author": entry.get("author", ""),
            "security_tier": security_tier,
            "requires_trust": _requires_trust(language, security_tier),
            # Declared permission scopes (``<domain>:<action>``) shown in the
            # install trust gate. Absent from older entries / local-only agents.
            "permissions": entry.get("permissions", []),
            "download_size_bytes": entry.get("download_size_bytes", 0),
            "requirements": entry.get("requirements", {"platforms": []}),
            "deprecated": entry.get("deprecated", False),
            "latest_version": latest,
            "installed_version": installed_ver,
            "status": status,
            "source": (reg.source if reg is not None else "hub"),
        }
        # Optional eval scorecard fields — absent from older catalog entries and
        # from builtin/custom agents that haven't run a benchmark yet.
        if "eval_score" in entry:
            merged["eval_score"] = entry["eval_score"]
        if "eval_scorecard_url" in entry:
            merged["eval_scorecard_url"] = entry["eval_scorecard_url"]
        by_id[agent_id] = merged

    # 2. Registry-only agents (builtins / custom not published to the hub).
    for agent_id, reg in registered.items():
        if agent_id in by_id:
            continue
        reg_tier = "verified" if reg.source == "builtin" else "experimental"
        by_id[agent_id] = {
            "id": agent_id,
            "name": reg.name,
            "description": reg.description,
            "category": reg.category,
            # Registry-only agents (builtins / custom) are always "agent" —
            # apps and components only exist as published hub packages.
            "type": DEFAULT_PACKAGE_TYPE,
            "icon": reg.icon,
            "language": reg.language,
            "author": "",
            "security_tier": reg_tier,
            "requires_trust": _requires_trust(reg.language, reg_tier),
            "permissions": [],
            "download_size_bytes": 0,
            "requirements": {"platforms": []},
            "deprecated": False,
            "latest_version": installed_versions.get(agent_id),
            "installed_version": installed_versions.get(agent_id),
            "status": STATUS_INSTALLED,
            "source": reg.source,
        }

    # Hide deprecated, not-yet-installed agents from the default listing. They
    # remain installable via include_deprecated (the UI confirms before install).
    if not include_deprecated:
        by_id = {
            aid: a
            for aid, a in by_id.items()
            if not (a["deprecated"] and a["status"] == STATUS_AVAILABLE)
        }

    return sorted(by_id.values(), key=lambda a: a["id"])


@dataclass
class UnifiedCatalog:
    """The merged catalog returned by :func:`build_catalog`."""

    agents: List[Dict[str, Any]] = field(default_factory=list)
    offline: bool = False
    generated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agents": self.agents,
            "offline": self.offline,
            "generated_at": self.generated_at,
            "total": len(self.agents),
        }


def build_catalog(
    registry: Any,
    *,
    base_url: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
    cache_path: Optional[Path] = None,
    installed_versions: Optional[Dict[str, str]] = None,
    force: bool = False,
    include_deprecated: bool = False,
) -> UnifiedCatalog:
    """Fetch the catalog and merge it with the registry into a unified view.

    When the hub is unreachable and no offline cache exists, the catalog
    degrades to the local registry alone (builtin/installed agents), flagged
    ``offline=True`` — the UI stays usable instead of erroring out. Remote-only
    "available" agents simply aren't listed until the hub is reachable again.
    """
    try:
        result = load_index(
            base_url=base_url, fetcher=fetcher, cache_path=cache_path, force=force
        )
        index_agents = result.agents
        offline = result.offline
        generated_at = result.generated_at
    except CatalogError as exc:
        # No remote catalog AND no cache: still show what's installed locally.
        logger.warning(
            "catalog: no remote catalog available (%s); showing local registry only",
            exc,
        )
        index_agents = []
        offline = True
        generated_at = None

    merged = merge_with_registry(
        index_agents,
        registry,
        installed_versions,
        include_deprecated=include_deprecated,
    )
    return UnifiedCatalog(
        agents=merged,
        offline=offline,
        generated_at=generated_at,
    )
