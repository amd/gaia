# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TheNoise image models must not inherit hard-coded legacy SD defaults."""

from unittest.mock import MagicMock

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.sd import SDToolsMixin


def _client():
    client = LemonadeClient.__new__(LemonadeClient)
    client.base_url = "http://localhost:13305/api/v1"
    client.log = MagicMock()
    client._send_request = MagicMock(return_value={"data": [{"b64_json": "abc"}]})
    client.list_sd_models = MagicMock(
        return_value=[
            {"id": "Qwen-Image-2.1-TheNoise", "recipe": "thenoise", "labels": ["image"]}
        ]
    )
    return client


def test_thenoise_model_preserves_server_defaults():
    client = _client()
    client.generate_image("a forest", model="Qwen-Image-2.1-TheNoise")
    payload = client._send_request.call_args.kwargs["data"]
    assert payload["model"] == "Qwen-Image-2.1-TheNoise"
    assert "steps" not in payload
    assert "cfg_scale" not in payload
    assert "size" not in payload
    assert "seed" not in payload


def test_thenoise_explicit_overrides_are_forwarded():
    client = _client()
    client.generate_image(
        "a forest", model="Qwen-Image-2.1-TheNoise", size="1024x768", steps=8, seed=42
    )
    payload = client._send_request.call_args.kwargs["data"]
    assert payload["size"] == "1024x768"
    assert payload["steps"] == 8
    assert payload["seed"] == 42
    assert "cfg_scale" not in payload


def test_non_image_model_is_rejected_before_generation():
    client = _client()
    with pytest.raises(
        LemonadeClientError, match="Invalid or not downloaded image model"
    ):
        client.generate_image("hello", model="My-Chat-LLM")
    client._send_request.assert_not_called()


def test_legacy_sd_defaults_still_work():
    client = _client()
    client.generate_image("a forest", model="SDXL-Turbo", seed=123)
    payload = client._send_request.call_args.kwargs["data"]
    assert payload["steps"] == 4
    assert payload["cfg_scale"] == 1.0
    assert payload["size"] == "512x512"


def test_catalog_discovers_thenoise_and_excludes_chat():
    client = _client()
    client.list_models = MagicMock(
        return_value={
            "data": [
                {"id": "TheNoiseModel", "recipe": "thenoise", "labels": ["image"]},
                {"id": "TextModel", "recipe": "llamacpp", "labels": ["chat"]},
            ]
        }
    )
    # Use the real method, rather than the local mock in _client().
    models = LemonadeClient.list_sd_models(client)
    assert [m["id"] for m in models] == ["TheNoiseModel"]


def test_mixin_thenoise_does_not_force_legacy_defaults(tmp_path):
    mixin = SDToolsMixin()
    mixin.sd_client = _client()
    mixin.sd_output_dir = tmp_path
    mixin.sd_default_model = "SDXL-Turbo"
    mixin.sd_default_size = None
    mixin.sd_default_steps = None
    mixin.sd_default_cfg = None
    mixin.sd_generations = []
    fake_png = b"\x89PNG\r\n\x1a\n" + b"x" * 30
    import base64

    mixin.sd_client.generate_image = MagicMock(
        return_value={"data": [{"b64_json": base64.b64encode(fake_png).decode()}]}
    )
    mixin.sd_client.load_model = MagicMock()
    result = mixin._generate_image("a forest", model="Qwen-Image-2.1-TheNoise")
    assert result["status"] == "success"
    assert result["model"] == "Qwen-Image-2.1-TheNoise"
    kwargs = mixin.sd_client.generate_image.call_args.kwargs
    assert kwargs["size"] is None
    assert kwargs["steps"] is None
    assert kwargs["cfg_scale"] is None
    mixin.sd_client.load_model.assert_called_once_with(
        "Qwen-Image-2.1-TheNoise", auto_download=False, prompt=False, timeout=600
    )


def test_image_save_sanitizes_model_name(tmp_path):
    mixin = SDToolsMixin()
    mixin.sd_output_dir = tmp_path
    output = mixin._save_image("art", b"PNG", "repo/model:name")
    assert output.parent == tmp_path
    assert "repo_model_name" in output.name


