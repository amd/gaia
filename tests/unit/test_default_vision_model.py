# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Which model reads scans and images when nobody named one (#6674).

A tested vision model already loaded, else the default chat model when it has
vision, else Gemma.
"""

from types import SimpleNamespace

import pytest

from gaia.config import GaiaConfig
from gaia.llm import lemonade_client as lc

NON_VISION = "Qwen3-0.6B-GGUF"


@pytest.fixture
def config(monkeypatch):
    def use(default, last_model=None, vision_model=None):
        monkeypatch.setattr(lc, "resolve_default_chat_model", lambda: default)
        monkeypatch.setattr(
            "gaia.config.GaiaConfig.load",
            classmethod(
                lambda cls: GaiaConfig(vision_model=vision_model, last_model=last_model)
            ),
        )

    return use


def test_a_default_with_vision_reads_images(config):
    config(lc.LARGE_DEFAULT_MODEL_NAME)
    assert lc.resolve_default_vision_model() == lc.LARGE_DEFAULT_MODEL_NAME


def test_a_default_without_vision_hands_images_to_gemma(config):
    config(NON_VISION)
    assert lc.resolve_default_vision_model() == lc.DEFAULT_MODEL_NAME


def test_a_last_chat_without_vision_keeps_the_default(config):
    config(lc.LARGE_DEFAULT_MODEL_NAME, last_model=NON_VISION)
    assert lc.resolve_default_vision_model() == lc.LARGE_DEFAULT_MODEL_NAME


def test_a_last_chat_with_vision_reads_images(config):
    config(NON_VISION, last_model=lc.LARGE_DEFAULT_MODEL_NAME)
    assert lc.resolve_default_vision_model() == lc.LARGE_DEFAULT_MODEL_NAME


@pytest.mark.parametrize("warm_cache", [False, True])
def test_discovered_cloud_flm_chat_does_not_turn_off_npu_vision(
    config, monkeypatch, warm_cache
):
    """A gateway id ending in -FLM must not be mistaken for an NPU model."""
    from gaia.llm.lemonade_client import record_cloud_models

    cloud_id = "opencode.big-model-FLM"
    catalog = {"data": [{"id": cloud_id, "recipe": "cloud"}]}
    reads = []

    def list_models(self):
        reads.append(True)
        record_cloud_models(catalog)
        return catalog

    monkeypatch.setattr(lc.LemonadeClient, "list_models", list_models)
    record_cloud_models(catalog if warm_cache else {"data": []})
    try:
        config(lc.DEFAULT_MODEL_NAME, last_model=cloud_id)
        assert lc.resolve_default_vision_model() == lc.DEFAULT_MODEL_NAME
        assert len(reads) == (0 if warm_cache else 1)
    finally:
        record_cloud_models({"data": []})


@pytest.mark.parametrize(
    "last_model", ["opencode.big-model", "Qwen3.5-35B-A3B-Unlisted-GGUF"]
)
def test_vision_default_never_reads_the_catalog_when_it_cant_change_the_answer(
    config, monkeypatch, last_model
):
    """``RAGConfig()`` resolves this default, so a stopped Lemonade must not break it."""
    from gaia.llm.lemonade_client import LemonadeClientError, record_cloud_models

    def unavailable(self, **_kwargs):
        raise LemonadeClientError("catalog unavailable")

    monkeypatch.setattr(lc.LemonadeClient, "list_models", unavailable)
    record_cloud_models({"data": []})
    config(lc.DEFAULT_MODEL_NAME, last_model=last_model)
    assert lc.resolve_default_vision_model() == lc.DEFAULT_MODEL_NAME


def test_unclassifiable_flm_chat_fails_loudly_for_vision(config, monkeypatch):
    from gaia.llm.lemonade_client import LemonadeClientError, record_cloud_models

    def unavailable(self, **_kwargs):
        raise LemonadeClientError("catalog unavailable")

    monkeypatch.setattr(lc.LemonadeClient, "list_models", unavailable)
    record_cloud_models({"data": []})
    config(lc.DEFAULT_MODEL_NAME, last_model="opencode.big-model-FLM")
    with pytest.raises(LemonadeClientError, match="opencode.big-model-FLM"):
        lc.resolve_default_vision_model()


def test_registered_dotted_local_chat_does_not_probe_catalog_for_vision(
    config, monkeypatch
):
    config(lc.LARGE_DEFAULT_MODEL_NAME, last_model="user.Llama-3.2-3B-GGUF")
    original_requirement = lc.find_model_requirement
    monkeypatch.setattr(
        lc,
        "find_model_requirement",
        lambda model: (
            SimpleNamespace(feature_tier="full")
            if model == "user.Llama-3.2-3B-GGUF"
            else original_requirement(model)
        ),
    )
    monkeypatch.setattr(
        lc.LemonadeClient,
        "list_models",
        lambda self, **kwargs: pytest.fail("catalog should not be queried"),
    )

    assert lc.resolve_default_vision_model() == lc.LARGE_DEFAULT_MODEL_NAME


def test_a_resident_tested_vision_model_is_used(monkeypatch):
    monkeypatch.setattr(
        lc.LemonadeClient,
        "health_check",
        lambda self: {
            "all_models_loaded": [{"model_name": lc.LARGE_DEFAULT_MODEL_NAME}]
        },
    )
    assert (
        lc.resident_vision_model(lc.DEFAULT_MODEL_NAME, "http://127.0.0.1:59992/api/v1")
        == lc.LARGE_DEFAULT_MODEL_NAME
    )
