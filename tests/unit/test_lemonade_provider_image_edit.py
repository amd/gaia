# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Provider-level passthroughs for Lemonade image edit and upscale."""

from unittest.mock import MagicMock

from gaia.llm.providers.lemonade import LemonadeProvider


def test_lemonade_provider_forwards_edit_image(monkeypatch, tmp_path):
    backend = MagicMock()
    backend.edit_image.return_value = {"data": []}
    monkeypatch.setattr(
        "gaia.llm.providers.lemonade.LemonadeClient",
        lambda **_kwargs: backend,
    )
    provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    source = tmp_path / "source.png"

    result = provider.edit_image(source, "add a red barn", size="512x512", n=2)

    assert result == {"data": []}
    backend.edit_image.assert_called_once_with(
        source, "add a red barn", size="512x512", n=2
    )


def test_lemonade_provider_forwards_upscale_image(monkeypatch, tmp_path):
    backend = MagicMock()
    backend.upscale_image.return_value = {"data": []}
    monkeypatch.setattr(
        "gaia.llm.providers.lemonade.LemonadeClient",
        lambda **_kwargs: backend,
    )
    provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    source = tmp_path / "source.png"

    result = provider.upscale_image(source, model="RealESRGAN-x4plus")

    assert result == {"data": []}
    backend.upscale_image.assert_called_once_with(source, model="RealESRGAN-x4plus")
