# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for end-to-end multi-device wiring (v0.20.0 blockers B1/B2/H1).

Covers:
  * MEDIUM — ``gpu`` selector accepts integrated OR discrete Radeon.
  * B2 — the runtime device selection is validated and fails loudly with an
    actionable error; ``device`` is threaded base Agent → ensure_ready.
  * B1 — the UI/CLI device → model resolution (DeviceConfig lookup).
"""

import json
import os
from types import SimpleNamespace

import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "hardware")


def _load_fixture(name: str):
    with open(os.path.join(FIXTURES_DIR, name), "r") as f:
        return json.load(f)


def _make_status(running=True, context_size=32768, loaded_models=None):
    if loaded_models is None:
        loaded_models = [{"id": "gemma", "labels": []}]
    return SimpleNamespace(
        running=running, context_size=context_size, loaded_models=loaded_models
    )


@pytest.fixture(autouse=True)
def _reset_manager():
    from gaia.llm.lemonade_manager import LemonadeManager

    LemonadeManager.reset()
    yield
    LemonadeManager.reset()


def _patch_lemonade(monkeypatch, fixture_file: str):
    from gaia.llm.lemonade_client import LemonadeClient

    data = _load_fixture(fixture_file)
    monkeypatch.setattr(
        LemonadeClient, "get_status", lambda self: _make_status(), raising=False
    )
    monkeypatch.setattr(
        LemonadeClient, "get_system_info", lambda self: data, raising=False
    )


# ── MEDIUM: gpu selector accepts igpu OR dgpu ────────────────────────────────


class TestDeviceToMinMapping:
    """``_DEVICE_TO_MIN`` maps the high-level selector to the right tier."""

    def test_mapping_values(self):
        from gaia.llm.lemonade_manager import _DEVICE_TO_MIN

        assert _DEVICE_TO_MIN["cpu"] == "cpu"
        assert _DEVICE_TO_MIN["npu"] == "amd_npu"
        # gpu must map to the LOWEST GPU tier so a dGPU-only box validates.
        assert _DEVICE_TO_MIN["gpu"] == "amd_dgpu"


class TestGpuSelectorAcceptsBothGpuTiers:
    """A 'gpu' request is satisfied by integrated OR discrete Radeon."""

    def test_gpu_satisfied_on_igpu_only(self, monkeypatch):
        from gaia.llm.lemonade_manager import LemonadeManager

        _patch_lemonade(monkeypatch, "igpu_only.json")
        assert LemonadeManager.ensure_ready(device="gpu") is True

    def test_gpu_satisfied_on_dgpu_only(self, monkeypatch):
        from gaia.llm.lemonade_manager import LemonadeManager

        _patch_lemonade(monkeypatch, "dgpu_only.json")
        # Pre-fix this raised because gpu mapped to amd_igpu and a dGPU box
        # reports only amd_dgpu (lower capability tier).
        assert LemonadeManager.ensure_ready(device="gpu") is True

    def test_cpu_always_satisfied(self, monkeypatch):
        from gaia.llm.lemonade_manager import LemonadeManager

        _patch_lemonade(monkeypatch, "cpu_only.json")
        assert LemonadeManager.ensure_ready(device="cpu") is True


# ── B2: unavailable device fails loudly with an actionable error ──────────────


class TestUnavailableDeviceRaises:
    def test_npu_request_on_cpu_only_raises_actionable(self, monkeypatch):
        from gaia.llm.lemonade_manager import (
            HardwareRequirementError,
            LemonadeManager,
        )

        _patch_lemonade(monkeypatch, "cpu_only.json")
        with pytest.raises(HardwareRequirementError) as exc:
            LemonadeManager.ensure_ready(device="npu")
        msg = str(exc.value)
        # Names the device and a concrete remedy (per CLAUDE.md fail-loudly rule).
        assert "npu" in msg.lower()
        assert "gaia init --profile npu" in msg

    def test_gpu_request_on_cpu_only_raises_actionable(self, monkeypatch):
        from gaia.llm.lemonade_manager import (
            HardwareRequirementError,
            LemonadeManager,
        )

        _patch_lemonade(monkeypatch, "cpu_only.json")
        with pytest.raises(HardwareRequirementError) as exc:
            LemonadeManager.ensure_ready(device="gpu")
        msg = str(exc.value)
        assert "gpu" in msg.lower()
        assert "gaia init" in msg


class TestDeviceValidatedAfterWarmInit:
    """B2: validation must run even when the manager is already initialized.

    The ``_initialized`` singleton fast-path used to skip device validation, so
    a UI device switch after the server warmed up on GPU silently bypassed the
    check. Validation now runs on every call (memoised per tier).
    """

    def test_device_switch_validated_when_already_initialized(self, monkeypatch):
        from gaia.llm.lemonade_manager import (
            HardwareRequirementError,
            LemonadeManager,
        )

        _patch_lemonade(monkeypatch, "cpu_only.json")
        # Warm up the singleton with a plain (no-device) call.
        assert LemonadeManager.ensure_ready(min_context_size=4096) is True
        assert LemonadeManager._initialized is True
        # A device switch to NPU on a CPU-only host must STILL raise.
        with pytest.raises(HardwareRequirementError):
            LemonadeManager.ensure_ready(device="npu")

    def test_passing_device_is_memoized(self, monkeypatch):
        from gaia.llm.lemonade_client import LemonadeClient
        from gaia.llm.lemonade_manager import LemonadeManager

        calls = {"n": 0}
        data = _load_fixture("npu_igpu.json")

        def _counting_sysinfo(self):
            calls["n"] += 1
            return data

        monkeypatch.setattr(
            LemonadeClient, "get_status", lambda self: _make_status(), raising=False
        )
        monkeypatch.setattr(
            LemonadeClient, "get_system_info", _counting_sysinfo, raising=False
        )
        assert LemonadeManager.ensure_ready(device="npu") is True
        after_first = calls["n"]
        assert after_first >= 1
        # A second build on the SAME device must not re-probe hardware.
        assert LemonadeManager.ensure_ready(device="npu") is True
        assert calls["n"] == after_first

    def test_unreachable_server_does_not_become_hardware_error(self, monkeypatch):
        from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
        from gaia.llm.lemonade_manager import LemonadeManager

        def _down_status(self):
            return _make_status(running=False)

        def _down_sysinfo(self):
            raise LemonadeClientError("Request failed: connection refused")

        monkeypatch.setattr(LemonadeClient, "get_status", _down_status, raising=False)
        monkeypatch.setattr(
            LemonadeClient, "get_system_info", _down_sysinfo, raising=False
        )
        # Server down + device requested must NOT raise a hardware error — it
        # returns False (not ready), same as the no-device down-server path.
        assert LemonadeManager.ensure_ready(device="npu") is False


class TestFormatDeviceError:
    def test_named_device_includes_remedy(self):
        from gaia.llm.lemonade_manager import _format_device_error

        msg = _format_device_error("npu", "amd_npu", ["cpu"])
        assert "npu" in msg.lower()
        assert "gaia init --profile npu" in msg

    def test_no_device_falls_back_to_raw(self):
        from gaia.llm.lemonade_manager import _format_device_error

        msg = _format_device_error(None, "amd_npu", ["cpu"])
        assert "required=amd_npu" in msg


# ── B2: device threaded base Agent → ensure_ready ────────────────────────────


class TestAgentThreadsDevice:
    def test_device_kwarg_forwarded_to_ensure_ready(self, monkeypatch):
        from gaia.agents.base.agent import Agent

        calls = {}

        def fake_ensure_ready(*args, **kwargs):
            calls["kwargs"] = kwargs
            return True

        monkeypatch.setattr(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            fake_ensure_ready,
            raising=False,
        )

        class MinimalAgent(Agent):
            def _register_tools(self):
                return None

        agent = MinimalAgent(skip_lemonade=False, device="npu")
        assert calls["kwargs"].get("device") == "npu"
        assert agent.device == "npu"

    def test_device_defaults_to_none(self, monkeypatch):
        from gaia.agents.base.agent import Agent

        calls = {}
        monkeypatch.setattr(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            lambda *a, **k: calls.update(kwargs=k) or True,
            raising=False,
        )

        class MinimalAgent(Agent):
            def _register_tools(self):
                return None

        MinimalAgent(skip_lemonade=False)
        assert calls["kwargs"].get("device") is None


# ── B2: ChatAgentConfig carries device + min_context_size ─────────────────────


class TestChatAgentConfigDeviceField:
    def test_config_has_device_and_ctx(self):
        from gaia.agents.chat.agent import ChatAgentConfig

        cfg = ChatAgentConfig(device="npu", min_context_size=4096)
        assert cfg.device == "npu"
        assert cfg.min_context_size == 4096

    def test_config_device_defaults_none(self):
        from gaia.agents.chat.agent import ChatAgentConfig

        cfg = ChatAgentConfig()
        assert cfg.device is None
        assert cfg.min_context_size is None


# ── B1: UI device → model resolution ─────────────────────────────────────────


class TestResolveDeviceModelUI:
    """``resolve_device_model`` is the heart of the UI dropdown fix."""

    def test_npu_resolves_to_flm_model_and_32k_ctx(self):
        from gaia.ui._chat_helpers import resolve_device_model

        model, ctx = resolve_device_model("chat", "npu", None)
        assert model == "gemma4-it-e2b-FLM"
        assert ctx == 32768

    def test_gpu_resolves_to_gguf_model_and_32k_ctx(self):
        from gaia.ui._chat_helpers import resolve_device_model

        model, ctx = resolve_device_model("chat", "gpu", None)
        assert model == "Gemma-4-E4B-it-GGUF"
        assert ctx == 32768

    def test_cpu_resolves_to_gguf_model(self):
        from gaia.ui._chat_helpers import resolve_device_model

        model, _ = resolve_device_model("chat", "cpu", None)
        assert model == "Gemma-4-E4B-it-GGUF"

    def test_no_device_returns_none(self):
        from gaia.ui._chat_helpers import resolve_device_model

        assert resolve_device_model("chat", None, None) == (None, None)

    def test_unknown_device_returns_none(self):
        from gaia.ui._chat_helpers import resolve_device_model

        assert resolve_device_model("chat", "quantum", None) == (None, None)

    def test_registered_agent_device_configs_used(self):
        """A registry entry's own device_configs take precedence over defaults."""
        from gaia.agents.registry import DeviceConfig
        from gaia.ui._chat_helpers import resolve_device_model

        custom = SimpleNamespace(
            device_configs=[
                DeviceConfig(
                    device="npu",
                    model="custom-npu-model",
                    recipe="flm",
                    backend="flm:npu",
                    ctx_size=2048,
                )
            ]
        )
        registry = SimpleNamespace(get=lambda _id: custom)
        model, ctx = resolve_device_model("my-agent", "npu", registry)
        assert model == "custom-npu-model"
        assert ctx == 2048


