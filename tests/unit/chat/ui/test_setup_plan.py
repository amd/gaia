# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The first-run plan: which Lemonade, which chat model, and how much to download.

Lemonade is faked; the plan's decisions come from ``gaia init``'s own functions,
which these tests drive with a fake client rather than re-deriving.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.config
from gaia.llm.lemonade_client import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MODEL_NAME,
    LARGE_DEFAULT_MODEL_NAME,
    LemonadeClient,
    LemonadeClientError,
)
from gaia.llm.model_fit import MachineCapacity
from gaia.ui import setup_plan as sp
from gaia.ui.routers import setup as setup_router

UI = {"X-Gaia-UI": "1"}
BIG_GPU = MachineCapacity(memory_gb=96, memory_source="AMD iGPU", disk_free_gb=500)
SMALL_GPU = MachineCapacity(memory_gb=12, memory_source="AMD iGPU", disk_free_gb=500)


@pytest.fixture(autouse=True)
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", path)
    monkeypatch.setattr(
        sp,
        "_lemonade",
        # A closed port, so a probe that escapes a fake can't reach a real server.
        lambda: {"mode": "gaia", "url": "http://127.0.0.1:9/api/v1"},
    )
    return path


class _FakeLemonade:
    """A running Lemonade on a PC with *capacity*; *downloaded* models are on disk."""

    def __init__(self, capacity, downloaded=(), version="11.8.0"):
        self.capacity = capacity
        self.downloaded = set(downloaded)
        self.version = version

    def health_check(self):
        return {"status": "ok", "version": self.version}

    def get_system_info(self, timeout=None):
        return {
            "devices": {
                "amd_igpu": {"available": True, "vram_gb": self.capacity.memory_gb}
            },
            "model_storage": {"free_bytes": self.capacity.disk_free_gb * 1e9},
        }

    def list_models(self, show_all=False):
        return {
            "data": [
                {"id": m, "downloaded": m in self.downloaded, "size": size}
                for m, size in (
                    (LARGE_DEFAULT_MODEL_NAME, 23.3),
                    (DEFAULT_MODEL_NAME, 5.6),
                    (DEFAULT_EMBEDDING_MODEL.removeprefix("user."), 0.3),
                )
            ]
        }


@pytest.fixture
def running(monkeypatch):
    """Point the plan at a fake running server on *capacity*."""

    def use(capacity, downloaded=(), version="11.8.0"):
        fake = _FakeLemonade(capacity, downloaded, version)
        monkeypatch.setattr(sp, "_client", lambda url: fake)
        return fake

    return use


@pytest.fixture
def no_server(monkeypatch):
    """A PC with no model server yet: nothing answers, nothing real is probed."""

    def unreachable(self, timeout=None):
        raise LemonadeClientError("Connection refused")

    monkeypatch.setattr(sp, "_client", lambda url: None)
    monkeypatch.setattr(LemonadeClient, "get_system_info", unreachable)


def _plan_ids(plan):
    return [m["id"] for m in plan["models"]]


def test_cold_pc_names_the_starting_model_and_says_the_pick_comes_later(no_server):
    plan = sp.build_plan()
    assert plan["lemonade"]["reachable"] is False
    assert plan["chat_model"]["id"] == DEFAULT_MODEL_NAME
    assert plan["decided_after_server"] is True
    assert plan["chat_model"]["downloaded"] is None
    assert DEFAULT_EMBEDDING_MODEL in _plan_ids(plan)
    assert plan["download_gb"] and plan["download_gb"] > 5


def test_cold_pc_with_a_cloud_default_still_names_a_local_model(no_server, config_file):
    """A cloud default_model has nothing to download; setup's local model follows the PC."""
    config_file.write_text(json.dumps({"default_model": "fireworks.glm-5p3-flash"}))
    plan = sp.build_plan()
    assert plan["chat_model"]["id"] == DEFAULT_MODEL_NAME
    assert plan["chat_model"]["user_set"] is False


