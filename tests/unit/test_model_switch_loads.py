# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What reaches the model server when the user switches models in the Agent UI.

A switch that is saved but never loaded looks fine in every test that mocks the
client: the setting changed, the route answered 200. On the PC the model the
user left is still the one resident. Each test here starts from a real state
(one model loaded, a chat under way) against the installer journeys' stand-in
Lemonade, which keeps one chat model loaded as the real one does and writes
down every request, so a test asserts what was loaded, not what was called.
"""

from __future__ import annotations

import json

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.agents.registry import AgentRegistry
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, NPU_DEFAULT_MODEL_NAME
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.ui import _chat_helpers as chat
from gaia.ui import warmup
from gaia.ui.database import ChatDatabase
from gaia.ui.routers import providers, system
from tests.fixtures.fake_lemonade import FakeLemonade

# The recording server is a socket on this machine; nothing leaves it.
pytestmark = pytest.mark.allow_network

GPU = DEFAULT_MODEL_NAME
NPU = NPU_DEFAULT_MODEL_NAME
EMBEDDER = "user.embeddinggemma-300m-GGUF"


class Ui:
    """The Agent UI backend's model routes, wired to a recording server."""

    def __init__(self, server: FakeLemonade, db: ChatDatabase, client: TestClient):
        self.server, self.db, self.client = server, db, client

    def load(self, model: str, ctx: int | None = None) -> None:
        """Have *model* resident, as an earlier chat turn left it."""
        body = {"model_name": model, **({"ctx_size": ctx} if ctx else {})}
        response = requests.post(f"{self.server.base_url}/load", json=body, timeout=10)
        assert response.status_code == 200, response.text

    def pick(self, model: str) -> None:
        """Pick *model* in the model list, and wait out the load it starts."""
        response = self.client.post("/api/providers/select", json={"model": model})
        assert response.status_code == 200, response.text
        assert warmup._warmer.wait(60), "the pick's load never finished"

    def save_custom_model(self, model: str) -> None:
        """Settings > Advanced > Custom model ID; an empty id clears it."""
        response = self.client.put("/api/settings", json={"custom_model": model})
        assert response.status_code == 200, response.text
        assert warmup._warmer.wait(60), "the saved model's load never finished"

    def resident_chat_models(self) -> list:
        health = requests.get(f"{self.server.base_url}/health", timeout=10).json()
        return [
            m["model_name"] for m in health["all_models_loaded"] if m["type"] == "llm"
        ]


@pytest.fixture
def ui(tmp_path, monkeypatch):
    """A PC with an NPU and both Gemma builds downloaded, chat relayed to the sidecar."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    # The preload is skipped on a small PC; a CI runner must not hide it.
    monkeypatch.setattr("gaia.llm.model_fit.ram_total_gb", lambda: 128.0)

    from gaia.ui.setup_runner import mark_initialized

    mark_initialized()

    # The shipped app: the flagship is a binary sidecar, never built in-process.
    registry = AgentRegistry()
    registry.register_sidecar("gaia", "GAIA", [])

    def must_not_build(*_args, **_kwargs):
        raise AssertionError("the sidecar flagship was built in-process")

    monkeypatch.setattr(registry, "create_agent", must_not_build)
    monkeypatch.setattr(chat, "_agent_registry", registry)

    providers.reset_restore_state()
    LemonadeManager.reset()
    db = ChatDatabase(":memory:")
    monkeypatch.setattr(warmup, "_warmer", None)
    warmup.configure(db)
    app = FastAPI()
    app.include_router(providers.router)
    app.include_router(system.router)
    app.state.db = db
    env = {"FAKE_LEMONADE_MAX_LLM": "1", "FAKE_LEMONADE_NPU": "1"}
    with FakeLemonade(downloaded=(GPU, NPU, EMBEDDER), env=env) as server:
        monkeypatch.setenv("LEMONADE_BASE_URL", server.base_url)
        yield Ui(server, db, TestClient(app, headers={"X-Gaia-UI": "1"}))
        warmup._warmer.wait(60)
    db.close()
    providers.reset_restore_state()
    LemonadeManager.reset()


@pytest.mark.parametrize("left, picked", [(NPU, GPU), (GPU, NPU)])
def test_picking_the_other_device_loads_it(ui, left, picked):
    """Gemma on the NPU, switched to Gemma on the GPU, stayed on the NPU."""
    ui.load(left)
    before = len(ui.server.loads)

    ui.pick(picked)

    assert ui.server.loads[before:] == [picked], (
        f"picking {picked} with {left} loaded sent these loads: "
        f"{ui.server.loads[before:]}"
    )
    assert ui.resident_chat_models() == [picked]
    status = warmup.warm_up_status()
    assert status["state"] == warmup.READY, status
    assert status["model"] == picked
    assert ui.server.downloads == []


def test_picking_the_loaded_model_again_loads_nothing(ui):
    ui.load(GPU)
    ui.pick(GPU)
    before = len(ui.server.loads)

    ui.pick(GPU)

    assert ui.server.loads[before:] == []
    assert ui.resident_chat_models() == [GPU]


def test_a_custom_model_id_saved_in_settings_is_loaded(ui):
    ui.load(NPU)
    ui.save_custom_model(GPU)
    assert ui.resident_chat_models() == [GPU]


def test_clearing_the_custom_model_loads_the_default(ui):
    ui.save_custom_model(NPU)
    assert ui.resident_chat_models() == [NPU]

    ui.save_custom_model("")

    assert ui.resident_chat_models() == [GPU]


def test_saving_the_same_custom_model_again_loads_nothing(ui):
    ui.save_custom_model(GPU)
    before = len(ui.server.requests)
    ui.save_custom_model(GPU)
    assert [r for r in ui.server.requests[before:] if r.path.endswith("/load")] == []


def test_a_model_picked_in_the_tui_is_loaded_when_the_app_restores_it(ui):
    """The app's own setting still names the model it last ran."""
    ui.db.set_setting("custom_model", NPU)
    ui.load(NPU)
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"last_model": GPU, "last_provider": "local"}), encoding="utf-8"
    )

    body = ui.client.get("/api/providers/active").json()

    assert body["model"] == GPU
    assert warmup._warmer.wait(60)
    assert ui.resident_chat_models() == [GPU]


def test_a_pick_made_while_a_reply_streams_loads_when_it_ends(ui, monkeypatch):
    ui.load(NPU)
    monkeypatch.setitem(chat._active_sse_handlers, "a-chat", object())
    ui.pick(GPU)
    assert ui.resident_chat_models() == [NPU], "a running reply keeps its model"

    chat._active_sse_handlers.pop("a-chat")
    warmup.turn_ended()

    assert warmup._warmer.wait(60)
    assert ui.resident_chat_models() == [GPU]


def test_the_status_names_the_picked_model_once_it_is_loaded(ui):
    ui.load(NPU)
    ui.pick(GPU)
    body = ui.client.get("/api/providers/local/status").json()
    assert body["model"] == GPU
    assert body["active_model"] == GPU


def test_a_model_the_sidecar_loaded_keeps_the_window_it_chose(ui):
    """The sidecar sizes its own load; a second opinion here would reload it."""
    ui.load(GPU, ctx=32768)
    before = len(ui.server.loads)

    ui.pick(GPU)

    assert ui.server.loads[before:] == []
    assert warmup.warm_up_status()["state"] == warmup.READY
