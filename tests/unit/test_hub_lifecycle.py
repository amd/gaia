# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.hub.lifecycle — configure / health / status (#465).

No live network, no real agent imports: health probes are injected so the tests
assert state transitions (healthy/degraded/error/not_installed) deterministically.
"""

import abc
import hashlib
import json
import os
import stat

import pytest

from gaia.hub import installer, lifecycle
from gaia.hub.lifecycle import (
    HEALTH_DEGRADED,
    HEALTH_ERROR,
    HEALTH_HEALTHY,
    HEALTH_NOT_INSTALLED,
    LifecycleError,
    configure,
    health_check,
    read_config,
    status,
    status_all,
)


def _install_sentinel(tmp_path, agent_id="demo", version="1.0.0"):
    """Write a minimal install sentinel so the agent reads as installed."""
    install_dir = installer.agent_install_dir(agent_id, tmp_path)
    install_dir.mkdir(parents=True, exist_ok=True)
    installer._write_sentinel(  # noqa: SLF001 - direct sentinel for test setup
        agent_id, version, "python", "deadbeef", install_dir
    )


class _FakeRegistration:
    def __init__(self, agent_id, source="installed", agent_class=None):
        self.id = agent_id
        self.source = source
        self.name = agent_id
        self.models = []
        self.agent_class = agent_class


class _FakeRegistry:
    def __init__(self, agents=None):
        self._agents = {a.id: a for a in (agents or [])}

    def get(self, agent_id):
        return self._agents.get(agent_id)

    def list(self):
        return list(self._agents.values())


# ---------------------------------------------------------------------------
# configure: persists + reloads + merges
# ---------------------------------------------------------------------------


def test_configure_persists_and_reloads(tmp_path):
    merged = configure("demo", {"model": "Qwen3.5-35B-A3B-GGUF"}, install_root=tmp_path)
    assert merged == {"model": "Qwen3.5-35B-A3B-GGUF"}

    # Round-trips from disk.
    reloaded = read_config("demo", install_root=tmp_path)
    assert reloaded == {"model": "Qwen3.5-35B-A3B-GGUF"}

    # File actually written under the agent dir.
    path = lifecycle.config_path("demo", tmp_path)
    assert path.exists()
    assert json.loads(path.read_text())["model"] == "Qwen3.5-35B-A3B-GGUF"


def test_configure_merges_by_default(tmp_path):
    configure("demo", {"model": "m1", "temperature": 0.2}, install_root=tmp_path)
    merged = configure("demo", {"model": "m2"}, install_root=tmp_path)
    # model overwritten, temperature preserved.
    assert merged == {"model": "m2", "temperature": 0.2}


def test_configure_replace_drops_existing(tmp_path):
    configure("demo", {"model": "m1", "temperature": 0.2}, install_root=tmp_path)
    merged = configure("demo", {"model": "m2"}, install_root=tmp_path, merge=False)
    assert merged == {"model": "m2"}


def test_configure_rejects_non_dict(tmp_path):
    with pytest.raises(LifecycleError):
        configure("demo", ["not", "a", "dict"], install_root=tmp_path)


def test_read_config_missing_returns_empty(tmp_path):
    assert read_config("nope", install_root=tmp_path) == {}


def test_read_config_corrupt_raises(tmp_path):
    path = lifecycle.config_path("demo", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LifecycleError):
        read_config("demo", install_root=tmp_path)


# ---------------------------------------------------------------------------
# health_check: healthy / degraded / error / not_installed
# ---------------------------------------------------------------------------


def test_health_not_installed(tmp_path):
    result = health_check("ghost", registry=_FakeRegistry(), install_root=tmp_path)
    assert result.state == HEALTH_NOT_INSTALLED
    assert not result.ok


def test_health_healthy(tmp_path):
    _install_sentinel(tmp_path, "demo")
    reg = _FakeRegistry([_FakeRegistration("demo")])
    result = health_check(
        "demo",
        registry=reg,
        install_root=tmp_path,
        loader=lambda aid, registry: [],  # loads cleanly, no warnings
    )
    assert result.state == HEALTH_HEALTHY
    assert result.ok


def test_health_error_when_loader_raises(tmp_path):
    _install_sentinel(tmp_path, "demo")
    reg = _FakeRegistry([_FakeRegistration("demo")])

    def broken_loader(aid, registry):
        raise RuntimeError("entry point blew up")

    result = health_check(
        "demo", registry=reg, install_root=tmp_path, loader=broken_loader
    )
    assert result.state == HEALTH_ERROR
    assert "entry point blew up" in result.detail


def test_health_degraded_on_loader_warning(tmp_path):
    _install_sentinel(tmp_path, "demo")
    reg = _FakeRegistry([_FakeRegistration("demo")])
    result = health_check(
        "demo",
        registry=reg,
        install_root=tmp_path,
        loader=lambda aid, registry: ["optional VLM model missing"],
    )
    assert result.state == HEALTH_DEGRADED
    assert "optional VLM model missing" in result.warnings


def test_health_degraded_on_corrupt_config(tmp_path):
    _install_sentinel(tmp_path, "demo")
    path = lifecycle.config_path("demo", tmp_path)
    path.write_text("{broken", encoding="utf-8")
    reg = _FakeRegistry([_FakeRegistration("demo")])
    result = health_check(
        "demo", registry=reg, install_root=tmp_path, loader=lambda aid, r: []
    )
    assert result.state == HEALTH_DEGRADED
    assert any("corrupt" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# _default_loader: custom_python (file-based) agents (#2268)
#
# File-based custom agents under ~/.gaia/agents/<id>/agent.py have no entry
# point by construction, so the wheel entry-point probe used to report EVERY
# one as `error`. The health signal is the registry's own discovery, hardened
# by a concrete-class check (no unimplemented abstract methods) that does NOT
# construct the agent.
# ---------------------------------------------------------------------------


class _AbstractAgent(abc.ABC):
    """Stand-in for a custom agent that never implements _register_tools."""

    @abc.abstractmethod
    def _register_tools(self):
        raise NotImplementedError


class _ConcreteAgent(_AbstractAgent):
    """Stand-in for a working custom agent."""

    def _register_tools(self):
        return None


def test_default_loader_custom_python_concrete_agent_loads():
    reg = _FakeRegistry(
        [
            _FakeRegistration(
                "jarvis", source="custom_python", agent_class=_ConcreteAgent
            )
        ]
    )
    # A concrete custom agent probes clean — no warnings, no raise.
    assert lifecycle._default_loader("jarvis", reg) == []


def test_default_loader_custom_python_abstract_agent_raises_named_cause():
    reg = _FakeRegistry(
        [_FakeRegistration("zoo", source="custom_python", agent_class=_AbstractAgent)]
    )
    with pytest.raises(LifecycleError) as exc:
        lifecycle._default_loader("zoo", reg)
    # The cause must name the real failure — the unimplemented abstract method.
    assert "_register_tools" in str(exc.value)


def test_default_loader_custom_python_without_class_is_discoverable():
    # Defensive: if the class wasn't captured, discovery alone is the signal.
    reg = _FakeRegistry(
        [_FakeRegistration("nolass", source="custom_python", agent_class=None)]
    )
    assert lifecycle._default_loader("nolass", reg) == []


def test_health_custom_python_working_agent_is_healthy(tmp_path):
    # End-to-end through the real default loader (no injected loader).
    reg = _FakeRegistry(
        [
            _FakeRegistration(
                "jarvis", source="custom_python", agent_class=_ConcreteAgent
            )
        ]
    )
    result = health_check("jarvis", registry=reg, install_root=tmp_path)
    assert result.state == HEALTH_HEALTHY
    assert result.ok


def test_health_custom_python_broken_agent_is_error_with_cause(tmp_path):
    reg = _FakeRegistry(
        [_FakeRegistration("zoo", source="custom_python", agent_class=_AbstractAgent)]
    )
    result = health_check("zoo", registry=reg, install_root=tmp_path)
    assert result.state == HEALTH_ERROR
    assert "_register_tools" in result.detail


def test_default_loader_installed_wheel_path_unregressed():
    # An installed-source agent with no matching entry point still raises the
    # wheel "no entry point" error — the custom_python branch must not swallow
    # or change this path.
    reg = _FakeRegistry([_FakeRegistration("ghostwheel", source="installed")])
    with pytest.raises(LifecycleError) as exc:
        lifecycle._default_loader("ghostwheel", reg)
    assert "entry point" in str(exc.value)


def test_default_loader_builtin_path_unregressed():
    # 'builder' is a reserved builtin id; presence in the registry is proof.
    reg = _FakeRegistry([_FakeRegistration("builder", source="builtin")])
    assert lifecycle._default_loader("builder", reg) == []


def test_health_builtin_without_sentinel_is_checked(tmp_path):
    # A builtin id (e.g. "chat") has no sentinel but must still be health-checked
    # rather than reported not_installed.
    reg = _FakeRegistry([_FakeRegistration("chat", source="builtin")])
    result = health_check(
        "chat", registry=reg, install_root=tmp_path, loader=lambda aid, r: []
    )
    assert result.state == HEALTH_HEALTHY


# ---------------------------------------------------------------------------
# status: aggregates version + health + config + source
# ---------------------------------------------------------------------------


def test_status_aggregates(tmp_path):
    _install_sentinel(tmp_path, "demo", version="2.1.0")
    configure("demo", {"model": "m1"}, install_root=tmp_path)
    reg = _FakeRegistry([_FakeRegistration("demo")])

    st = status("demo", registry=reg, install_root=tmp_path, loader=lambda aid, r: [])
    assert st.installed is True
    assert st.installed_version == "2.1.0"
    assert st.health == HEALTH_HEALTHY
    assert st.config == {"model": "m1"}
    assert st.source == "installed"

    d = st.to_dict()
    assert d["installed_version"] == "2.1.0"
    assert d["health"] == HEALTH_HEALTHY


def test_status_all_covers_installed_and_registered(tmp_path):
    _install_sentinel(tmp_path, "demo")
    reg = _FakeRegistry(
        [_FakeRegistration("demo"), _FakeRegistration("chat", source="builtin")]
    )
    all_status = status_all(
        registry=reg, install_root=tmp_path, loader=lambda aid, r: []
    )
    assert set(all_status) == {"demo", "chat"}
    assert all_status["demo"].installed_version is not None
    assert all_status["chat"].installed_version is None  # builtin, no semver


# ---------------------------------------------------------------------------
# health_check: binary-kind agents (#2084 — platform-selection installer fix)
#
# Binary-kind agents (e.g. email) have no site-packages / entry point to scan;
# health is "does the generic executable file exist" instead. These tests
# drive a real installer.install() binary flow so the on-disk sentinel shape
# matches whatever the fix actually writes, rather than hand-guessing it.
# ---------------------------------------------------------------------------

_BASE = "https://hub.test"


def _binary_manifest_single(
    agent_id="email", version="0.1.0", platform_key="linux-x64", data=b"binary-bytes"
):
    filename = f"{agent_id}-agent-{platform_key}"
    sha = hashlib.sha256(data).hexdigest()
    path = f"agents/{agent_id}/{version}/{filename}"
    artifact = {
        "filename": filename,
        "path": path,
        "size_bytes": len(data),
        "sha256": sha,
        "content_type": "application/octet-stream",
    }
    manifest = {
        "id": agent_id,
        "language": "python",
        "latest_version": version,
        "requirements": {"platforms": []},
        "versions": {
            version: {
                "version": version,
                "artifact": artifact,
                "artifacts": [artifact],
            }
        },
    }
    return manifest, data


def _install_binary_agent(tmp_path, agent_id="email", platform_key="linux-x64"):
    """Install a real binary-kind agent so its sentinel matches the fix's shape."""
    manifest, data = _binary_manifest_single(
        agent_id=agent_id, platform_key=platform_key
    )
    version = manifest["latest_version"]
    artifact_path = manifest["versions"][version]["artifact"]["path"]

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return f"id: {agent_id}\n".encode()
        if url == f"{_BASE}/{artifact_path}":
            return data
        raise AssertionError(url)

    def refuse_pip(args):
        raise AssertionError(f"run_pip must not be called for a binary install: {args}")

    installer.install(
        agent_id,
        manifest=manifest,
        base_url=_BASE,
        fetcher=fetcher,
        run_pip=refuse_pip,
        install_root=tmp_path,
        platform_key=platform_key,
    )
    return installer.agent_install_dir(agent_id, tmp_path) / f"{agent_id}-agent"


def test_health_binary_kind_healthy_without_entrypoint_probe(tmp_path):
    exe_path = _install_binary_agent(
        tmp_path, agent_id="email", platform_key="linux-x64"
    )
    assert exe_path.exists()  # sanity: the install actually wrote the executable
    if os.name == "posix":
        assert exe_path.stat().st_mode & stat.S_IXUSR

    def exploding_loader(agent_id, registry):
        raise AssertionError("entry-point loader must not run for a binary-kind agent")

    result = health_check("email", install_root=tmp_path, loader=exploding_loader)
    assert result.state == HEALTH_HEALTHY


def test_health_binary_kind_missing_executable_is_not_healthy(tmp_path):
    exe_path = _install_binary_agent(
        tmp_path, agent_id="email", platform_key="linux-x64"
    )
    exe_path.unlink()

    def exploding_loader(agent_id, registry):
        raise AssertionError("entry-point loader must not run for a binary-kind agent")

    result = health_check("email", install_root=tmp_path, loader=exploding_loader)
    assert result.state in (HEALTH_DEGRADED, HEALTH_ERROR)
    assert result.detail
