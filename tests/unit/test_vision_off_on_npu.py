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


@pytest.mark.parametrize("cloud", ["fireworks.deepseek-v4p1-flash", "claude-opus-5-5"])
def test_a_cloud_chat_reads_images_though_the_local_default_is_the_npu(
    cloud, chat_model
):
    chat_model(NPU, last_model=cloud)
    assert lc.resolve_default_vision_model() == lc.DEFAULT_MODEL_NAME
    chat_model(NPU, vision_model="Qwen3-VL-4B-Instruct-GGUF", last_model=cloud)
    assert lc.resolve_default_vision_model() == "Qwen3-VL-4B-Instruct-GGUF"


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


def test_a_cloud_chat_with_no_vision_model_is_offered_the_download(
    chat_model, monkeypatch
):
    chat_model(NPU, last_model=CLOUD)
    monkeypatch.setattr(
        setup_plan,
        "build_plan",
        lambda *a, **k: {
            "lemonade": {"reachable": True},
            "disk": {"free_gb": 100.0},
            "catalog": [
                {
                    "id": lc.DEFAULT_MODEL_NAME,
                    "name": "Gemma 4 E4B",
                    "size_gb": 6.0,
                    "source": None,
                    "downloaded": False,
                }
            ],
        },
    )
    need = setup_plan.model_need("vision")
    assert need["needed"] is True
    assert need["model"]["id"] == lc.DEFAULT_MODEL_NAME
    assert "off" not in need


@IMAGE_TOOLS
def test_npu_chat_with_no_vision_model_names_both_causes_and_every_step(
    call, chat_model, downloaded, tmp_path
):
    chat_model(NPU)
    downloaded(NPU, lc.DEFAULT_EMBEDDING_MODEL)
    error = _read(call, tmp_path)
    assert error == lc.VISION_NOT_INSTALLED
    assert error.startswith("No image-reading model is installed on this PC")
    assert "NPU" in error and "Gemma 4 E4B" in error and "Settings > Model" in error
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
    assert "close GAIA and open it again" in error


@pytest.mark.parametrize("installed", [True, False])
def test_indexing_names_the_same_cause_as_the_image_tools(
    installed, chat_model, downloaded, tmp_path
):
    chat_model(NPU)
    downloaded(NPU, *([lc.DEFAULT_MODEL_NAME] if installed else []))
    sdk = RAGSDK.__new__(RAGSDK)
    sdk.config = types.SimpleNamespace(vlm_model=None, use_vlm=True, base_url=None)
    image = tmp_path / "scan.png"
    image.write_bytes(b"x")
    cause = lc.VISION_OFF_ON_NPU if installed else lc.VISION_NOT_INSTALLED
    with pytest.raises(ImageExtractionError) as excinfo:
        sdk._extract_text_from_image(str(image))
    assert cause in str(excinfo.value)
    assert cause in sdk._read_images_remedy("  2. ")


def test_init_does_not_test_reading_images_on_the_npu(monkeypatch):
    cmd = InitCommand(profile="gaia", yes=True)
    monkeypatch.setattr(cmd, "_chat_model", lambda client: NPU)
    assert cmd._vision_test_model(client=None) is None


def test_an_empty_scan_on_the_npu_names_no_model_to_download(chat_model, downloaded):
    chat_model(NPU)
    downloaded(NPU, lc.DEFAULT_MODEL_NAME)
    sdk = RAGSDK.__new__(RAGSDK)
    sdk.config = types.SimpleNamespace(vlm_model=None, base_url=None)
    remedy = sdk._read_images_remedy("  2. ")
    assert "None" not in remedy
    assert "while it runs on the NPU" in remedy