def test_cold_pc_with_a_local_default_names_it_and_skips_the_hardware_pick(
    no_server, config_file
):
    config_file.write_text(json.dumps({"default_model": LARGE_DEFAULT_MODEL_NAME}))
    plan = sp.build_plan()
    assert plan["chat_model"]["id"] == LARGE_DEFAULT_MODEL_NAME
    assert plan["decided_after_server"] is False
    # The user's own model replaces Gemma, exactly as gaia init does.
    assert DEFAULT_MODEL_NAME not in _plan_ids(plan)


def test_use_mine_on_a_big_pc_names_the_large_model_and_its_real_size(running):
    running(BIG_GPU)
    plan = sp.build_plan()
    chat = plan["chat_model"]
    assert chat == {
        "id": LARGE_DEFAULT_MODEL_NAME,
        "user_set": False,
        "size_gb": 23.3,
        "downloaded": False,
    }
    assert plan["decided_after_server"] is False
    assert plan["recommended_model"] == LARGE_DEFAULT_MODEL_NAME
    # The hardware's pick keeps Gemma beside it for images: what gaia init pulls.
    assert set(_plan_ids(plan)) >= {LARGE_DEFAULT_MODEL_NAME, DEFAULT_MODEL_NAME}
    assert plan["download_gb"] == pytest.approx(23.3 + 5.6 + 0.3)
    assert plan["memory_gb"] == 96


def test_an_already_downloaded_model_costs_nothing(running):
    running(BIG_GPU, downloaded={LARGE_DEFAULT_MODEL_NAME, DEFAULT_MODEL_NAME})
    plan = sp.build_plan()
    assert plan["chat_model"]["downloaded"] is True
    assert plan["download_gb"] == pytest.approx(0.3)


def test_options_say_which_models_fit(running):
    running(SMALL_GPU)
    options = {o["id"]: o for o in sp.build_plan()["options"]}
    assert options[LARGE_DEFAULT_MODEL_NAME]["fits"] is False
    assert "memory" in options[LARGE_DEFAULT_MODEL_NAME]["reason"]
    assert options[DEFAULT_MODEL_NAME]["fits"] is True


def test_options_show_the_catalog_size(running):
    running(BIG_GPU)
    options = {o["id"]: o for o in sp.build_plan()["options"]}
    assert options[DEFAULT_MODEL_NAME]["size_gb"] == 5.6


def test_a_model_this_lemonade_is_too_old_for_is_not_offered(running):
    """It fits the PC, but setup would refuse it on this server's version."""
    running(BIG_GPU, version="10.9.1")
    options = {o["id"]: o for o in sp.build_plan()["options"]}
    assert options[LARGE_DEFAULT_MODEL_NAME]["fits"] is False
    assert "Lemonade" in options[LARGE_DEFAULT_MODEL_NAME]["reason"]
    assert options[DEFAULT_MODEL_NAME]["fits"] is True
    with pytest.raises(sp.SetupPlanError, match="won't fit"):
        sp.choose_chat_model(LARGE_DEFAULT_MODEL_NAME)


def test_picking_the_smaller_model_saves_it_and_drops_the_large_download(
    running, config_file
):
    running(BIG_GPU)
    sp.choose_chat_model(DEFAULT_MODEL_NAME)
    assert json.loads(config_file.read_text())["default_model"] == DEFAULT_MODEL_NAME
    plan = sp.build_plan()
    assert plan["chat_model"]["id"] == DEFAULT_MODEL_NAME
    assert plan["chat_model"]["user_set"] is True
    assert LARGE_DEFAULT_MODEL_NAME not in _plan_ids(plan)


def test_picking_the_recommendation_again_hands_the_choice_back_to_setup(
    running, config_file
):
    running(BIG_GPU)
    sp.choose_chat_model(DEFAULT_MODEL_NAME)
    sp.choose_chat_model(LARGE_DEFAULT_MODEL_NAME)
    assert json.loads(config_file.read_text())["default_model"] is None
    assert sp.build_plan()["chat_model"]["user_set"] is False


