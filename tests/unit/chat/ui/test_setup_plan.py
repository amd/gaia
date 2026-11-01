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

_REAL_LEMONADE = sp._lemonade

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
    with pytest.raises(sp.NeedsAcknowledgement, match="Lemonade v11"):
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


def test_a_model_that_does_not_fit_asks_before_it_is_saved(running, config_file):
    running(SMALL_GPU)
    with pytest.raises(sp.NeedsAcknowledgement, match="may not run") as ask:
        sp.choose_chat_model(LARGE_DEFAULT_MODEL_NAME)
    assert ask.value.detail["acknowledge"] == "acknowledge_no_fit"
    assert not config_file.exists()
    sp.choose_chat_model(LARGE_DEFAULT_MODEL_NAME, acknowledge_no_fit=True)
    assert (
        json.loads(config_file.read_text())["default_model"] == LARGE_DEFAULT_MODEL_NAME
    )


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
    with pytest.raises(sp.NeedsAcknowledgement, match="free memory"):
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


# ── which server the plan asks ─────────────────────────────────────────────


@pytest.fixture
def lemonade_target(monkeypatch):
    """GAIA set to *mode*; GAIA's own runs at *embedded* (None: not running)."""
    from gaia.llm import lemonade_choice, lemonade_client

    def use(mode, base_url=None, embedded=None):
        monkeypatch.setattr(sp, "_lemonade", _REAL_LEMONADE)
        monkeypatch.setattr(
            lemonade_choice,
            "describe",
            lambda: {"mode": mode, "base_url": base_url},
        )
        monkeypatch.setattr(
            lemonade_client, "running_embedded_lemonade_url", lambda: embedded
        )

    return use


def test_gaias_own_not_running_never_asks_the_default_port(
    lemonade_target, monkeypatch
):
    """Another Lemonade on :13305 has its own downloads; GAIA's own has none yet."""
    lemonade_target("gaia")
    monkeypatch.setattr(
        LemonadeClient,
        "health_check",
        lambda self: pytest.fail(f"asked {self.base_url}, not GAIA's own server"),
    )
    plan = sp.build_plan()
    assert plan["lemonade"] == {"mode": "gaia", "url": None, "reachable": False}
    assert plan["chat_model"]["downloaded"] is None
    assert plan["decided_after_server"] is True
    assert plan["download_gb"] and plan["download_gb"] > 5


def test_gaias_own_running_is_the_server_asked(lemonade_target, monkeypatch):
    lemonade_target("gaia", embedded="http://127.0.0.1:51234/api/v1")
    fake = _FakeLemonade(BIG_GPU, downloaded={LARGE_DEFAULT_MODEL_NAME})
    asked = []
    monkeypatch.setattr(sp, "_client", lambda url: asked.append(url) or fake)
    plan = sp.build_plan()
    assert asked == ["http://127.0.0.1:51234/api/v1"]
    assert plan["chat_model"]["downloaded"] is True


def test_use_mine_asks_the_users_server(lemonade_target, monkeypatch):
    lemonade_target("external", base_url="http://localhost:13305/api/v1")
    fake = _FakeLemonade(BIG_GPU, downloaded={LARGE_DEFAULT_MODEL_NAME})
    asked = []
    monkeypatch.setattr(sp, "_client", lambda url: asked.append(url) or fake)
    plan = sp.build_plan()
    assert asked == ["http://localhost:13305/api/v1"]
    assert plan["lemonade"]["url"] == "http://localhost:13305/api/v1"
    assert plan["chat_model"]["downloaded"] is True


def test_switching_to_gaias_own_changes_the_plan(lemonade_target, monkeypatch):
    """The plan the setup screen re-reads after "Use GAIA's own" must differ."""
    fake = _FakeLemonade(BIG_GPU, downloaded={LARGE_DEFAULT_MODEL_NAME})
    monkeypatch.setattr(sp, "_client", lambda url: fake if url else None)
    lemonade_target("external", base_url="http://localhost:13305/api/v1")
    mine = sp.build_plan()
    lemonade_target("gaia")
    own = sp.build_plan()
    assert mine["chat_model"]["downloaded"] is True
    assert own["chat_model"]["downloaded"] is None
    assert own["download_gb"] > mine["download_gb"]


def test_picking_a_model_before_gaias_own_runs_says_to_start_setup(lemonade_target):
    lemonade_target("gaia")
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


def test_chat_model_endpoint_asks_to_confirm_a_model_that_may_not_fit(client, running):
    running(SMALL_GPU)
    body = {"model_id": LARGE_DEFAULT_MODEL_NAME}
    r = client.post("/api/setup/chat-model", json=body, headers=UI)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "no_fit" and detail["capacity_gb"] == 12
    assert "may not run" in detail["message"]
    r = client.post(
        "/api/setup/chat-model",
        json={**body, "acknowledge_no_fit": True},
        headers=UI,
    )
    assert r.status_code == 200
    assert r.json()["chat_model"]["id"] == LARGE_DEFAULT_MODEL_NAME


