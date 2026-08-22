# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Contract tests for ``packaging/gen_binaries_lock.py`` (schema 3.0, two lanes).

The lock this writes is the integrity gate the npm client enforces on every
download, and half of it now points at the ``terminal-hub`` lane — which this
package does not publish. A wrong filename or a wrong lane version there is not
a build failure anywhere; it is a 404 on a user's first run. These tests cover
the generator's loud failures as much as its happy path.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location(
    "gaia_gen_binaries_lock", PACKAGING / "gen_binaries_lock.py"
)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

AGENT_VERSION = "0.1.0"
TUI_VERSION = "0.23.0"
SIDECAR_BASE = f"https://hub.amd-gaia.ai/agents/gaia/{AGENT_VERSION}"
TUI_BASE = f"https://hub.amd-gaia.ai/agents/terminal-hub/{TUI_VERSION}"

COMMITTED_LOCK = (
    Path(__file__).resolve().parents[3] / "gaia" / "npm" / "binaries.lock.json"
)

SIDECAR_ARTIFACTS = {
    "win32-x64": "gaia-agent-win32-x64.exe",
    "darwin-arm64": "gaia-agent-darwin-arm64",
    "darwin-x64": "gaia-agent-darwin-x64",
    "linux-x64": "gaia-agent-linux-x64",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _record(component: str, platform: str, filename: str) -> dict:
    body = f"{component}-{platform}"
    return {
        "component": component,
        "platform": platform,
        "filename": filename,
        "executable": ("gaia-agent" if component == "sidecar" else "gaia-tui")
        + (".exe" if filename.endswith(".exe") else ""),
        "sha256": _sha(body),
        "size": len(body),
    }


def _write_meta(tmp_path: Path, rec: dict) -> Path:
    p = tmp_path / f"{rec['component']}-{rec['platform']}.meta.json"
    p.write_text(json.dumps([rec], indent=2), encoding="utf-8")
    return p


def _seed_lock(tmp_path: Path) -> Path:
    """A copy of the committed lock — the real starting state of a release."""
    lock = tmp_path / "binaries.lock.json"
    lock.write_text(COMMITTED_LOCK.read_text(encoding="utf-8"), encoding="utf-8")
    return lock


def _run(lock: Path, metas: list[Path], **overrides) -> int:
    argv = [
        "--version",
        overrides.get("version", AGENT_VERSION),
        "--sidecar-base-url",
        overrides.get("sidecar_base_url", SIDECAR_BASE),
        "--tui-version",
        overrides.get("tui_version", TUI_VERSION),
        "--tui-base-url",
        overrides.get("tui_base_url", TUI_BASE),
        "--lock",
        str(lock),
    ]
    for m in metas:
        argv += ["--meta", str(m)]
    return gen.main(argv)


def _all_metas(tmp_path: Path) -> list[Path]:
    metas = [
        _write_meta(tmp_path, _record("sidecar", p, f))
        for p, f in SIDECAR_ARTIFACTS.items()
    ]
    metas += [
        _write_meta(tmp_path, _record("tui", p, f))
        for p, f in gen.TUI_ARTIFACT_NAMES.items()
    ]
    return metas


def test_committed_lock_is_the_schema_this_generator_writes():
    # The generator refuses a lock of another schema, so drift between the two
    # would break every release rather than any one test.
    committed = json.loads(COMMITTED_LOCK.read_text(encoding="utf-8"))
    assert committed["schemaVersion"] == gen.SCHEMA_VERSION
    assert set(committed["components"]) == set(gen.COMPONENTS)
    # No leftover shared base URL from schema 2.0.
    assert "baseUrl" not in committed
    # The committed tui pin is what release_agent_gaia.yml reads to decide which
    # terminal-hub version must be published before this package can ship.
    tui = committed["components"]["tui"]
    assert tui["baseUrl"].endswith(f"/agents/terminal-hub/{tui['componentVersion']}")


def test_full_release_writes_both_lanes(tmp_path, capsys):
    lock = _seed_lock(tmp_path)
    assert _run(lock, _all_metas(tmp_path)) == 0
    written = json.loads(lock.read_text(encoding="utf-8"))

    assert written["schemaVersion"] == "3.0"
    assert written["agentVersion"] == AGENT_VERSION
    assert "baseUrl" not in written

    sidecar = written["components"]["sidecar"]
    tui = written["components"]["tui"]
    assert sidecar["componentVersion"] == AGENT_VERSION
    assert sidecar["baseUrl"] == SIDECAR_BASE
    assert tui["componentVersion"] == TUI_VERSION
    assert tui["baseUrl"] == TUI_BASE
    # The two lanes are genuinely different — the whole point of schema 3.0.
    assert tui["baseUrl"] != sidecar["baseUrl"]

    assert set(sidecar["platforms"]) == set(SIDECAR_ARTIFACTS)
    assert set(tui["platforms"]) == set(gen.TUI_ARTIFACT_NAMES)
    for platform, entry in tui["platforms"].items():
        assert entry["filename"] == gen.TUI_ARTIFACT_NAMES[platform]
        assert entry["sha256"] == _sha(f"tui-{platform}")
        assert entry["size"] > 0
    # Installed under our own name, never the hub's `gaia`.
    assert tui["platforms"]["win32-x64"]["executable"] == "gaia-tui.exe"
    assert tui["platforms"]["linux-x64"]["executable"] == "gaia-tui"


def test_win32_key_maps_onto_terminal_hubs_win_artifact(tmp_path):
    lock = _seed_lock(tmp_path)
    assert _run(lock, _all_metas(tmp_path)) == 0
    tui = json.loads(lock.read_text(encoding="utf-8"))["components"]["tui"]["platforms"]
    # process.platform says win32; terminal-hub publishes win-*. The mapping is
    # carried by the filename, and the key stays in the npm namespace.
    assert tui["win32-x64"]["filename"] == "gaia-win-x64.exe"
    assert tui["win32-arm64"]["filename"] == "gaia-win-arm64.exe"
    assert all("win32" not in e["filename"] for e in tui.values())


def test_partial_metas_keep_the_other_platforms(tmp_path):
    lock = _seed_lock(tmp_path)
    _run(lock, _all_metas(tmp_path))
    before = json.loads(lock.read_text(encoding="utf-8"))

    only = _write_meta(
        tmp_path, _record("sidecar", "linux-x64", "gaia-agent-linux-x64")
    )
    assert _run(lock, [only]) == 0
    after = json.loads(lock.read_text(encoding="utf-8"))
    assert (
        after["components"]["tui"]["platforms"]
        == before["components"]["tui"]["platforms"]
    )
    assert set(after["components"]["sidecar"]["platforms"]) == set(SIDECAR_ARTIFACTS)


def test_rejects_a_tui_filename_terminal_hub_does_not_publish(tmp_path):
    # The regression this whole change is guarding: the old release published
    # `gaia-tui-<platform>` under our own lane. Feeding that name now must fail —
    # it would 404 against terminal-hub.
    lock = _seed_lock(tmp_path)
    bad = _write_meta(tmp_path, _record("tui", "win32-x64", "gaia-tui-win32-x64.exe"))
    with pytest.raises(SystemExit) as e:
        _run(lock, [bad])
    assert "gaia-win-x64.exe" in str(e.value)
    assert "terminal-hub" in str(e.value)


def test_rejects_a_stale_committed_entry_no_meta_overwrites(tmp_path):
    # An entry with no meta this run keeps its committed value, so a stale
    # hand-edit would otherwise ship unnoticed.
    lock = _seed_lock(tmp_path)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["components"]["tui"]["platforms"]["linux-x64"][
        "filename"
    ] = "gaia-tui-linux-x64"
    lock.write_text(json.dumps(data), encoding="utf-8")
    only_sidecar = _write_meta(
        tmp_path, _record("sidecar", "linux-x64", "gaia-agent-linux-x64")
    )
    with pytest.raises(SystemExit) as e:
        _run(lock, [only_sidecar])
    assert "stale committed value" in str(e.value)


def test_rejects_a_stale_platform_the_component_does_not_publish(tmp_path):
    lock = _seed_lock(tmp_path)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["components"]["sidecar"]["platforms"]["linux-arm64"] = {
        "filename": "gaia-agent-linux-arm64",
        "executable": "gaia-agent",
        "sha256": "d" * 64,
        "size": 1,
    }
    lock.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        _run(lock, _all_metas(tmp_path))
    assert "unsupported platform" in str(e.value)


def test_rejects_a_placeholder_hash(tmp_path):
    lock = _seed_lock(tmp_path)
    rec = _record("tui", "linux-x64", "gaia-linux-x64")
    rec["sha256"] = "PENDING-replace-with-real-sha256"
    with pytest.raises(SystemExit) as e:
        _run(lock, [_write_meta(tmp_path, rec)])
    assert "placeholder" in str(e.value)


def test_rejects_a_truncated_hash(tmp_path):
    lock = _seed_lock(tmp_path)
    rec = _record("sidecar", "linux-x64", "gaia-agent-linux-x64")
    rec["sha256"] = "abc123"
    with pytest.raises(SystemExit) as e:
        _run(lock, [_write_meta(tmp_path, rec)])
    assert "non-sha256" in str(e.value)


def test_rejects_an_unknown_component(tmp_path):
    lock = _seed_lock(tmp_path)
    rec = _record("sidecar", "linux-x64", "gaia-agent-linux-x64")
    rec["component"] = "installer"
    with pytest.raises(SystemExit) as e:
        _run(lock, [_write_meta(tmp_path, rec)])
    assert "unknown component" in str(e.value)


def test_rejects_an_unsupported_platform_for_the_component(tmp_path):
    # The sidecar has no arm64 Linux freeze; the TUI does. A meta claiming one
    # must not quietly create an entry the installer would then 404 on.
    lock = _seed_lock(tmp_path)
    rec = _record("sidecar", "linux-arm64", "gaia-agent-linux-arm64")
    with pytest.raises(SystemExit) as e:
        _run(lock, [_write_meta(tmp_path, rec)])
    assert "unsupported platform" in str(e.value)


@pytest.mark.parametrize(
    "overrides, needle",
    [
        ({"sidecar_base_url": "ftp://hub.amd-gaia.ai/agents/gaia/0.1.0"}, "http(s)"),
        ({"tui_base_url": "hub.amd-gaia.ai/agents/terminal-hub/0.23.0"}, "http(s)"),
        # A base URL whose trailing segment disagrees with its version would
        # point every installer at a different release than the one pinned.
        (
            {"tui_base_url": "https://hub.amd-gaia.ai/agents/terminal-hub/0.22.0"},
            "0.22.0",
        ),
        ({"sidecar_base_url": "https://hub.amd-gaia.ai/agents/gaia/0.0.9"}, "0.0.9"),
    ],
)
def test_rejects_a_bad_base_url(tmp_path, overrides, needle):
    lock = _seed_lock(tmp_path)
    with pytest.raises(SystemExit) as e:
        _run(lock, _all_metas(tmp_path), **overrides)
    assert needle in str(e.value)


def test_rejects_a_lock_of_another_schema(tmp_path):
    lock = tmp_path / "binaries.lock.json"
    lock.write_text(
        json.dumps({"schemaVersion": "2.0", "baseUrl": SIDECAR_BASE, "components": {}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as e:
        _run(lock, _all_metas(tmp_path))
    assert "schemaVersion" in str(e.value)


def test_rejects_a_missing_meta_file(tmp_path):
    lock = _seed_lock(tmp_path)
    with pytest.raises(SystemExit) as e:
        _run(lock, [tmp_path / "nope.meta.json"])
    assert "not found" in str(e.value)


def test_rejects_a_meta_that_is_not_a_record_array(tmp_path):
    lock = _seed_lock(tmp_path)
    bad = tmp_path / "bad.meta.json"
    bad.write_text(json.dumps({"component": "tui"}), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        _run(lock, [bad])
    assert "JSON array" in str(e.value)


def test_generated_lock_matches_the_npm_clients_expectations(tmp_path):
    """Every entry the generator writes must satisfy the fetcher's contract."""
    lock = _seed_lock(tmp_path)
    _run(lock, _all_metas(tmp_path))
    written = json.loads(lock.read_text(encoding="utf-8"))
    for component, lane in written["components"].items():
        assert lane["baseUrl"].startswith("https://")
        for platform, entry in lane["platforms"].items():
            assert set(entry) == {"filename", "executable", "sha256", "size"}
            # A separator would escape the cache dir / the base URL; the npm
            # client rejects it, so the generator must never produce it.
            assert "/" not in entry["filename"] and "\\" not in entry["filename"]
            assert "/" not in entry["executable"]
            is_win = platform.startswith("win32")
            assert entry["filename"].endswith(".exe") is is_win
            assert entry["executable"].endswith(".exe") is is_win
            assert len(entry["sha256"]) == 64
            assert entry["size"] > 0
