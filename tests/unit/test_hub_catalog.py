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


# ---------------------------------------------------------------------------
# Skills lane (#2467)
# ---------------------------------------------------------------------------


def _skill(name, version="0.1.0", **over):
    """A catalog entry as the hub Worker emits it for a published skill."""
    e = _entry(name, version=version, type="skill")
    e.update(
        {
            "category": "skills",
            "language": "python",
            "security_tier": "experimental",
            "permissions": ["network:read:*.brave.com"],
            "tools_count": 1,
            "skill_metadata": {
                "tools": [{"name": "search_web", "description": "Search the web"}],
                "tools_required": ["query_documents"],
                "requirements": {
                    "model": ">=7B",
                    "context": "",
                    "python": ">=3.10",
                    "dependencies": [],
                    "node_dependencies": [],
                    "env_vars": ["BRAVE_API_KEY"],
                    "hardware": {"npu": "", "gpu_vram": ""},
                },
                "audit": {
                    "verdict": "unaudited",
                    "engine": "",
                    "audited_at": "",
                    "findings": 0,
                },
            },
        }
    )
    e.update(over)
    return e


def test_entry_package_type_defaults_to_agent():
    # Entries published before the #1716 discriminator carry no `type` — they
    # are agents, and must not be mistaken for a skill.
    assert catalog.entry_package_type(_entry("weather")) == "agent"
    assert catalog.is_skill_entry(_entry("weather")) is False
    # An explicit null (a hand-edited/partial entry) is still an agent.
    assert catalog.entry_package_type(_entry("weather", type=None)) == "agent"


@pytest.mark.parametrize("pkg_type", ["agent", "app", "component"])
def test_non_skill_lanes_are_not_skills(pkg_type):
    assert catalog.is_skill_entry(_entry("x", type=pkg_type)) is False


def test_skill_and_agent_entry_readers_split_the_catalog():
    entries = [
        _entry("chat"),
        _skill("web-research"),
        _entry("studio", type="app"),
        _skill("incident-review"),
    ]
    assert [e["id"] for e in catalog.skill_entries(entries)] == [
        "web-research",
        "incident-review",
    ]
    assert [e["id"] for e in catalog.agent_entries(entries)] == ["chat", "studio"]
    # The skill entry survives the filter intact — the CLI/UI read its
    # tier + permissions + tools straight off it.
    skill = catalog.skill_entries(entries)[0]
    assert skill["security_tier"] == "experimental"
    assert skill["permissions"] == ["network:read:*.brave.com"]
    assert skill["skill_metadata"]["tools_required"] == ["query_documents"]


def test_merge_with_registry_excludes_skills_from_the_agent_lane():
    # A skill is not installable through the agent path, so surfacing it in the
    # merged agent catalog would offer a broken install action.
    merged = merge_with_registry(
        [_entry("chat"), _skill("web-research"), _entry("studio", type="app")],
        _FakeReg([]),
        {},
    )
    assert [a["id"] for a in merged] == ["chat", "studio"]


def test_merge_with_registry_ignores_an_installed_agent_named_like_a_skill():
    # Defensive: a locally-registered agent keeps its own entry even when the
    # catalog also lists a skill; ids are one namespace, so this should not
    # happen, but the merge must not drop the registry agent if it ever does.
    merged = merge_with_registry(
        [_skill("web-research")],
        _FakeReg([_Reg("web-research")]),
        {},
    )
    assert [a["id"] for a in merged] == ["web-research"]
    assert merged[0]["type"] == "agent"
    assert merged[0]["status"] == "installed"


def test_build_catalog_keeps_skills_out_of_the_unified_agent_list(tmp_path):
    payload = json.dumps(_index(_entry("demo"), _skill("web-research"))).encode()

    unified = build_catalog(
        _FakeReg([]),
        base_url="https://hub.test",
        fetcher=lambda url: payload,
        cache_path=tmp_path / "c.json",
        force=True,
    )
    assert [a["id"] for a in unified.agents] == ["demo"]
    # ...but it is still reachable on its own lane, not dropped on the floor.
    assert [s["id"] for s in unified.skills] == ["web-research"]
    payload = unified.to_dict()
    assert [s["id"] for s in payload["skills"]] == ["web-research"]
    # `total` stays the agent count — the number the Hub page has always shown.
    assert payload["total"] == 1


