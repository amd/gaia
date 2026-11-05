# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA reads no images while chat runs on the NPU, and says so wherever it comes up.

The NPU model doesn't read images, and loading a second model next to it for
that one job would mean a 6 GB download, so the feature is off instead.
"""

import types

import pytest

from gaia.installer.init_command import InitCommand
from gaia.llm import lemonade_client as lc
from gaia.rag.sdk import RAGSDK, ImageExtractionError
from gaia.ui import setup_plan
from gaia.vlm.mixin import VLMToolsMixin

NPU = "gemma4-it-e4b-FLM"


@pytest.fixture
def chat_model(monkeypatch):
    def use(model, vision_model=None, last_model=None):
        monkeypatch.setattr(lc, "resolve_default_chat_model", lambda: model)
        monkeypatch.setattr(
            "gaia.config.GaiaConfig.load",
            classmethod(
                lambda cls: types.SimpleNamespace(
                    vision_model=vision_model, last_model=last_model
                )
            ),
        )

    return use


def test_no_vision_model_while_chat_runs_on_the_npu(chat_model):
    chat_model(NPU)
    assert lc.resolve_default_vision_model() is None


def test_a_saved_vision_model_does_not_turn_it_back_on(chat_model):
    chat_model(NPU, vision_model=lc.DEFAULT_MODEL_NAME)
    assert lc.resolve_default_vision_model() is None


def test_the_gpu_keeps_its_vision_model(chat_model):
    chat_model(lc.LARGE_DEFAULT_MODEL_NAME)
    assert lc.resolve_default_vision_model() == lc.LARGE_DEFAULT_MODEL_NAME


def test_it_follows_the_model_chat_reopens_on(chat_model):
    chat_model(NPU, last_model=lc.DEFAULT_MODEL_NAME)
    assert lc.resolve_default_vision_model() == lc.DEFAULT_MODEL_NAME
    chat_model(lc.DEFAULT_MODEL_NAME, last_model=NPU)
    assert lc.resolve_default_vision_model() is None


def test_images_are_read_by_the_model_chat_reopens_on(chat_model):
    chat_model(NPU, last_model=lc.LARGE_DEFAULT_MODEL_NAME)
    assert lc.resolve_default_vision_model() == lc.LARGE_DEFAULT_MODEL_NAME


def test_a_cloud_chat_falls_back_to_the_local_default(chat_model):
    chat_model(NPU, last_model="fireworks.deepseek-v4p1-flash")
    assert lc.resolve_default_vision_model() is None


def test_chat_is_told_it_is_off_and_offered_no_download(chat_model, monkeypatch):
    chat_model(NPU)
    monkeypatch.setattr(
        setup_plan, "build_plan", lambda *a, **k: pytest.fail("no plan needed")
    )
    need = setup_plan.model_need("vision")
    assert need == {
        "job": "vision",
        "needed": False,
        "model": None,
        "disk_free_gb": None,
        "off": "npu",
    }


CLOUD = "fireworks.deepseek-v4p1-flash"
IMAGE_TOOLS = pytest.mark.parametrize(
    "call",
    [
        lambda host, path: host._analyze_image(path),
        lambda host, path: host._answer_question_about_image(path, "what is it?"),
    ],
)


@pytest.fixture
def downloaded(monkeypatch):
    """Lemonade lists *models* as downloaded; None makes it unreachable."""

    def use(*models):
        class Client:
            def __init__(self, base_url=None, verbose=True):
                pass

            def list_models(self, show_all=False):
                assert not show_all, "only downloaded models count"
                if models == (None,):
                    raise lc.LemonadeClientError("Request failed: refused")
                return {"data": [{"id": m} for m in models]}

        monkeypatch.setattr(lc, "LemonadeClient", Client)

    return use


def _read(call, tmp_path):
    host = VLMToolsMixin()
    host.vlm_model = None
    host.vlm_client = None
    image = tmp_path / "photo.png"
    image.write_bytes(b"x")
    result = call(host, str(image))
    assert result["status"] == "error"
    return result["error"]


@IMAGE_TOOLS
@pytest.mark.parametrize("last_model", [CLOUD, NPU])
def test_with_no_vision_model_the_tools_say_none_is_installed(
    call, last_model, chat_model, downloaded, tmp_path
):
    chat_model(NPU, last_model=last_model)
    downloaded(NPU, lc.DEFAULT_EMBEDDING_MODEL)
    error = _read(call, tmp_path)
    assert error == lc.VISION_NOT_INSTALLED
    assert "Settings > Model" in error and "Gemma 4 E4B" in error
    assert "GPU" not in error and "NPU" not in error
    assert "`" not in error and "gaia " not in error


@IMAGE_TOOLS
def test_with_a_vision_model_installed_the_npu_is_the_cause(
    call, chat_model, downloaded, tmp_path
):
    chat_model(NPU)
    downloaded(NPU, lc.DEFAULT_MODEL_NAME)
    assert _read(call, tmp_path) == lc.VISION_OFF_ON_NPU


@IMAGE_TOOLS
def test_a_chosen_vision_model_counts_as_installed(
    call, chat_model, downloaded, tmp_path
):
    chat_model(NPU, vision_model="user.My-VL-GGUF")
    downloaded(NPU, "My-VL-GGUF")
    assert _read(call, tmp_path) == lc.VISION_OFF_ON_NPU


@IMAGE_TOOLS
def test_an_unreachable_server_names_no_wrong_cause(
    call, chat_model, downloaded, tmp_path
):
    chat_model(NPU)
    downloaded(None)
    error = _read(call, tmp_path)
    assert "couldn't check which models are installed" in error
    assert "GPU" not in error


def test_indexing_an_image_says_why_it_is_not_read(tmp_path):
    sdk = RAGSDK.__new__(RAGSDK)
    sdk.config = types.SimpleNamespace(vlm_model=None, use_vlm=True)
    image = tmp_path / "scan.png"
    image.write_bytes(b"x")
    with pytest.raises(ImageExtractionError, match="while it runs on the NPU"):
        sdk._extract_text_from_image(str(image))


def test_init_does_not_test_reading_images_on_the_npu(monkeypatch):
    cmd = InitCommand(profile="gaia", yes=True)
    monkeypatch.setattr(cmd, "_chat_model", lambda client: NPU)
    assert cmd._vision_test_model(client=None) is None


def test_an_empty_scan_on_the_npu_names_no_model_to_download():
    sdk = RAGSDK.__new__(RAGSDK)
    sdk.config = types.SimpleNamespace(vlm_model=None)
    remedy = sdk._read_images_remedy("  2. ")
    assert "None" not in remedy
    assert "while it runs on the NPU" in remedy
