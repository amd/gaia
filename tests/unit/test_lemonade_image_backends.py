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