def test_older_hub_index_without_a_skills_lane_yields_no_skills(tmp_path):
    # Backward compat: an index.json served by a hub deployed before #2467 has
    # no skill entries and no `type` key at all. Every entry must stay an
    # agent, and the skills lane must be empty rather than absent or None.
    payload = json.dumps(_index(_entry("demo"), _entry("chat"))).encode()

    unified = build_catalog(
        _FakeReg([]),
        base_url="https://hub.test",
        fetcher=lambda url: payload,
        cache_path=tmp_path / "c.json",
        force=True,
    )
    assert [a["id"] for a in unified.agents] == ["chat", "demo"]
    assert unified.skills == []
    assert unified.to_dict()["skills"] == []
    # Pre-discriminator entries are agents, not an unknown lane.
    assert {a["type"] for a in unified.agents} == {"agent"}


@pytest.mark.parametrize(
    "bad_type", [None, "", 0, False, 123, ["skill"], {"kind": "skill"}]
)
def test_a_malformed_type_never_becomes_a_skill(bad_type):
    # A hand-edited or partially-written entry must fall back to the agent lane,
    # never be promoted into the skills lane on a truthy-but-wrong value.
    entry = _entry("weird", type=bad_type)
    assert catalog.is_skill_entry(entry) is False
    assert catalog.skill_entries([entry]) == []
    assert [e["id"] for e in catalog.agent_entries([entry])] == ["weird"]


def test_a_partial_skill_entry_is_still_kept_out_of_the_agent_lane():
    # The lane decision must rest on `type` alone. A skill entry that lost its
    # skill_metadata (truncated write, older Worker) is still a skill — it must
    # not fall back into the agent lane, where it would render an install
    # button wired to the agent installer.
    no_metadata = _skill("web-research")
    del no_metadata["skill_metadata"]
    junk_metadata = _skill("incident-review", skill_metadata="not-a-dict")

    entries = [_entry("chat"), no_metadata, junk_metadata]
    assert [e["id"] for e in catalog.skill_entries(entries)] == [
        "web-research",
        "incident-review",
    ]
    assert [a["id"] for a in merge_with_registry(entries, _FakeReg([]), {})] == ["chat"]


def test_a_skill_missing_optional_display_fields_does_not_break_the_split():
    # Only `id` is guaranteed by _validate_index; the reader must not require
    # any other key to classify an entry.
    minimal = {"id": "bare-skill", "type": "skill"}
    entries = [_entry("chat"), minimal]
    assert [e["id"] for e in catalog.skill_entries(entries)] == ["bare-skill"]
    assert [a["id"] for a in merge_with_registry(entries, _FakeReg([]), {})] == ["chat"]


def test_offline_with_no_cache_degrades_to_an_empty_skills_lane(tmp_path):
    def _boom(url):
        raise OSError("network down")

    unified = build_catalog(
        _FakeReg([_Reg("chat")]),
        base_url="https://hub.test",
        fetcher=_boom,
        cache_path=tmp_path / "missing.json",
        force=True,
    )
    assert unified.offline is True
    # The local registry still renders; the skills lane is empty, not absent.
    assert [a["id"] for a in unified.agents] == ["chat"]
    assert unified.skills == []


def test_the_offline_disk_cache_still_splits_the_lanes(tmp_path):
    # A cached catalog goes through the same reader, so a skill cached before
    # the network dropped must not resurface as an installable agent.
    cache = tmp_path / "catalog-cache.json"
    cache.write_text(
        json.dumps(_index(_entry("demo"), _skill("web-research"))), encoding="utf-8"
    )

    def _boom(url):
        raise OSError("network down")

    unified = build_catalog(
        _FakeReg([]),
        base_url="https://hub.test",
        fetcher=_boom,
        cache_path=cache,
        force=True,
    )
    assert unified.offline is True
    assert [a["id"] for a in unified.agents] == ["demo"]
    assert [s["id"] for s in unified.skills] == ["web-research"]


# ---------------------------------------------------------------------------
# Offline-cache staleness (#2467 follow-up)
#
# The offline cache used to be able to serve arbitrarily old data with nothing
# saying so. Working offline is supported; presenting a months-old catalog as
# the current one is not.
# ---------------------------------------------------------------------------


def _stale_cache(tmp_path, *, age_days, agent_id="cached"):
    """Write a disk cache stamped ``age_days`` in the past."""
    import datetime

    cache = tmp_path / "catalog-cache.json"
    stamped_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=age_days
    )
    document = _index(_entry(agent_id))
    document[catalog.CACHE_STAMP_KEY] = stamped_at.isoformat(timespec="seconds")
    cache.write_text(json.dumps(document))
    return cache