def _mixin(tmp_path, downloaded):
    import base64

    mixin = SDToolsMixin()
    mixin.sd_client = _client()
    mixin.sd_client.list_sd_models = MagicMock(return_value=downloaded)
    mixin.sd_output_dir = tmp_path
    mixin.sd_default_model = "SDXL-Turbo"
    mixin.sd_default_size = None
    mixin.sd_default_steps = None
    mixin.sd_default_cfg = None
    mixin.sd_generations = []
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 30).decode()
    mixin.sd_client.generate_image = MagicMock(
        return_value={"data": [{"b64_json": png}]}
    )
    mixin.sd_client.load_model = MagicMock()
    return mixin


@pytest.mark.parametrize(
    "downloaded",
    [
        [],
        [{"id": "Qwen-Image-2.1-TheNoise-INT4", "recipe": "thenoise"}],
        [{"id": "qwen-image-2.1-thenoise", "recipe": "thenoise"}],
        [{"recipe": "thenoise"}],
    ],
)
def test_mixin_never_loads_a_thenoise_model_that_is_not_downloaded(
    tmp_path, downloaded
):
    mixin = _mixin(tmp_path, downloaded)
    result = mixin._generate_image("a forest", model="Qwen-Image-2.1-TheNoise")
    assert result["status"] == "error"
    assert "not downloaded" in result["error"]
    mixin.sd_client.load_model.assert_not_called()
    mixin.sd_client.generate_image.assert_not_called()


def test_mixin_default_model_is_unchanged_and_skips_the_catalog(tmp_path):
    mixin = _mixin(tmp_path, [])
    result = mixin._generate_image("a forest", seed=7)
    assert result["status"] == "success"
    mixin.sd_client.list_sd_models.assert_not_called()
    mixin.sd_client.load_model.assert_called_once_with(
        "SDXL-Turbo", auto_download=True, prompt=False, timeout=600
    )
    kwargs = mixin.sd_client.generate_image.call_args.kwargs
    assert (kwargs["model"], kwargs["size"], kwargs["steps"], kwargs["cfg_scale"]) == (
        "SDXL-Turbo",
        "512x512",
        4,
        1.0,
    )
    assert kwargs["timeout"] == 300


@pytest.mark.parametrize("size", ["0x512", "512", "512x512x2", "abcxdef", "123456x1"])
def test_mixin_rejects_malformed_thenoise_size(tmp_path, size):
    mixin = _mixin(tmp_path, [{"id": "Qwen-Image-2.1-TheNoise", "recipe": "thenoise"}])
    result = mixin._generate_image("a", model="Qwen-Image-2.1-TheNoise", size=size)
    assert result["status"] == "error"
    mixin.sd_client.generate_image.assert_not_called()


def test_console_never_shows_none_for_server_chosen_settings(tmp_path):
    mixin = _mixin(tmp_path, [{"id": "Qwen-Image-2.1-TheNoise", "recipe": "thenoise"}])
    mixin.console = MagicMock()
    mixin._generate_image("a forest", model="Qwen-Image-2.1-TheNoise")
    shown = [
        str(c)
        for m in (mixin.console.start_progress, mixin.console.print_image)
        for c in m.call_args_list
    ]
    assert shown
    assert not any("None" in text for text in shown), shown


def test_thenoise_load_request_is_model_name_only(monkeypatch):
    """A fresh TheNoise load must not carry llama.cpp-only fields Lemonade rejects."""
    import gaia.llm.lemonade_client as lc

    client = LemonadeClient.__new__(LemonadeClient)
    client.base_url = "http://localhost:13305/api/v1"
    client.log = MagicMock()
    client._model_recipe = MagicMock(return_value="thenoise")
    client._check_free_memory = MagicMock()
    client._post_load_with_transient_retry = MagicMock(return_value={"status": "ok"})
    monkeypatch.setattr(lc, "preferred_llamacpp_backend", lambda: "vulkan")
    client._load_model_leased(
        "Qwen-Image-2.1-TheNoise", auto_download=True, prompt=False, timeout=600
    )
    body = client._post_load_with_transient_retry.call_args.args[1]
    assert body == {"model_name": "Qwen-Image-2.1-TheNoise"}