# ── B1: CLI device → model mapping (shared DEFAULT_DEVICE_CONFIGS source) ─────


class TestCliDeviceModelMapping:
    """The CLI resolves device → model from DEFAULT_DEVICE_CONFIGS."""

    @pytest.mark.parametrize(
        "device,expected_model",
        [
            ("gpu", "Gemma-4-E4B-it-GGUF"),
            ("cpu", "Gemma-4-E4B-it-GGUF"),
            ("npu", "gemma4-it-e2b-FLM"),
        ],
    )
    def test_default_device_config_models(self, device, expected_model):
        from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

        model = next(dc.model for dc in DEFAULT_DEVICE_CONFIGS if dc.device == device)
        assert model == expected_model


# ── B1: sessions router rewrites the model on a device switch (HTTP layer) ────


class TestSessionsRouterDeviceRewrite:
    """PUT /api/sessions/{id} with a device must rewrite the persisted model."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from gaia.ui.server import create_app

        # No `with` — avoids running the lifespan (which would touch Lemonade);
        # the device rewrite path needs only the DB + DEFAULT_DEVICE_CONFIGS.
        return TestClient(create_app(db_path=":memory:"))

    def test_switch_to_npu_rewrites_model(self, client):
        created = client.post(
            "/api/sessions", json={"model": "Gemma-4-E4B-it-GGUF"}
        ).json()
        resp = client.put(f"/api/sessions/{created['id']}", json={"device": "npu"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["device"] == "npu"
        assert body["model"] == "gemma4-it-e2b-FLM"

    def test_switch_to_gpu_keeps_default_model(self, client):
        created = client.post("/api/sessions", json={}).json()
        resp = client.put(f"/api/sessions/{created['id']}", json={"device": "gpu"})
        assert resp.status_code == 200
        assert resp.json()["model"] == "Gemma-4-E4B-it-GGUF"

    def test_title_only_update_leaves_model_untouched(self, client):
        created = client.post(
            "/api/sessions", json={"model": "Gemma-4-E4B-it-GGUF"}
        ).json()
        resp = client.put(f"/api/sessions/{created['id']}", json={"title": "Renamed"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Renamed"
        assert body["model"] == "Gemma-4-E4B-it-GGUF"
