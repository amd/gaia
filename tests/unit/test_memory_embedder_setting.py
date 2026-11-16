# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings › Memory shows where documents and memory are embedded, and moves it.

One embedder serves every chat: the PC's profile picks it, and the user may
choose the other device. A change reaches chats already open, since their
agents are rebuilt, and is refused while a reply runs.
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


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A PC with *profile*; the model server says what it can run."""
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
    state = {"unavailable": {}, "stopped": [], "evicted": 0}
    monkeypatch.setattr(
        providers,
        "local_unavailable_reason",
        lambda model: state["unavailable"].get(model),
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.LemonadeClient.get_model_info",
        lambda self, model: {"id": model, "downloaded": True},
    )
    monkeypatch.setattr(
        "gaia.ui.email_sidecar.daemon_client.stop_sidecar",
        lambda agent_id: state["stopped"].append(agent_id),
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
    state["unavailable"][FLM] = "this PC has no NPU"
    options = {
        o["device"]: o for o in client.get("/api/memory/embedder").json()["options"]
    }
    assert options["npu"]["available"] is False
    assert options["npu"]["reason"] == "this PC has no NPU"
    assert options["gpu"]["available"] is True


def test_choosing_the_gpu_on_an_npu_pc_moves_every_chat(pc, client):
    """Every agent is rebuilt, the sidecar's too: they picked theirs as they were built."""
    from gaia.agents.registry import machine_embedding_model

    state = pc("npu")
    body = client.put("/api/memory/embedder", json={"device": "gpu"}).json()
    assert (body["device"], body["chosen"]) == ("gpu", True)
    assert machine_embedding_model() == GGUF
    assert state["evicted"] == 1
    assert state["stopped"] == ["gaia"]


def test_following_the_setup_again_clears_the_choice(pc, client):
    pc("npu", embedding_device="gpu")
    body = client.put("/api/memory/embedder", json={"device": None}).json()
    assert (body["device"], body["chosen"]) == ("npu", False)
    assert GaiaConfig.load().embedding_device is None


def test_a_device_this_pc_cant_use_is_refused(pc, client):
    state = pc("gpu")
    state["unavailable"][FLM] = "this PC has no NPU"
    resp = client.put("/api/memory/embedder", json={"device": "npu"})
    assert resp.status_code == 409
    assert "no NPU" in resp.json()["detail"]
    assert GaiaConfig.load().embedding_device is None
    assert state["stopped"] == []


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
