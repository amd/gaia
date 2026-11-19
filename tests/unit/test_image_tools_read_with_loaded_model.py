# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The image tools read with a tested vision model that is already loaded.

Lemonade holds one chat model. A session that chats on a cloud model resolves
its vision model to Gemma, and its image tools used to load Gemma even while
another session's Qwen3.6, which reads images too, was the loaded model. Each
image then unloaded that session's model and its next request reloaded it.

No Lemonade server required.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.vlm_client import VLMClient
from gaia.vlm.mixin import VLMToolsMixin

GEMMA = lc.DEFAULT_MODEL_NAME
QWEN = lc.LARGE_DEFAULT_MODEL_NAME
UNTESTED = "Qwen3-VL-4B-Instruct-GGUF"
BASE_URL = "http://127.0.0.1:59993/api/v1"
_OK = {"choices": [{"message": {"content": "a red square"}}]}


def _backend(*loaded):
    backend = MagicMock()
    backend.base_url = BASE_URL
    backend.list_models.return_value = {
        "data": [{"id": name, "downloaded": True} for name in (GEMMA, QWEN, UNTESTED)]
    }
    backend.health_check.return_value = {
        "all_models_loaded": [{"model_name": name} for name in loaded]
    }
    backend.chat_completions.return_value = _OK
    return backend


@pytest.fixture
def server():
    """Every VLMClient built in the test talks to the backend set here."""
    state = {"backend": _backend()}
    with patch(
        "gaia.llm.lemonade_client.LemonadeClient",
        side_effect=lambda **kwargs: state["backend"],
    ):
        yield state


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "red_square.png"
    path.write_bytes(b"png")
    return str(path)


def _host(model):
    class Host(VLMToolsMixin):
        def __init__(self):
            self.vlm_model = model
            self.vlm_client = VLMClient(vlm_model=model, base_url=BASE_URL)

    return Host()


CALLS = [
    lambda host, image: host._analyze_image(image, "all"),
    lambda host, image: host._answer_question_about_image(image, "What colour?"),
]


@pytest.mark.parametrize("call", CALLS)
def test_a_loaded_tested_model_reads_the_image(server, image, call):
    server["backend"] = _backend("user.embeddinggemma-300m-GGUF", QWEN)

    result = call(_host(GEMMA), image)

    assert result["status"] == "success", result
    assert result["model"] == QWEN
    sent = server["backend"].chat_completions.call_args_list
    assert [request.kwargs["model"] for request in sent] == [QWEN]


@pytest.mark.parametrize("call", CALLS)
def test_the_configured_model_reads_when_no_tested_model_is_loaded(server, image, call):
    server["backend"] = _backend("user.embeddinggemma-300m-GGUF")

    result = call(_host(GEMMA), image)

    assert result["model"] == GEMMA
    assert server["backend"].chat_completions.call_args.kwargs["model"] == GEMMA


def test_the_configured_model_reads_when_it_is_the_loaded_one(server, image):
    server["backend"] = _backend(GEMMA)
    host = _host(GEMMA)

    host._analyze_image(image, "all")

    assert host._image_reader() is host.vlm_client
    assert server["backend"].chat_completions.call_args.kwargs["model"] == GEMMA


def test_a_model_chosen_outside_the_tested_set_is_kept(server, image):
    server["backend"] = _backend(QWEN)

    result = _host(UNTESTED)._analyze_image(image, "all")

    assert result["model"] == UNTESTED
    assert server["backend"].chat_completions.call_args.kwargs["model"] == UNTESTED
    server["backend"].health_check.assert_not_called()


def test_an_unreadable_load_list_keeps_the_configured_model(server, image):
    server["backend"] = _backend(QWEN)
    server["backend"].health_check.side_effect = lc.LemonadeClientError("busy")

    with patch.object(lc.log, "warning") as warned:
        result = _host(GEMMA)._analyze_image(image, "all")

    assert result["model"] == GEMMA
    assert "Could not read the loaded models" in warned.call_args.args[0]


def test_the_switch_is_logged(server, image):
    server["backend"] = _backend(QWEN)

    with patch("gaia.vlm.mixin.logger") as log:
        _host(GEMMA)._analyze_image(image, "all")

    said = log.info.call_args.args
    assert said[0] % said[1:] == (
        f"Reading the image with {QWEN}, which is already loaded, instead of "
        f"{GEMMA}: loading {GEMMA} would unload it."
    )


def test_the_load_list_is_read_from_the_readers_own_server():
    asked = MagicMock()
    asked.health_check.return_value = {"all_models_loaded": [{"model_name": QWEN}]}

    with patch("gaia.llm.lemonade_client.LemonadeClient") as built:
        assert lc.resident_vision_model(GEMMA, client=asked) == QWEN

    built.assert_not_called()
    asked.health_check.assert_called_once_with()