def test_a_model_that_does_not_fit_is_refused(running):
    running(SMALL_GPU)
    with pytest.raises(sp.SetupPlanError, match="won't fit"):
        sp.choose_chat_model(LARGE_DEFAULT_MODEL_NAME)


@pytest.fixture
def free_gb(monkeypatch):
    """Set the memory free now on the fake server's PC, with *loaded* resident."""
    from gaia.llm import lemonade_client
    from gaia.llm.model_fit import FreeMemory

    def use(fake, gb, loaded=None):
        fake.base_url = "http://127.0.0.1:9/api/v1"
        monkeypatch.setattr(
            lemonade_client,
            "_free_memory_snapshot",
            lambda url: (FreeMemory(gb, gb), dict(loaded or {})),
        )

    return use


def test_a_busy_big_pc_shows_why_the_large_model_does_not_fit_now(running, free_gb):
    free_gb(running(BIG_GPU), 10)
    plan = sp.build_plan()
    assert plan["recommended_model"] == DEFAULT_MODEL_NAME
    large = next(o for o in plan["options"] if o["id"] == LARGE_DEFAULT_MODEL_NAME)
    assert large["fits"] is False and "free memory" in large["reason"]
    with pytest.raises(sp.SetupPlanError, match="free memory"):
        sp.choose_chat_model(LARGE_DEFAULT_MODEL_NAME)


def test_picking_gemma_on_a_busy_pc_survives_a_later_idle_run(
    running, free_gb, config_file
):
    fake = running(BIG_GPU)
    free_gb(fake, 10)
    sp.choose_chat_model(DEFAULT_MODEL_NAME)
    free_gb(fake, 80)
    assert json.loads(config_file.read_text())["default_model"] == DEFAULT_MODEL_NAME
    plan = sp.build_plan()
    assert plan["recommended_model"] == LARGE_DEFAULT_MODEL_NAME
    assert plan["chat_model"]["id"] == DEFAULT_MODEL_NAME
    assert plan["chat_model"]["user_set"] is True


def test_a_loaded_model_is_named_as_what_may_hold_the_memory(running, free_gb):
    free_gb(running(BIG_GPU), 10, loaded={DEFAULT_MODEL_NAME: 65536})
    large = next(
        o for o in sp.build_plan()["options"] if o["id"] == LARGE_DEFAULT_MODEL_NAME
    )
    assert f"{DEFAULT_MODEL_NAME} loaded" in large["reason"]


def test_only_setups_own_models_can_be_picked(running):
    running(BIG_GPU)
    with pytest.raises(sp.SetupPlanError, match="Settings"):
        sp.choose_chat_model("some-other-model")


def test_picking_before_the_server_runs_says_to_start_setup(no_server):
    with pytest.raises(sp.SetupPlanError, match="Start setup"):
        sp.choose_chat_model(DEFAULT_MODEL_NAME)


# ── endpoints ──────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(setup_router.router)
    return TestClient(app)


def test_plan_endpoint(client, running):
    running(BIG_GPU)
    r = client.get("/api/setup/plan", headers=UI)
    assert r.status_code == 200
    assert r.json()["chat_model"]["id"] == LARGE_DEFAULT_MODEL_NAME


def test_plan_endpoint_needs_the_ui_header(client, running):
    running(BIG_GPU)
    assert client.get("/api/setup/plan", headers={"X-Gaia-UI": ""}).status_code == 403


def test_chat_model_endpoint_returns_the_new_plan(client, running):
    running(BIG_GPU)
    r = client.post(
        "/api/setup/chat-model", json={"model_id": DEFAULT_MODEL_NAME}, headers=UI
    )
    assert r.status_code == 200
    assert r.json()["chat_model"]["id"] == DEFAULT_MODEL_NAME


def test_chat_model_endpoint_explains_a_refusal(client, running):
    running(SMALL_GPU)
    r = client.post(
        "/api/setup/chat-model", json={"model_id": LARGE_DEFAULT_MODEL_NAME}, headers=UI
    )
    assert r.status_code == 422
    assert "won't fit" in r.json()["detail"]
