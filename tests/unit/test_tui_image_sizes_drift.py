# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The TUI's embedded image sizes must match the ones Python offers and checks."""

import json
from pathlib import Path

from gaia.config import IMAGE_MAX_STEPS
from gaia.llm.lemonade_client import LemonadeClient
from gaia.sd.settings import CANDIDATE_SIZES
from gaia.ui.routers.images import DEFAULT_IMAGE_MODEL

TUI_IMAGES = Path(__file__).parents[2] / "tui" / "internal" / "images"


def _sizes_doc():
    return json.loads((TUI_IMAGES / "sizes.json").read_text(encoding="utf-8"))


def test_tui_candidate_sizes_match():
    assert _sizes_doc()["candidate_sizes"] == [f"{w}x{h}" for w, h in CANDIDATE_SIZES]


def test_tui_legacy_models_and_sizes_match():
    doc = _sizes_doc()
    assert doc["legacy_models"] == list(LemonadeClient.SD_MODELS)
    assert doc["legacy_sizes"] == list(LemonadeClient.SD_SIZES)


def test_tui_max_steps_and_default_model_match():
    doc = _sizes_doc()
    assert doc["max_steps"] == IMAGE_MAX_STEPS
    assert doc["default_model"] == DEFAULT_IMAGE_MODEL
