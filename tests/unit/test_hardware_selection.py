import json
import os
from types import SimpleNamespace

import pytest

from gaia.agents.base.agent import HardwareRequirement
from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.lemonade_manager import HardwareRequirementError, LemonadeManager

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "hardware")


def load_fixture(name: str):
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, "r") as f:
        return json.load(f)


def make_status(running=True, context_size=32768, loaded_models=None):
    if loaded_models is None:
        loaded_models = [{"id": "gemma", "labels": []}]
    return SimpleNamespace(
        running=running, context_size=context_size, loaded_models=loaded_models
    )


@pytest.fixture(autouse=True)
def reset_manager():
    # Ensure LemonadeManager singleton state does not leak across tests
    LemonadeManager.reset()
    yield
    LemonadeManager.reset()


def patch_lemonade(monkeypatch, fixture_file: str):
    data = load_fixture(fixture_file)

    def fake_get_status(self):
        return make_status()

    def fake_get_system_info(self):
        return data

    monkeypatch.setattr(LemonadeClient, "get_status", fake_get_status, raising=False)
    monkeypatch.setattr(
        LemonadeClient, "get_system_info", fake_get_system_info, raising=False
    )


def test_requirement_satisfied_npu(monkeypatch):
    patch_lemonade(monkeypatch, "npu_igpu.json")
    # should not raise for required amd_npu
    assert LemonadeManager.ensure_ready(required_min_device="amd_npu") is True


def test_requirement_satisfied_igpu(monkeypatch):
    patch_lemonade(monkeypatch, "igpu_only.json")
    assert LemonadeManager.ensure_ready(required_min_device="amd_igpu") is True


def test_requirement_not_satisfied_cpu_only(monkeypatch):
    patch_lemonade(monkeypatch, "cpu_only.json")
    with pytest.raises(HardwareRequirementError):
        LemonadeManager.ensure_ready(required_min_device="amd_igpu")


def test_metal_satisfies_gpu_requirement(monkeypatch):
    # Apple Silicon: Lemonade's system_info reports 'metal' (its health payload
    # calls the same device 'gpu') — the generic gpu tier must validate, or the
    # UI's default device='gpu' request fails on every Mac.
    patch_lemonade(monkeypatch, "metal_mac.json")
    assert LemonadeManager.ensure_ready(required_min_device="amd_dgpu") is True


def test_metal_does_not_satisfy_npu_requirement(monkeypatch):
    # The normalization maps metal to the GPU tier only — an NPU floor must
    # still fail loudly on a Mac.
    patch_lemonade(monkeypatch, "metal_mac.json")
    with pytest.raises(HardwareRequirementError):
        LemonadeManager.ensure_ready(required_min_device="amd_npu")


def test_list_shaped_devices(monkeypatch):
    # Use the fixture helper so get_status is patched too
    patch_lemonade(monkeypatch, "hardware_list.json")
    assert LemonadeManager.ensure_ready(required_min_device="amd_igpu") is True


def test_empty_devices_falls_back_to_cpu(monkeypatch):
    # Patch Lemonade to return an empty devices list
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.LemonadeClient.get_status",
        lambda *args, **kwargs: make_status(),
        raising=False,
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.LemonadeClient.get_system_info",
        lambda *args, **kwargs: {"devices": []},
        raising=False,
    )
    with pytest.raises(HardwareRequirementError):
        # requiring an iGPU when no device reported should raise
        LemonadeManager.ensure_ready(required_min_device="amd_igpu")


def test_agent_init_enforces_hardware(monkeypatch):
    # End-to-end: Agent.__init__ should call LemonadeManager.ensure_ready()
    class DummyAgent:
        REQUIRED_HARDWARE = HardwareRequirement(min_device="amd_igpu")

        def __init__(self):
            # reuse LemonadeManager.ensure_ready path
            LemonadeManager.ensure_ready(
                required_min_device=self.REQUIRED_HARDWARE.min_device
            )

    # Patch the client to return CPU-only and a running status
    patch_lemonade(monkeypatch, "cpu_only.json")

    with pytest.raises(HardwareRequirementError):
        DummyAgent()


def test_agent_init_passes_none_when_no_requirement(monkeypatch):
    # Ensure Agent.__init__ calls LemonadeManager.ensure_ready with
    # required_min_device=None when the subclass does not declare REQUIRED_HARDWARE.
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

    # Instantiate with skip_lemonade=False so ensure_ready is invoked
    MinimalAgent(skip_lemonade=False)

    assert "kwargs" in calls
    assert calls["kwargs"].get("required_min_device") is None


def test_ensure_ready_none_min_context_uses_default(monkeypatch):
    # Callers thread config values through verbatim; an unset (None) ctx floor
    # must mean "the default", not TypeError at the >= comparison (#2096 E2E).
    patch_lemonade(monkeypatch, "metal_mac.json")
    assert LemonadeManager.ensure_ready(min_context_size=None) is True
