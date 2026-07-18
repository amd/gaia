# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.hub.catalog — fetch, cache, and registry merge."""

import json

import pytest

from gaia.hub import catalog
from gaia.hub.catalog import (
    CatalogError,
    build_catalog,
    compare_versions,
    load_index,
    merge_with_registry,
)


@pytest.fixture(autouse=True)
def _clear_mem_cache():
    catalog.clear_cache()
    yield
    catalog.clear_cache()


def _index(*agents):
    return {
        "schema_version": 1,
        "generated_at": "2026-06-03T00:00:00Z",
        "agents": list(agents),
    }


def _entry(agent_id, version="1.0.0", **over):
    e = {
        "id": agent_id,
        "name": agent_id.title(),
        "description": f"{agent_id} agent",
        "category": "general",
        "latest_version": version,
        "icon": "",
        "language": "python",
        "author": "AMD",
        "security_tier": "community",
        "download_size_bytes": 1000,
        "requirements": {"platforms": []},
        "deprecated": False,
    }
    e.update(over)
    return e


class _FakeReg:
    def __init__(self, regs):
        self._regs = regs

    def list(self):
        return self._regs


class _Reg:
    def __init__(self, agent_id, source="builtin"):
        self.id = agent_id
        self.name = agent_id.title()
        self.description = ""
        self.category = "general"
        self.icon = ""
        self.language = "python"
        self.source = source


# ---------------------------------------------------------------------------
# Fetch + cache
# ---------------------------------------------------------------------------


def test_load_index_network_success_writes_cache(tmp_path):
    cache = tmp_path / "catalog-cache.json"
    payload = json.dumps(_index(_entry("demo"))).encode()

    def fetcher(url):
        assert url.endswith("/index.json")
        return payload

    result = load_index(
        base_url="https://hub.test", fetcher=fetcher, cache_path=cache, force=True
    )
    assert result.offline is False
    assert result.source == "network"
    assert [a["id"] for a in result.agents] == ["demo"]
    assert cache.exists()
    assert json.loads(cache.read_text())["agents"][0]["id"] == "demo"


def test_offline_fallback_uses_disk_cache(tmp_path):
    cache = tmp_path / "catalog-cache.json"
    cache.write_text(json.dumps(_index(_entry("cached"))))

    def failing_fetcher(url):
        raise ConnectionError("no network")

    result = load_index(
        base_url="https://hub.test",
        fetcher=failing_fetcher,
        cache_path=cache,
        force=True,
    )
    assert result.offline is True
    assert result.source == "cache"
    assert result.agents[0]["id"] == "cached"


def test_no_network_no_cache_raises(tmp_path):
    def failing_fetcher(url):
        raise ConnectionError("no network")

    with pytest.raises(CatalogError):
        load_index(
            base_url="https://hub.test",
            fetcher=failing_fetcher,
            cache_path=tmp_path / "missing.json",
            force=True,
        )


def test_malformed_index_raises(tmp_path):
    def fetcher(url):
        return b'{"no_agents": true}'

    with pytest.raises(CatalogError):
        load_index(
            base_url="https://hub.test",
            fetcher=fetcher,
            cache_path=tmp_path / "c.json",
            force=True,
        )


# ---------------------------------------------------------------------------
# SemVer compare
# ---------------------------------------------------------------------------


def test_compare_versions():
    assert compare_versions("1.0.1", "1.0.0") == 1
    assert compare_versions("1.0.0", "1.0.1") == -1
    assert compare_versions("2.0.0", "2.0.0") == 0
    assert compare_versions("1.2.0", "1.10.0") == -1
    # Release outranks its prerelease.
    assert compare_versions("1.0.0", "1.0.0-rc.1") == 1


# ---------------------------------------------------------------------------
# Merge + status
# ---------------------------------------------------------------------------


