# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A tested vision model already on disk reads images before GAIA asks for Gemma.

On a cloud chat with no ``default_model`` the resolver names Gemma. When Gemma
isn't downloaded but Qwen3.6 is, the image tools used to stop at "download
Gemma (about 6 GB)" although a tested image reader was already installed.

No Lemonade server required.
"""

import types
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.vlm_client import VLMClient, VLMExtractionError
from gaia.vlm.mixin import VLMToolsMixin

CLOUD = "fireworks.deepseek-v3p1"
GEMMA = lc.DEFAULT_MODEL_NAME
QWEN = lc.LARGE_DEFAULT_MODEL_NAME
FLASH = lc.FLASH_OPTION_MODEL_NAME
_OK = {"choices": [{"message": {"content": "a planted tree"}}]}


@pytest.fixture
def config(monkeypatch):
    def use(default=GEMMA, last_model=None, vision_model=None):
        monkeypatch.setattr(lc, "resolve_default_chat_model", lambda: default)
        monkeypatch.setattr(
            "gaia.config.GaiaConfig.load",
            classmethod(
                lambda cls: types.SimpleNamespace(
                    vision_model=vision_model, last_model=last_model
                )
            ),
        )

    use()
    return use


def _vlm(model, *catalog, loaded=()):
    with patch("gaia.llm.lemonade_client.LemonadeClient"):
        client = VLMClient(vlm_model=model, base_url="http://127.0.0.1:59993/api/v1")
    client.client = MagicMock()
    client.client.list_models.return_value = {"data": list(catalog)}
    client.client.health_check.return_value = {
        "all_models_loaded": [{"model_name": name} for name in loaded]
    }
    client.client.chat_completions.return_value = _OK
    return client


def _entry(model, downloaded):
    return {"id": model, "downloaded": downloaded, "size": 5.97}


def test_a_cloud_chat_reads_images_with_the_downloaded_qwen(config):
    config(last_model=CLOUD)
    vlm = _vlm(
        lc.resolve_default_vision_model(), _entry(GEMMA, False), _entry(QWEN, True)
    )

    assert vlm.extract_from_image(b"png") == "a planted tree"
    assert vlm.client.chat_completions.call_args.kwargs["model"] == QWEN


def test_with_no_tested_reader_downloaded_it_still_names_gemma(config):
    config(last_model=CLOUD)
    vlm = _vlm(
        lc.resolve_default_vision_model(), _entry(GEMMA, False), _entry(QWEN, False)
    )

    with pytest.raises(VLMExtractionError, match=f"'{GEMMA}' is not downloaded"):
        vlm.extract_from_image(b"png")
    vlm.client.chat_completions.assert_not_called()


def test_on_the_npu_reading_images_stays_off_with_qwen_downloaded(config):
    config(default="gemma4-it-e4b-FLM")
    assert lc.resolve_default_vision_model() is None


def test_a_vision_model_chosen_in_setup_is_never_replaced(config):
    config(last_model=CLOUD, vision_model=GEMMA)
    vlm = _vlm(GEMMA, _entry(GEMMA, False), _entry(QWEN, True))

    with pytest.raises(VLMExtractionError, match=f"'{GEMMA}' is not downloaded"):
        vlm.extract_from_image(b"png")


def test_an_untested_model_is_never_picked(config):
    vlm = _vlm(GEMMA, _entry(GEMMA, False), _entry("Qwen3-VL-4B-Instruct-GGUF", True))

    with pytest.raises(VLMExtractionError):
        vlm.extract_from_image(b"png")


def test_a_model_outside_the_tested_set_is_not_swapped(config):
    vlm = _vlm(
        "Qwen3-VL-4B-Instruct-GGUF",
        {"id": "Qwen3-VL-4B-Instruct-GGUF", "downloaded": False},
        _entry(QWEN, True),
    )

    with pytest.raises(VLMExtractionError):
        vlm.extract_from_image(b"png")


def test_a_loaded_reader_wins_over_another_downloaded_one(config):
    vlm = _vlm(
        GEMMA,
        _entry(GEMMA, False),
        _entry(QWEN, True),
        _entry(FLASH.removeprefix("user."), True),
        loaded=[FLASH.removeprefix("user.")],
    )

    vlm.extract_from_image(b"png")
    assert vlm.client.chat_completions.call_args.kwargs["model"] == FLASH


def test_an_unreadable_load_list_still_uses_a_downloaded_reader(config):
    vlm = _vlm(GEMMA, _entry(GEMMA, False), _entry(QWEN, True), _entry(FLASH, True))
    vlm.client.health_check.side_effect = lc.LemonadeClientError("down")

    vlm.extract_from_image(b"png")
    assert vlm.client.chat_completions.call_args.kwargs["model"] == QWEN


def test_the_tool_reports_the_model_that_read_the_image(config, tmp_path):
    image = tmp_path / "planted.png"
    image.write_bytes(b"png")

    class Host(VLMToolsMixin):
        def __init__(self):
            self.vlm_model = GEMMA
            self.vlm_client = _vlm(GEMMA, _entry(GEMMA, False), _entry(QWEN, True))

    result = Host()._answer_question_about_image(str(image), "What is this?")

    assert result["status"] == "success"
    assert result["model"] == QWEN
