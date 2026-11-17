# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings › Memory shows where documents and memory are embedded, and moves it.

One embedder serves every chat: the PC's profile picks it, and the user may
choose the other device. Whether this PC can run each one comes from the
model server's ``/system-info``, the same reading setup uses. A change reaches
chats already open, since their agents are rebuilt, and is refused while a
reply runs.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.ui import _chat_helpers as chat
from gaia.ui.routers import memory as memory_router
from gaia.ui.routers import providers

GGUF = "user.embeddinggemma-300m-GGUF"
FLM = "embed-gemma-300m-FLM"


def _system_info(npu: bool) -> dict:
    """``/system-info`` of a Windows PC with a Radeon iGPU, with or without an NPU."""
    devices = {"amd_igpu": {"available": True, "vram_gb": 16.0}}
    if npu:
        devices["amd_npu"] = {"available": True, "name": "AMD NPU"}
    return {
        "OS Version": "Windows 11",
        "Physical Memory": "64.0 GB",
        "devices": devices,
        "unavailable_recipes": [] if npu else ["flm"],
    }


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A PC with *profile*; ``state["npu"]`` says whether it has an NPU."""
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
    state = {"npu": True, "evicted": 0}
    monkeypatch.setattr(
        providers, "_read_system_info", lambda: _system_info(state["npu"])
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.LemonadeClient.get_model_info",
        lambda self, model: {"id": model, "downloaded": True},
    )

    def evict():
        state["evicted"] += 1
        return 0

    monkeypatch.setattr(chat, "evict_all_session_agents", evict)
    monkeypatch.setattr(chat, "_active_sse_handlers", {})

    def setup(profile: str, **keys):
        config_file.write_text(
            json.dumps({"default_device": profile, **keys}), encoding="utf-8"
        )
        return state

    return setup


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(memory_router.router)
    return TestClient(app, headers={"X-Gaia-UI": "1"})


def test_an_npu_pc_shows_its_npu_embedder(pc, client):
    pc("npu")
    body = client.get("/api/memory/embedder").json()
    assert body["device"] == "npu"
    assert body["chosen"] is False
    assert body["profile_device"] == "npu"
    assert {o["device"]: o["model"] for o in body["options"]} == {
        "gpu": GGUF,
        "npu": FLM,
    }


def test_a_pc_without_an_npu_offers_it_as_unavailable(pc, client):
    state = pc("gpu")
    state["npu"] = False
    options = {
        o["device"]: o for o in client.get("/api/memory/embedder").json()["options"]
    }
    assert options["npu"]["available"] is False
    assert "NPU" in options["npu"]["reason"]
    assert options["gpu"]["available"] is True


def test_a_pc_with_an_npu_offers_both(pc, client):
    pc("gpu")
    options = client.get("/api/memory/embedder").json()["options"]
    assert all(o["available"] is True for o in options)


def test_choosing_the_gpu_on_an_npu_pc_moves_every_chat(pc, client):
    """This process's agents are rebuilt; the sidecar's rebuild on their next turn."""
    from gaia.agents.registry import machine_embedding_model

    state = pc("npu")
    body = client.put("/api/memory/embedder", json={"device": "gpu"}).json()
    assert (body["device"], body["chosen"], body["changed"]) == ("gpu", True, True)
    assert machine_embedding_model() == GGUF
    assert state["evicted"] == 1


def test_choosing_the_device_already_in_use_changes_nothing(pc, client):
    """Pinning the profile's own device keeps every chat's agent and vectors."""
    state = pc("npu")
    body = client.put("/api/memory/embedder", json={"device": "npu"}).json()
    assert (body["device"], body["chosen"], body["changed"]) == ("npu", True, False)
    assert state["evicted"] == 0


def test_following_the_setup_again_clears_the_choice(pc, client):
    pc("npu", embedding_device="gpu")
    body = client.put("/api/memory/embedder", json={"device": None}).json()
    assert (body["device"], body["chosen"]) == ("npu", False)
    assert GaiaConfig.load().embedding_device is None


def test_a_device_this_pc_cant_use_is_refused(pc, client):
    state = pc("gpu")
    state["npu"] = False
    resp = client.put("/api/memory/embedder", json={"device": "npu"})
    assert resp.status_code == 409
    assert "NPU" in resp.json()["detail"]
    assert GaiaConfig.load().embedding_device is None
    assert state["evicted"] == 0


def test_a_change_waits_for_a_running_reply(pc, client, monkeypatch):
    state = pc("npu")
    monkeypatch.setattr(chat, "_active_sse_handlers", {"a-chat": object()})
    resp = client.put("/api/memory/embedder", json={"device": "gpu"})
    assert resp.status_code == 409
    assert GaiaConfig.load().embedding_device is None
    assert state["evicted"] == 0


def test_an_unproven_device_is_refused(pc, client):
    pc("gpu")
    assert client.put("/api/memory/embedder", json={"device": "cpu"}).status_code == 422


def test_the_choice_survives_the_apps_own_setup(pc):
    """Setup rewrites the profile, never the user's embedder choice."""
    from gaia.agents.registry import machine_embedding_model
    from gaia.installer.init_command import InitCommand

    pc("npu", embedding_device="gpu")
    init = InitCommand.__new__(InitCommand)
    init.profile = "npu"
    init._record_chat_choice = lambda config: None
    GaiaConfig.update(init._apply_profile_to)
    assert machine_embedding_model() == GGUF


@pytest.mark.parametrize("value", ["cpu", "vulkan", "both"])
def test_config_refuses_an_unproven_embedding_device(pc, value):
    pc("gpu")
    with pytest.raises(GaiaConfigError, match="embedding_device"):
        GaiaConfig.save_key("embedding_device", value)