def test_merge_status_available_when_not_installed():
    merged = merge_with_registry([_entry("demo")], _FakeReg([]), {})
    assert merged[0]["status"] == "available"
    assert merged[0]["source"] == "hub"


def test_merge_status_installed_at_latest():
    merged = merge_with_registry(
        [_entry("demo", version="1.0.0")], _FakeReg([]), {"demo": "1.0.0"}
    )
    assert merged[0]["status"] == "installed"
    assert merged[0]["installed_version"] == "1.0.0"


def test_merge_status_update_available():
    merged = merge_with_registry(
        [_entry("demo", version="2.0.0")], _FakeReg([]), {"demo": "1.0.0"}
    )
    assert merged[0]["status"] == "update_available"
    assert merged[0]["latest_version"] == "2.0.0"


def test_merge_builtin_registry_only_is_installed():
    merged = merge_with_registry([], _FakeReg([_Reg("chat")]), {})
    assert merged[0]["id"] == "chat"
    assert merged[0]["status"] == "installed"
    assert merged[0]["source"] == "builtin"


def test_merge_registered_catalog_agent_is_installed():
    # In the catalog AND registered (entry-point installed) but no sentinel
    # version known -> still treated as installed, not available.
    merged = merge_with_registry(
        [_entry("demo")], _FakeReg([_Reg("demo", source="installed")]), {}
    )
    assert merged[0]["status"] == "installed"


def test_merge_propagates_type_and_permissions_for_hub_lanes():
    # #1722: the Hub page's Apps/Components/Agents lanes and install trust gate
    # read `type` and `permissions` off the merged entry. They must survive the
    # registry merge from the catalog index (#1716 discriminator).
    merged = merge_with_registry(
        [
            _entry("studio", type="app", permissions=["fs:read", "net:fetch"]),
            _entry("rag-kit", type="component"),
            _entry("weather"),  # no type -> defaults to "agent"
        ],
        _FakeReg([]),
        {},
    )
    by_id = {a["id"]: a for a in merged}
    assert by_id["studio"]["type"] == "app"
    assert by_id["studio"]["permissions"] == ["fs:read", "net:fetch"]
    assert by_id["rag-kit"]["type"] == "component"
    assert by_id["weather"]["type"] == "agent"
    assert by_id["weather"]["permissions"] == []


def test_merge_registry_only_agent_defaults_to_agent_type():
    # Builtins / custom agents are always the "agent" kind with no declared
    # permissions — apps and components exist only as published hub packages.
    merged = merge_with_registry([], _FakeReg([_Reg("chat")]), {})
    assert merged[0]["type"] == "agent"
    assert merged[0]["permissions"] == []


def test_build_catalog_degrades_to_registry_when_offline_no_cache(tmp_path):
    # Hub unreachable AND no on-disk cache: the unified catalog must still
    # return the local registry (builtins stay usable) flagged offline, rather
    # than propagating CatalogError up to the UI as a blocking error.
    def failing_fetcher(url):
        raise ConnectionError("no network")

    unified = build_catalog(
        _FakeReg([_Reg("chat")]),
        base_url="https://hub.test",
        fetcher=failing_fetcher,
        cache_path=tmp_path / "missing.json",
        force=True,
    )
    assert unified.offline is True
    assert [a["id"] for a in unified.agents] == ["chat"]
    assert unified.agents[0]["status"] == "installed"


def test_build_catalog_merges(tmp_path):
    payload = json.dumps(_index(_entry("demo", version="3.0.0"))).encode()

    unified = build_catalog(
        _FakeReg([_Reg("chat")]),
        base_url="https://hub.test",
        fetcher=lambda url: payload,
        cache_path=tmp_path / "c.json",
        installed_versions={"demo": "1.0.0"},
        force=True,
    )
    by_id = {a["id"]: a for a in unified.agents}
    assert by_id["demo"]["status"] == "update_available"
    assert by_id["chat"]["status"] == "installed"
    assert unified.offline is False
