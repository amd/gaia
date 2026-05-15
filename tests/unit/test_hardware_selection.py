import json
import os
from types import SimpleNamespace

import pytest

from gaia.llm.lemonade_manager import LemonadeManager, HardwareRequirementError
from gaia.llm.lemonade_client import LemonadeClient


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "hardware")


def load_fixture(name: str):
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, "r") as f:
        return json.load(f)


def make_status(running=True, context_size=32768, loaded_models=None):
    if loaded_models is None:
        loaded_models = [{"id": "gemma", "labels": []}]
    return SimpleNamespace(running=running, context_size=context_size, loaded_models=loaded_models)


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
    monkeypatch.setattr(LemonadeClient, "get_system_info", fake_get_system_info, raising=False)


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