def test_chat_model_endpoint_refuses_a_model_that_is_not_there(client, running):
    running(BIG_GPU)
    r = client.post("/api/setup/chat-model", json={"model_id": "nope-GGUF"}, headers=UI)
    assert r.status_code == 422


# ── the model catalog, skips, disk (#6226) ─────────────────────────────────

FLM = "gemma4-it-e2b-FLM"


class _LemonadeWithFlm(_FakeLemonade):
    """A Lemonade holding an FLM chat model, a speech model, and a stray."""

    def __init__(self, *args, free_gb=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.free_gb = free_gb
        self.deleted = []

    def get_system_info(self, timeout=None):
        info = super().get_system_info(timeout)
        if self.free_gb is not None:
            info["model_storage"] = {"free_bytes": self.free_gb * 1e9, "path": "D:/hub"}
        return info

    def list_models(self, show_all=False):
        data = super().list_models(show_all)["data"]
        data += [
            {
                "id": FLM,
                "downloaded": FLM in self.downloaded,
                "size": 3.4,
                "recipe": "flm",
                "labels": ["chat"],
            },
            {
                "id": "Whisper-Base",
                "downloaded": True,
                "size": 0.1,
                "recipe": "whispercpp",
                "labels": ["audio"],
            },
            {"id": "Never-Pulled-GGUF", "downloaded": False, "size": 1.0},
        ]
        return {"data": data}

    def delete_model(self, model_name, timeout=None):
        self.deleted.append(model_name)
        self.downloaded.discard(model_name)


@pytest.fixture
def flm_box(monkeypatch):
    def use(capacity=SMALL_GPU, downloaded=(FLM,), free_gb=None):
        fake = _LemonadeWithFlm(capacity, downloaded, free_gb=free_gb)
        monkeypatch.setattr(sp, "_client", lambda url: fake)
        return fake

    return use


def _entry(plan, model_id):
    return next(e for e in plan["catalog"] if e["id"] == model_id)


def test_catalog_offers_the_users_flm_model_but_not_speech_or_strays(flm_box):
    flm_box()
    plan = sp.build_plan()
    ids = [e["id"] for e in plan["catalog"]]
    assert FLM in ids
    assert "Whisper-Base" not in ids and "Never-Pulled-GGUF" not in ids
    flm = _entry(plan, FLM)
    assert flm["chat_choice"] and flm["downloaded"] and flm["size_gb"] == 0.0
    assert flm["recipe"] == "flm" and not flm["planned"]
    assert not _entry(plan, DEFAULT_EMBEDDING_MODEL)["chat_choice"]


def test_catalog_says_what_each_planned_model_is_for_and_costs(flm_box):
    flm_box()
    plan = sp.build_plan()
    gemma = _entry(plan, DEFAULT_MODEL_NAME)
    assert gemma["roles"] == ["chat", "vision"] and gemma["recommended"]
    assert gemma["selected"] and gemma["size_gb"] == pytest.approx(5.6)
    assert "images" in gemma["if_skipped"]
    qwen = _entry(plan, LARGE_DEFAULT_MODEL_NAME)
    assert not qwen["recommended"] and not qwen["selected"]
    assert qwen["fits"] is False and qwen["capacity_gb"] == 12
    assert qwen["need_gb"] > 23 and "12 GB" in qwen["fit_reason"]


def test_big_pc_recommends_the_large_model_and_keeps_gemma_for_vision(running):
    running(BIG_GPU)
    plan = sp.build_plan()
    assert _entry(plan, LARGE_DEFAULT_MODEL_NAME)["recommended"]
    gemma = _entry(plan, DEFAULT_MODEL_NAME)
    assert gemma["roles"] == ["vision"] and gemma["recommended"]


def test_cold_pc_marks_fit_unknown_rather_than_guessing(
    no_server, tmp_path, monkeypatch
):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    plan = sp.build_plan()
    qwen = _entry(plan, LARGE_DEFAULT_MODEL_NAME)
    assert qwen["fits"] is None and qwen["need_gb"] is None
    assert qwen["downloaded"] is None
    assert plan["disk"]["source"] == "local"
    assert plan["disk"]["path"] == str(tmp_path / "hub")


def test_flm_user_picks_their_model_and_gemma_leaves_the_downloads(
    flm_box, config_file
):
    flm_box()
    sp.choose_chat_model(FLM)
    assert json.loads(config_file.read_text())["default_model"] == FLM
    plan = sp.build_plan()
    assert plan["chat_model"] == {
        "id": FLM,
        "user_set": True,
        "size_gb": 0.0,
        "downloaded": True,
    }
    assert [m["id"] for m in plan["models"]] == [DEFAULT_EMBEDDING_MODEL, FLM]
    assert plan["download_gb"] == pytest.approx(0.3)
    gemma = _entry(plan, DEFAULT_MODEL_NAME)
    assert not gemma["selected"] and "images" in gemma["if_skipped"]


def test_a_skipped_model_leaves_the_download_and_says_what_stops(flm_box):
    flm_box()
    sp.set_model_skipped(DEFAULT_MODEL_NAME, True)
    plan = sp.build_plan()
    assert plan["skipped_models"] == [DEFAULT_MODEL_NAME]
    assert DEFAULT_MODEL_NAME not in [m["id"] for m in plan["models"]]
    assert _entry(plan, DEFAULT_MODEL_NAME)["skipped"]
    assert plan["download_gb"] == pytest.approx(0.3)


def test_picking_a_skipped_model_takes_it_off_the_skip_list(flm_box, config_file):
    flm_box(capacity=BIG_GPU)
    sp.set_model_skipped(DEFAULT_MODEL_NAME, True)
    sp.choose_chat_model(DEFAULT_MODEL_NAME)
    assert json.loads(config_file.read_text())["skipped_models"] == []


def test_disk_short_for_the_selection_is_a_warning_to_confirm(flm_box):
    flm_box(downloaded=(), free_gb=2.0)
    plan = sp.build_plan()
    assert plan["disk"] == {"path": "D:/hub", "free_gb": 2.0, "source": "lemonade"}
    assert "Free 3.9 GB" in plan["disk_warning"]
    warning = sp.run_disk_warning()
    assert warning["drop"] == [DEFAULT_MODEL_NAME]
    assert warning["need_gb"] == plan["download_gb"]
    with pytest.raises(sp.NeedsAcknowledgement):
        sp.prepare_download(DEFAULT_MODEL_NAME)


def test_remove_deletes_only_on_request_and_keeps_it_from_coming_back(
    flm_box, config_file
):
    fake = flm_box(downloaded=(FLM, DEFAULT_MODEL_NAME))
    assert sp.remove_model(DEFAULT_MODEL_NAME) == 5.6
    assert fake.deleted == [DEFAULT_MODEL_NAME]
    assert json.loads(config_file.read_text())["skipped_models"] == [DEFAULT_MODEL_NAME]
    with pytest.raises(sp.SetupPlanError, match="not downloaded"):
        sp.remove_model(DEFAULT_MODEL_NAME)
    assert fake.deleted == [DEFAULT_MODEL_NAME]


def test_skip_and_remove_endpoints(client, flm_box):
    flm_box(downloaded=(FLM, DEFAULT_MODEL_NAME))
    r = client.post(
        "/api/setup/models/skip",
        json={"model_id": DEFAULT_EMBEDDING_MODEL},
        headers=UI,
    )
    assert r.status_code == 200
    assert r.json()["skipped_models"] == [DEFAULT_EMBEDDING_MODEL]
    bad = client.post("/api/setup/models/skip", json={"model_id": FLM}, headers=UI)
    assert bad.status_code == 422
    r = client.post(
        "/api/setup/models/remove", json={"model_id": DEFAULT_MODEL_NAME}, headers=UI
    )
    assert r.status_code == 200 and r.json()["freed_gb"] == 5.6


def test_remove_endpoint_refuses_a_model_that_is_downloading(
    client, flm_box, monkeypatch
):
    fake = flm_box(downloaded=(FLM, DEFAULT_MODEL_NAME))
    monkeypatch.setattr(setup_router.runner, "busy_with", lambda m: True)
    r = client.post(
        "/api/setup/models/remove", json={"model_id": DEFAULT_MODEL_NAME}, headers=UI
    )
    assert r.status_code == 409 and fake.deleted == []


def test_download_endpoint_confirms_low_disk_then_pulls_and_unskips(
    client, flm_box, monkeypatch, config_file
):
    fake = flm_box(downloaded=(FLM,), free_gb=1.0)
    sp.set_model_skipped(DEFAULT_MODEL_NAME, True)
    started = []
    monkeypatch.setattr(
        setup_router.runner,
        "download_model",
        lambda c, m, keep: started.append((c, m, keep)) or {"state": "idle"},
    )
    body = {"model_id": DEFAULT_MODEL_NAME, "keep_trying": True}
    r = client.post("/api/setup/models/download", json=body, headers=UI)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "low_disk"
    assert started == []
    r = client.post(
        "/api/setup/models/download",
        json={**body, "acknowledge_low_disk": True},
        headers=UI,
    )
    assert r.status_code == 202
    assert started == [(fake, DEFAULT_MODEL_NAME, True)]
    assert json.loads(config_file.read_text())["skipped_models"] == []


def test_download_endpoint_refuses_a_model_nobody_has(client, flm_box):
    flm_box()
    r = client.post(
        "/api/setup/models/download", json={"model_id": "nope-GGUF"}, headers=UI
    )
    assert r.status_code == 422


def test_run_endpoint_asks_before_downloading_past_the_free_space(
    client, flm_box, monkeypatch
):
    flm_box(downloaded=(), free_gb=1.0)
    seen = []
    monkeypatch.setattr(
        setup_router.runner,
        "start",
        lambda *a, **k: seen.append(k) or {"state": "running"},
    )
    r = client.post("/api/setup/run", json={}, headers=UI)
    assert r.status_code == 409
    assert r.json()["detail"]["acknowledge"] == "acknowledge_low_disk"
    assert seen == []
    r = client.post("/api/setup/run", json={"acknowledge_low_disk": True}, headers=UI)
    assert r.status_code == 200
    assert seen == [
        {
            "allow_low_disk": True,
            "keep_trying": False,
            "extra_models": [],
            "server_only": False,
        }
    ]


def test_run_endpoint_pulls_ticked_extras_and_counts_them_against_the_disk(
    client, flm_box, monkeypatch
):
    flm_box(capacity=BIG_GPU, downloaded=(FLM, DEFAULT_MODEL_NAME), free_gb=10.0)
    seen = []
    monkeypatch.setattr(
        setup_router.runner,
        "start",
        lambda *a, **k: seen.append(k) or {"state": "running"},
    )
    body = {"extra_models": [LARGE_DEFAULT_MODEL_NAME.lower()]}
    r = client.post("/api/setup/run", json=body, headers=UI)
    # Gemma is on disk; the ticked 23 GB model alone overflows 10 GB free.
    assert r.status_code == 409 and r.json()["detail"]["code"] == "low_disk"
    assert LARGE_DEFAULT_MODEL_NAME in r.json()["detail"]["drop"]
    r = client.post(
        "/api/setup/run", json={**body, "acknowledge_low_disk": True}, headers=UI
    )
    assert r.status_code == 200
    # Sent as the catalog names it, so `gaia init` matches it exactly.
    assert seen[0]["extra_models"] == [LARGE_DEFAULT_MODEL_NAME]


def test_run_endpoint_refuses_an_extra_model_nobody_has(client, flm_box, monkeypatch):
    flm_box()
    monkeypatch.setattr(
        setup_router.runner,
        "start",
        lambda *a, **k: pytest.fail("must not start"),
    )
    r = client.post("/api/setup/run", json={"extra_models": ["nope-GGUF"]}, headers=UI)
    assert r.status_code == 422 and "nope-GGUF" in r.json()["detail"]


def test_server_only_run_skips_the_disk_check_and_takes_no_extras(
    client, flm_box, monkeypatch
):
    flm_box(downloaded=(), free_gb=0.1)
    seen = []
    monkeypatch.setattr(
        setup_router.runner,
        "start",
        lambda *a, **k: seen.append(k) or {"state": "running"},
    )
    r = client.post("/api/setup/run", json={"server_only": True}, headers=UI)
    assert r.status_code == 200 and seen[0]["server_only"] is True
    r = client.post(
        "/api/setup/run",
        json={"server_only": True, "extra_models": [DEFAULT_MODEL_NAME]},
        headers=UI,
    )
    assert r.status_code == 422 and len(seen) == 1


def test_catalog_separates_gaias_models_from_ones_only_in_lemonade(flm_box):
    flm_box()
    plan = sp.build_plan()
    assert _entry(plan, FLM)["gaia_model"] is False
    assert _entry(plan, DEFAULT_MODEL_NAME)["gaia_model"] is True
    assert _entry(plan, LARGE_DEFAULT_MODEL_NAME)["gaia_model"] is True
    assert _entry(plan, DEFAULT_EMBEDDING_MODEL)["gaia_model"] is True


def test_plan_counts_a_model_downloaded_beside_the_profile(flm_box):
    flm_box(capacity=BIG_GPU, downloaded=(FLM,))
    sp.choose_chat_model(FLM)
    plan = sp.build_plan(extra_models=[DEFAULT_MODEL_NAME])
    assert DEFAULT_MODEL_NAME in [m["id"] for m in plan["models"]]
    assert plan["download_gb"] == pytest.approx(5.6 + 0.3)


def test_without_a_server_each_action_says_what_it_could_not_do(no_server):
    with pytest.raises(sp.SetupPlanError, match="can't remove Gemma"):
        sp.remove_model(DEFAULT_MODEL_NAME)
    with pytest.raises(sp.SetupPlanError, match="can't download"):
        sp.prepare_download(DEFAULT_MODEL_NAME)
