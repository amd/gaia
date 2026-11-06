# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Image creation settings: choices from Lemonade's catalog, the shared
config keys, the settings routes, and the mixin creating images with them."""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import LemonadeClientError
from gaia.sd.settings import check_image_choice, image_model_choices
from gaia.ui.database import ChatDatabase
from gaia.ui.routers import images

#: A slice of Lemonade 2026.44.1's catalog, as /models?show_all=true lists it.
CATALOG = [
    {
        "id": "SDXL-Turbo",
        "recipe": "sd-cpp",
        "downloaded": False,
        "labels": ["image"],
        "size": 6.46,
        "image_defaults": {
            "width": 512,
            "height": 512,
            "steps": 4,
            "cfg_scale": 1.0,
            "soft_pixel_cap": 1048576,
        },
    },
    {
        "id": "Z-Image-Turbo",
        "recipe": "sd-cpp",
        "downloaded": True,
        "labels": ["image"],
        "size": 20.7,
        "image_defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 9,
            "cfg_scale": 1.0,
            "soft_pixel_cap": 2097152,
        },
    },
    {
        "id": "Krea-2-Turbo",
        "recipe": "thenoise",
        "downloaded": False,
        "labels": ["image"],
        "size": 35.0,
        "image_defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 8,
            "soft_pixel_cap": 4194304,
        },
    },
    {
        "id": "RealESRGAN-x4plus",
        "recipe": "sd-cpp",
        "downloaded": True,
        "labels": ["upscaling", "image"],
        "image_defaults": None,
    },
    {
        "id": "Gemma-4-E4B-it-GGUF",
        "recipe": "llamacpp",
        "downloaded": True,
        "labels": ["vision"],
    },
]


def _saved():
    path = config_mod.GAIA_CONFIG_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ── Choices ────────────────────────────────────────────────────────────────


def test_choices_are_downloaded_models_and_legacy_sd_upscalers_excluded():
    choices = {c["id"]: c for c in image_model_choices(CATALOG)}
    # Krea is not downloaded and is no legacy model; an upscaler makes no image.
    assert list(choices) == ["Z-Image-Turbo", "SDXL-Turbo"]
    assert choices["SDXL-Turbo"]["downloads_on_first_use"] is True
    assert choices["Z-Image-Turbo"]["downloads_on_first_use"] is False


def test_sizes_stay_under_each_models_pixel_cap():
    choices = {c["id"]: c for c in image_model_choices(CATALOG)}
    z = choices["Z-Image-Turbo"]
    assert z["default_size"] == "1024x1024" and z["default_steps"] == 9
    assert "1440x1440" in z["sizes"] and "1920x1080" in z["sizes"]
    assert all(
        int(w) * int(h) <= 2097152 for w, h in (s.split("x") for s in z["sizes"])
    )
    # The SD client takes only these three for a legacy model.
    assert choices["SDXL-Turbo"]["sizes"] == ["512x512", "768x768", "1024x1024"]


@pytest.mark.parametrize(
    "model,size,steps,named",
    [
        ("Krea-2-Turbo", None, None, "not downloaded"),
        ("Z-Image-Turbo", "4096x4096", None, "4096x4096"),
        ("Z-Image-Turbo", "big", None, "WIDTHxHEIGHT"),
        ("Z-Image-Turbo", None, 0, "1 to 100"),
    ],
)
def test_a_choice_lemonade_cannot_make_says_why(model, size, steps, named):
    with pytest.raises(ValueError, match=named):
        check_image_choice(image_model_choices(CATALOG), model, size, steps)


# ── gaia config ────────────────────────────────────────────────────────────


def test_gaia_config_set_takes_auto_and_checks_the_shape():
    config = GaiaConfig()
    config.set("image_model", "Z-Image-Turbo")
    config.set("image_size", "1280x720")
    config.set("image_steps", "12")
    assert (config.image_model, config.image_size, config.image_steps) == (
        "Z-Image-Turbo",
        "1280x720",
        12,
    )
    config.set("image_size", "auto")
    config.set("image_steps", "")
    assert (config.image_size, config.image_steps) == (None, None)
    with pytest.raises(GaiaConfigError, match="WIDTHxHEIGHT"):
        config.set("image_size", "huge")
    with pytest.raises(GaiaConfigError, match="1 to 100"):
        config.set("image_steps", "500")


# ── Routes ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture
def catalog(monkeypatch):
    listed = MagicMock(return_value={"data": CATALOG})
    monkeypatch.setattr(lc.LemonadeClient, "list_models", listed)
    monkeypatch.setattr(
        lc, "resolve_default_chat_model", lambda: "Qwen3.6-35B-A3B-GGUF"
    )
    return listed


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(images.router)
    app.state.db = db
    return TestClient(app, headers={"X-Gaia-UI": "1"})


def test_a_fresh_install_creates_images_with_sdxl_turbo_at_its_defaults(
    client, catalog
):
    body = client.get("/api/images/settings").json()
    assert (body["model"], body["size"], body["steps"]) == ("SDXL-Turbo", None, None)
    assert body["model_available"] is True
    assert [m["id"] for m in body["models"]] == ["Z-Image-Turbo", "SDXL-Turbo"]
    assert body["chat_model_creates_images"] is True


def test_settings_save_to_the_config_the_tui_reads(client, catalog):
    reply = client.put(
        "/api/images/settings",
        json={"model": "Z-Image-Turbo", "size": "1280x720", "steps": 12},
    )
    assert reply.status_code == 200, reply.text
    saved = {k: v for k, v in _saved().items() if k.startswith("image_")}
    assert saved == {
        "image_model": "Z-Image-Turbo",
        "image_size": "1280x720",
        "image_steps": 12,
    }


def test_a_new_model_goes_back_to_its_own_size_and_steps(client, catalog):
    client.put(
        "/api/images/settings",
        json={"model": "Z-Image-Turbo", "size": "1440x1440", "steps": 20},
    )
    body = client.put("/api/images/settings", json={"model": "SDXL-Turbo"}).json()
    assert (body["model"], body["size"], body["steps"]) == ("SDXL-Turbo", None, None)


def test_null_size_or_steps_means_automatic(client, catalog):
    client.put(
        "/api/images/settings",
        json={"model": "Z-Image-Turbo", "size": "1440x1440", "steps": 20},
    )
    body = client.put("/api/images/settings", json={"size": None}).json()
    assert (body["model"], body["size"], body["steps"]) == ("Z-Image-Turbo", None, 20)


def test_size_alone_saves_the_default_model_with_it(client, catalog):
    client.put("/api/images/settings", json={"size": "1024x1024"})
    assert _saved()["image_model"] == "SDXL-Turbo"
    assert _saved()["image_size"] == "1024x1024"


@pytest.mark.parametrize(
    "update,named",
    [
        ({"model": "Krea-2-Turbo"}, "not downloaded"),
        ({"model": "Z-Image-Turbo", "size": "4096x4096"}, "4096x4096"),
        ({"steps": 0}, "1 to 100"),
    ],
)
def test_a_setting_lemonade_cannot_make_is_refused_and_nothing_saved(
    client, catalog, update, named
):
    reply = client.put("/api/images/settings", json=update)
    assert reply.status_code == 422
    assert named in reply.json()["detail"]
    assert _saved() == {}


def test_a_model_removed_since_it_was_saved_is_reported(client, catalog):
    GaiaConfig.save_keys({"image_model": "Krea-2-Turbo"})
    assert client.get("/api/images/settings").json()["model_available"] is False


def test_a_compact_chat_model_is_reported_as_unable_to_create_images(
    client, catalog, db
):
    db.set_setting("custom_model", "Gemma-4-E4B-it-GGUF")
    body = client.get("/api/images/settings").json()
    assert body["chat_model"] == "Gemma-4-E4B-it-GGUF"
    assert body["chat_model_creates_images"] is False


def test_an_unreachable_lemonade_is_503_naming_it(client, catalog):
    catalog.side_effect = LemonadeClientError("connection refused")
    reply = client.get("/api/images/settings")
    assert reply.status_code == 503
    assert "connection refused" in reply.json()["detail"]


# ── The mixin uses them ────────────────────────────────────────────────────


def _sd_host(tmp_path, follow):
    from gaia.sd.mixin import SDToolsMixin

    host = SDToolsMixin()
    host.sd_client = MagicMock()
    host.sd_client.list_sd_models.return_value = [{"id": "Z-Image-Turbo"}]
    host.sd_client.generate_image.side_effect = RuntimeError("stop after the request")
    host.sd_output_dir = tmp_path
    host.sd_default_model = "SDXL-Turbo"
    host.sd_default_size = None
    host.sd_default_steps = None
    host.sd_default_cfg = None
    host.sd_generations = []
    host.sd_follow_user_settings = follow
    return host


def test_an_image_without_a_named_model_uses_the_saved_settings(tmp_path):
    GaiaConfig.save_keys(
        {"image_model": "Z-Image-Turbo", "image_size": "1280x720", "image_steps": 12}
    )
    host = _sd_host(tmp_path, follow=True)
    host._generate_image("a lighthouse")
    sent = host.sd_client.generate_image.call_args.kwargs
    assert (sent["model"], sent["size"], sent["steps"]) == (
        "Z-Image-Turbo",
        "1280x720",
        12,
    )


def test_naming_the_saved_model_keeps_the_saved_size_and_steps(tmp_path):
    # The image skill has the agent pass list_sd_models' default by name.
    GaiaConfig.save_keys(
        {"image_model": "Z-Image-Turbo", "image_size": "1280x720", "image_steps": 12}
    )
    host = _sd_host(tmp_path, follow=True)
    host._generate_image("a lighthouse", model="Z-Image-Turbo")
    sent = host.sd_client.generate_image.call_args.kwargs
    assert (sent["model"], sent["size"], sent["steps"]) == (
        "Z-Image-Turbo",
        "1280x720",
        12,
    )
    host._generate_image("a lighthouse", model="Z-Image-Turbo", size="512x512")
    assert host.sd_client.generate_image.call_args.kwargs["size"] == "512x512"


def test_a_named_model_keeps_its_own_defaults(tmp_path):
    GaiaConfig.save_keys(
        {"image_model": "Z-Image-Turbo", "image_size": "1280x720", "image_steps": 12}
    )
    host = _sd_host(tmp_path, follow=True)
    host.sd_client.list_sd_models.return_value = [
        {"id": "Z-Image-Turbo"},
        {"id": "Krea-2-Turbo"},
    ]
    host._generate_image("a lighthouse", model="Krea-2-Turbo")
    sent = host.sd_client.generate_image.call_args.kwargs
    assert (sent["model"], sent["size"], sent["steps"]) == ("Krea-2-Turbo", None, None)


def test_an_sdk_agent_that_does_not_follow_ignores_the_saved_settings(tmp_path):
    GaiaConfig.save_keys({"image_model": "Z-Image-Turbo"})
    host = _sd_host(tmp_path, follow=False)
    host._generate_image("a lighthouse")
    assert host.sd_client.generate_image.call_args.kwargs["model"] == "SDXL-Turbo"


def test_a_saved_model_since_removed_says_where_to_fix_it(tmp_path):
    GaiaConfig.save_keys({"image_model": "Krea-2-Turbo"})
    host = _sd_host(tmp_path, follow=True)
    result = host._generate_image("a lighthouse")
    assert result["status"] == "error"
    assert "Krea-2-Turbo" in result["error"] and "image settings" in result["error"]
    host.sd_client.generate_image.assert_not_called()