def _offline(cache):
    def failing_fetcher(url):
        raise ConnectionError("no network")

    return load_index(
        base_url="https://hub.test",
        fetcher=failing_fetcher,
        cache_path=cache,
        force=True,
    )


def test_a_live_fetch_stamps_the_cache_with_its_refresh_time(tmp_path):
    """Without the stamp there is nothing to measure age against."""
    cache = tmp_path / "catalog-cache.json"

    result = load_index(
        base_url="https://hub.test",
        fetcher=lambda url: json.dumps(_index(_entry("demo"))).encode(),
        cache_path=cache,
        force=True,
    )

    assert result.age_seconds == 0
    assert result.stale is False
    assert catalog.CACHE_STAMP_KEY in json.loads(cache.read_text())


def test_a_live_fetch_does_not_put_the_stamp_in_the_in_memory_catalog(tmp_path):
    """The stamp is ours; it must not look like a field the hub sent."""
    load_index(
        base_url="https://hub.test",
        fetcher=lambda url: json.dumps(_index(_entry("demo"))).encode(),
        cache_path=tmp_path / "catalog-cache.json",
        force=True,
    )

    assert catalog.CACHE_STAMP_KEY not in (catalog._MEM.raw or {})


def test_a_stale_offline_cache_is_flagged_and_warns(tmp_path, caplog):
    cache = _stale_cache(tmp_path, age_days=90)

    with caplog.at_level("WARNING", logger="gaia.hub.catalog"):
        result = _offline(cache)

    assert result.offline is True
    assert result.stale is True
    assert result.age_seconds > catalog.CACHE_STALE_AFTER_SECONDS
    assert "90 days ago" in result.age_text
    # Offline must still WORK — the point is that it says how old it is.
    assert [a["id"] for a in result.agents] == ["cached"]
    assert any("last refreshed" in r.getMessage() for r in caplog.records)


def test_a_fresh_offline_cache_is_not_flagged_stale(tmp_path, caplog):
    cache = _stale_cache(tmp_path, age_days=1)

    with caplog.at_level("WARNING", logger="gaia.hub.catalog"):
        result = _offline(cache)

    assert result.offline is True
    assert result.stale is False
    assert result.age_seconds < catalog.CACHE_STALE_AFTER_SECONDS
    assert not any("last refreshed" in r.getMessage() for r in caplog.records)


def test_a_cache_just_over_the_threshold_is_stale(tmp_path):
    """The boundary is where an off-by-one would hide the whole feature."""
    threshold_days = catalog.CACHE_STALE_AFTER_SECONDS / 86400

    assert _offline(_stale_cache(tmp_path, age_days=threshold_days + 0.5)).stale is True
    assert (
        _offline(_stale_cache(tmp_path, age_days=threshold_days - 0.5)).stale is False
    )


def test_an_unstamped_cache_falls_back_to_its_file_mtime(tmp_path):
    """A cache written by an older GAIA must not read as fresh by default."""
    import os
    import time

    cache = tmp_path / "catalog-cache.json"
    cache.write_text(json.dumps(_index(_entry("legacy"))))
    old = time.time() - catalog.CACHE_STALE_AFTER_SECONDS * 2
    os.utime(cache, (old, old))

    result = _offline(cache)

    assert result.stale is True
    assert result.age_seconds > catalog.CACHE_STALE_AFTER_SECONDS


def test_build_catalog_surfaces_the_cache_age_to_its_consumers(tmp_path):
    """The UI renders this dict; if age never reaches it, nothing can show it."""
    unified = build_catalog(
        _FakeReg([]),
        base_url="https://hub.test",
        fetcher=_raise_offline,
        cache_path=_stale_cache(tmp_path, age_days=45),
        force=True,
    )

    payload = unified.to_dict()
    assert payload["stale"] is True
    assert payload["age_text"] == "45 days ago"
    assert payload["age_seconds"] > catalog.CACHE_STALE_AFTER_SECONDS


def _raise_offline(url):
    raise ConnectionError("no network")


def test_describe_age_reads_like_a_person_wrote_it():
    assert catalog.describe_age(0) == "just now"
    assert catalog.describe_age(60 * 30) == "30 minutes ago"
    assert catalog.describe_age(3600 * 5) == "5 hours ago"
    assert catalog.describe_age(86400 * 3) == "3 days ago"
    assert catalog.describe_age(None) == "unknown"
